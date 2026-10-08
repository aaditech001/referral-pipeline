"""Data profiling for ALL source tables.

For every column we report: inferred type, null count, % populated,
distinct count, min, max and max string length. One CSV per table plus a
combined Excel workbook are written to output/profiling/.
"""
import os
import pandas as pd
from common import OUTPUT_DIR, TABLES, load_table


def infer_type(s: pd.Series) -> str:
    """Best-effort type from the string values (all columns load as text)."""
    v = s.dropna()
    if v.empty:
        return "unknown"
    if v.str.lower().isin(["true", "false"]).all():
        return "boolean"
    if pd.to_numeric(v, errors="coerce").notna().all():
        return "integer" if v.str.fullmatch(r"-?\d+").all() else "float"
    if v.str.fullmatch(r"\d{4}-\d{2}-\d{2}").all():
        return "date"
    if v.str.fullmatch(r"\d{4}-\d{2}-\d{2}T[\d:.]+Z").all():
        return "timestamp"
    return "string"


def profile_table(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for col in df.columns:
        s = df[col]
        non_null = s.dropna()
        typ = infer_type(s)
        # numbers compare numerically, everything else lexically
        cmp = pd.to_numeric(non_null) if typ in ("integer", "float") else non_null
        rows.append({
            "column_name": col,
            "data_type": typ,
            "null_count": int(s.isna().sum()),
            "nulls_allowed": "Yes" if s.isna().any() else "No (in this data)",
            "pct_populated": round(100 * non_null.size / len(s), 2) if len(s) else 0,
            "distinct_count": int(non_null.nunique()),
            "min_value": cmp.min() if not cmp.empty else None,
            "max_value": cmp.max() if not cmp.empty else None,
            "max_length": int(non_null.str.len().max()) if not non_null.empty else 0,
            "row_count": len(s),
        })
    return pd.DataFrame(rows)


def main() -> None:
    out = os.path.join(OUTPUT_DIR, "profiling")
    os.makedirs(out, exist_ok=True)
    frames = {}
    for name in TABLES:
        prof = profile_table(load_table(name))
        prof.to_csv(os.path.join(out, f"{name}_profile.csv"), index=False)
        frames[name] = prof
        print(f"profiled {name}: {len(prof)} columns")
    with pd.ExcelWriter(os.path.join(out, "profiling_all_tables.xlsx")) as xw:
        for name, prof in frames.items():
            prof.to_excel(xw, sheet_name=name[:31], index=False)


if __name__ == "__main__":
    main()
