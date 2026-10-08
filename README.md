# Referral Program Pipeline

Profiles the 7 source tables and builds a referral report (46 rows) with an
`is_business_logic_valid` fraud flag.

## Run with Docker
```bash
docker build -t referral-pipeline .
docker run --rm -v "$(pwd)/output:/app/output" referral-pipeline
```
Results appear in `./output/` on your machine:
- `referral_report.csv` – final report
- `profiling/` – per-table profiling CSVs + `profiling_all_tables.xlsx`

## Run locally
```bash
pip install -r requirements.txt
python src/profiling.py
python src/pipeline.py
```
Folders can be changed with env vars `DATA_DIR` and `OUTPUT_DIR`. No credentials are used.

## Layout
- `src/common.py` – CSV loading (text `null` -> real null)
- `src/profiling.py` – null/distinct/min/max/length profile for every column
- `docs/data_dictionary.xlsx` – business-user data dictionary (built by `docs/build_data_dictionary.py`)
- `src/pipeline.py` – clean -> join -> time adjustment -> fraud logic -> report

## Key assumptions (please review)
1. **Duplicates:** `user_logs`, `lead_logs` and `user_referral_logs` are history logs; the latest row per key is kept so joins never multiply rows.
2. **Timezones:** `referral_at`, `updated_at` and `reward_granted_at` use the referrer's `timezone_homeclub`; `transaction_at` uses `timezone_transaction`. If the referrer is unknown, Asia/Jakarta is used.
3. **Leads** are joined only when `referral_source = 'Lead'`.
4. **Reward value** "10 days" is stored as `num_reward_days = 10`.
5. **Nulls:** text -> "Unknown", numbers -> 0. Date columns with no real value (no transaction / no reward granted) stay blank.
6. **Membership check** compares expiry date to the referral date.
7. A transaction ID that is missing from `paid_transactions` is treated as having no transaction details.
8. Anything not matching a *valid* rule is flagged `False`.
