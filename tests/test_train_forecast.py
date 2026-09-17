import os
from datetime import datetime, timedelta
from openpyxl import Workbook

from training.train_forecast import train_and_save, resample_to_hourly, MIN_DATA_POINTS


def _make_excel(rows: list[tuple], path: str):
    wb = Workbook()
    ws = wb.active
    ws.title = "Raw Checks"
    ws.append(["target_name", "checked_at", "status_code", "response_time_ms",
               "is_up", "error_message", "is_anomaly", "anomaly_z_score"])
    for row in rows:
        ws.append(list(row))
    wb.save(path)


def test_train_and_save_skips_when_data_too_small(tmp_path):
    excel_path = str(tmp_path / "small.xlsx")
    base = datetime(2026, 1, 1)
    rows = [
        ("T1", (base + timedelta(hours=i)).isoformat(), 200, 100.0 + i, True, "", False, None)
        for i in range(5)  # jauh di bawah MIN_DATA_POINTS
    ]
    _make_excel(rows, excel_path)

    result = train_and_save(excel_path, target_id=1, output_dir=str(tmp_path / "models"))

    assert result is None
    assert not os.path.exists(str(tmp_path / "models" / "1.pkl"))


def test_train_and_save_creates_model_file_with_enough_data(tmp_path):
    excel_path = str(tmp_path / "enough.xlsx")
    base = datetime(2026, 1, 1)
    rows = [
        ("T1", (base + timedelta(hours=i)).isoformat(), 200, 100.0 + (i % 24), True, "", False, None)
        for i in range(MIN_DATA_POINTS + 10)
    ]
    _make_excel(rows, excel_path)

    result = train_and_save(excel_path, target_id=1, output_dir=str(tmp_path / "models"))

    assert result == str(tmp_path / "models" / "1.pkl")
    assert os.path.exists(result)


def test_resample_to_hourly_returns_series_indexed_by_hour():
    base = datetime(2026, 1, 1, 0, 0)
    rows = [
        {"checked_at": (base + timedelta(minutes=30 * i)).isoformat(), "response_time_ms": 100.0 + i}
        for i in range(6)  # 3 jam data, 2 titik per jam
    ]
    series = resample_to_hourly(rows)
    assert len(series) == 3
    assert series.index[0].hour == 0
