"""Shared helpers: locating files and loading the raw CSVs."""
import os
import pandas as pd

DATA_DIR = os.environ.get("DATA_DIR", "data")
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "output")

# Strings that the source system uses to mean "no value".
NULL_TOKENS = ["", "null", "NULL", "Null", "None", "nan", "NaN"]

# Logical table name -> CSV file name
TABLES = {
    "lead_logs": "lead_log.csv",  # note: file name is singular
    "user_referrals": "user_referrals.csv",
    "user_referral_logs": "user_referral_logs.csv",
    "user_logs": "user_logs.csv",
    "user_referral_statuses": "user_referral_statuses.csv",
    "referral_rewards": "referral_rewards.csv",
    "paid_transactions": "paid_transactions.csv",
}


def load_table(name: str) -> pd.DataFrame:
    """Read one CSV as strings; text nulls ('null', '') become real NaN."""
    path = os.path.join(DATA_DIR, TABLES[name])
    return pd.read_csv(path, dtype=str, na_values=NULL_TOKENS, keep_default_na=False)


def load_all() -> dict:
    return {name: load_table(name) for name in TABLES}
