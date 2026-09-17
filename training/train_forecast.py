"""
Script training offline: baca Excel hasil export (app/excel_export.py),
resample response_time_ms ke per-jam, fit model Holt-Winters
(statsmodels ExponentialSmoothing), simpan ke training/models/{target_id}.pkl.

Dijalankan manual atau lewat scheduler OS (cron/Task Scheduler), TIDAK
berjalan di dalam proses web server -- training bisa makan waktu lebih
lama dari 1x siklus check biasa.

Cara pakai:
    python training/train_forecast.py --excel laporan.xlsx --target-id 1
"""
import argparse
import os
import pandas as pd
import joblib
from openpyxl import load_workbook
from statsmodels.tsa.holtwinters import ExponentialSmoothing

# Minimum titik data (per jam) sebelum training dianggap layak dipercaya.
# Di bawah ini, hasil fit terlalu tidak reliable untuk dipakai forecast.
MIN_DATA_POINTS = 48


def read_raw_checks(excel_path: str) -> list[dict]:
    wb = load_workbook(excel_path, read_only=True)
    ws = wb["Raw Checks"]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    header = rows[0]
    return [dict(zip(header, row)) for row in rows[1:]]


def resample_to_hourly(raw_rows: list[dict]) -> pd.Series:
    """
    raw_rows: list of dict dengan minimal key 'checked_at' (ISO string)
    dan 'response_time_ms' (float, bisa None untuk baris down).
    Return: pandas Series rata-rata response_time_ms per jam, index
    berupa Timestamp per jam, gap diisi lewat interpolasi linear.
    """
    df = pd.DataFrame(raw_rows)
    df["checked_at"] = pd.to_datetime(df["checked_at"])
    df = df.set_index("checked_at").sort_index()
    hourly = df["response_time_ms"].resample("h").mean()
    return hourly.interpolate(method="linear")


def train_and_save(excel_path: str, target_id: int, output_dir: str = "training/models") -> str | None:
    raw_rows = read_raw_checks(excel_path)
    series = resample_to_hourly(raw_rows)
    series = series.dropna()

    if len(series) < MIN_DATA_POINTS:
        print(f"[skip] Target {target_id}: cuma {len(series)} titik data per jam, "
              f"butuh minimal {MIN_DATA_POINTS}. Tidak training.")
        return None

    model = ExponentialSmoothing(
        series, trend="add", seasonal="add", seasonal_periods=24,
    ).fit()

    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"{target_id}.pkl")
    joblib.dump(model, output_path)
    print(f"[ok] Model target {target_id} disimpan ke {output_path} ({len(series)} titik data).")
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Training model forecast dari Excel export Netmonitor.")
    parser.add_argument("--excel", required=True, help="Path ke file .xlsx hasil export")
    parser.add_argument("--target-id", type=int, required=True, help="ID target monitoring")
    parser.add_argument("--output-dir", default="training/models")
    args = parser.parse_args()

    train_and_save(args.excel, args.target_id, args.output_dir)


if __name__ == "__main__":
    main()
