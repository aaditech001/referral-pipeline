"""Referral program pipeline: load -> clean -> process -> fraud logic -> CSV.

Run:  python src/pipeline.py
"""
import os
import numpy as np
import pandas as pd
from common import OUTPUT_DIR, load_all

DEFAULT_TZ = "Asia/Jakarta"   # used only when no timezone can be found (see README)
CLUB_COLUMNS = ["referrer_homeclub", "transaction_location"]  # exempt from Initcap


# ----------------------------------------------------------------- helpers
def initcap(s: pd.Series) -> pd.Series:
    """Spark-style initcap: first letter of each word upper, rest lower."""
    return s.str.lower().str.replace(
        r"(^|\s)(\w)", lambda m: m.group(1) + m.group(2).upper(), regex=True)


def to_local(ts: pd.Series, tz: pd.Series) -> pd.Series:
    """Convert UTC timestamps to local wall-clock time, timezone given per row.

    Returns naive datetimes (local time) so they can be compared directly.
    """
    ts = pd.to_datetime(ts, utc=True, format="mixed")
    out = pd.Series(pd.NaT, index=ts.index, dtype="datetime64[ns]")
    for zone in tz.dropna().unique():
        m = tz == zone
        out[m] = ts[m].dt.tz_convert(zone).dt.tz_localize(None).astype("datetime64[ns]")
    return out


# -------------------------------------------------------------- 1. cleaning
def clean(t: dict) -> dict:
    """Fix types and remove duplicate rows that would multiply joins."""
    for df in t.values():
        for c in df.columns:
            df[c] = df[c].str.strip()

    # user_logs is a change log: identical rows repeat -> keep the latest per user
    ul = t["user_logs"].astype({"id": int}).sort_values("id").drop_duplicates("user_id", keep="last")
    ul["is_deleted"] = ul["is_deleted"].str.lower().eq("true")
    ul["membership_expired_date"] = pd.to_datetime(ul["membership_expired_date"])
    t["user_logs"] = ul

    # lead_logs is a status history -> keep the latest status per lead
    t["lead_logs"] = (t["lead_logs"].astype({"id": int}).sort_values("id")
                      .drop_duplicates("lead_id", keep="last"))

    # referral logs: keep the most recent log per referral
    rl = t["user_referral_logs"]
    rl["is_reward_granted"] = rl["is_reward_granted"].str.lower().eq("true")
    rl["id"] = rl["id"].astype(int)
    t["user_referral_logs"] = rl.sort_values("created_at").drop_duplicates("user_referral_id", keep="last")

    # "10 days" -> 10
    rw = t["referral_rewards"]
    rw["num_reward_days"] = rw["reward_value"].str.extract(r"(\d+)")[0].astype(float)
    t["referral_rewards"] = rw
    return t


# ------------------------------------------------------------ 2. processing
def process(t: dict) -> pd.DataFrame:
    ref = t["user_referrals"]
    users = (t["user_logs"].drop(columns=["id"]).add_prefix("referrer_")
             .rename(columns={"referrer_user_id": "referrer_id"}))

    df = (
        ref
        .merge(t["user_referral_statuses"][["id", "description"]].rename(
            columns={"id": "user_referral_status_id", "description": "referral_status"}),
            on="user_referral_status_id", how="left")
        .merge(t["referral_rewards"][["id", "num_reward_days"]].rename(
            columns={"id": "referral_reward_id"}), on="referral_reward_id", how="left")
        .merge(users, on="referrer_id", how="left")
        .merge(t["paid_transactions"], on="transaction_id", how="left")
        .merge(t["user_referral_logs"].rename(columns={
            "id": "referral_details_id", "user_referral_id": "referral_id",
            "created_at": "log_created_at"})[
            ["referral_details_id", "referral_id", "log_created_at", "is_reward_granted"]],
            on="referral_id", how="left")
    )
    # leads are joined ONLY for referral_source == 'Lead' (see ERD note)
    leads = t["lead_logs"][["lead_id", "source_category"]]
    lead_part = (df.loc[df["referral_source"] == "Lead", ["referral_id", "referee_id"]]
                 .merge(leads, left_on="referee_id", right_on="lead_id", how="left")
                 [["referral_id", "source_category"]])
    df = df.merge(lead_part, on="referral_id", how="left")

    # Time adjustment: UTC -> local time. referral/updated/reward timestamps
    # use the referrer's home-club timezone (they have none of their own).
    ref_tz = df["referrer_timezone_homeclub"].fillna(DEFAULT_TZ)
    df["referral_at"] = to_local(df["referral_at"], ref_tz)
    df["updated_at"] = to_local(df["updated_at"], ref_tz)
    df["reward_granted_at"] = to_local(
        df["log_created_at"].where(df["is_reward_granted"].fillna(False).astype(bool)), ref_tz)
    df["transaction_at"] = to_local(df["transaction_at"], df["timezone_transaction"].fillna(DEFAULT_TZ))

    # Source category (spec's CASE logic)
    df["referral_source_category"] = np.select(
        [df.referral_source == "User Sign Up", df.referral_source == "Draft Transaction",
         df.referral_source == "Lead"],
        ["Online", "Offline", df["source_category"]], default=None)

    # referee_id only means something for leads
    df.loc[df.referral_source != "Lead", "referee_id"] = np.nan
    return df


# ---------------------------------------------------------- 3. fraud logic
def add_business_logic(df: pd.DataFrame) -> pd.DataFrame:
    """Add is_business_logic_valid (bool). Must run BEFORE nulls are filled."""
    has_reward = df["num_reward_days"].fillna(0) > 0
    no_reward = ~has_reward
    status = df["referral_status"]
    berhasil = status == "Berhasil"
    has_tx = df["transaction_id"].notna()
    paid = df["transaction_status"] == "PAID"
    tx_after = df["transaction_at"] > df["referral_at"]
    tx_before = df["transaction_at"] < df["referral_at"]
    same_month = df["transaction_at"].dt.to_period("M") == df["referral_at"].dt.to_period("M")
    member_ok = df["referrer_membership_expired_date"] >= df["referral_at"].dt.normalize()
    not_deleted = df["referrer_is_deleted"] == False  # noqa: E712 (NaN-safe)
    granted = df["is_reward_granted"] == True         # noqa: E712

    valid_1 = (has_reward & berhasil & has_tx & paid & (df["transaction_type"] == "NEW")
               & tx_after & same_month & member_ok & not_deleted & granted)
    valid_2 = status.isin(["Menunggu", "Tidak Berhasil"]) & no_reward

    invalid_1 = has_reward & ~berhasil
    invalid_2 = has_reward & ~has_tx
    invalid_3 = no_reward & has_tx & paid & tx_after
    invalid_4 = berhasil & no_reward
    invalid_5 = has_tx & tx_before

    any_invalid = invalid_1 | invalid_2 | invalid_3 | invalid_4 | invalid_5
    df["is_business_logic_valid"] = ((valid_1 | valid_2) & ~any_invalid).astype(bool)
    df.attrs["rule_hits"] = {k: int(v.sum()) for k, v in dict(
        valid_1=valid_1, valid_2=valid_2, invalid_1=invalid_1, invalid_2=invalid_2,
        invalid_3=invalid_3, invalid_4=invalid_4, invalid_5=invalid_5).items()}
    return df


# ------------------------------------------------------ 4. final shaping
FINAL_COLUMNS = [
    "referral_details_id", "referral_id", "referral_source", "referral_source_category",
    "referral_at", "referrer_id", "referrer_name", "referrer_phone_number", "referrer_homeclub",
    "referee_id", "referee_name", "referee_phone", "referral_status", "num_reward_days",
    "transaction_id", "transaction_status", "transaction_at", "transaction_location",
    "transaction_type", "updated_at", "reward_granted_at", "is_business_logic_valid"]
NO_INITCAP = set(CLUB_COLUMNS) | {"referral_id", "referrer_id", "referee_id", "transaction_id",
                                  "referrer_phone_number", "referee_phone"}


def finalize(df: pd.DataFrame) -> pd.DataFrame:
    out = df.rename(columns={"referrer_name": "referrer_name"})[FINAL_COLUMNS].copy()
    for c in out.select_dtypes(include=["object", "string"]).columns:
        if c not in NO_INITCAP:
            out[c] = initcap(out[c].astype("object"))
    # Null handling: ids/text -> 'Unknown', numbers -> 0.
    out["referral_details_id"] = out["referral_details_id"].fillna(0).astype(int)
    out["num_reward_days"] = out["num_reward_days"].fillna(0).astype(int)
    for c in out.select_dtypes(include=["object", "string"]).columns:
        out[c] = out[c].fillna("Unknown")
    return out


def main() -> None:
    tables = clean(load_all())
    df = add_business_logic(process(tables))
    report = finalize(df)
    assert len(report) == 46 and report.referral_id.is_unique, "row count / duplicate check failed"
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, "referral_report.csv")
    report.to_csv(path, index=False, date_format="%Y-%m-%d %H:%M:%S")
    print(f"wrote {len(report)} rows -> {path}")
    print("valid:", int(report.is_business_logic_valid.sum()),
          "invalid:", int((~report.is_business_logic_valid).sum()))
    print("rule hits:", df.attrs["rule_hits"])


if __name__ == "__main__":
    main()
