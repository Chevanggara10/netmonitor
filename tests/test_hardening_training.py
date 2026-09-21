import os
from datetime import datetime, timedelta

import joblib
import pandas as pd
import pytest
from openpyxl import Workbook

from training import train_forecast
from training.train_forecast import (
    MIN_DATA_POINTS, TrainingDataError, clip_outliers, prepare_training_series,
    read_raw_checks, train_and_save,
)

HEADER = ["target_name", "checked_at", "status_code", "response_time_ms",
          "is_up", "error_message", "is_anomaly", "anomaly_z_score"]


def _make_excel(rows, path, sheet="Raw Checks", header=HEADER):
    wb = Workbook()
    ws = wb.active
    ws.title = sheet
    ws.append(header)
    for row in rows:
        ws.append(list(row))
    wb.save(path)


def _hourly_rows(start, hours, value=lambda i: 100.0 + (i % 24)):
    return [("T", (start + timedelta(hours=i)).isoformat(), 200, value(i), True, "", False, None)
            for i in range(hours)]


def _bundle(path):
    return joblib.load(path)


def test_missing_raw_checks_sheet_raises_clear_error(tmp_path):
    path = str(tmp_path / "x.xlsx")
    _make_excel([], path, sheet="SheetLain")
    with pytest.raises(TrainingDataError, match="Raw Checks"):
        read_raw_checks(path)


def test_missing_required_columns_raises_clear_error(tmp_path):
    path = str(tmp_path / "x.xlsx")
    _make_excel([("a", "b")], path, header=["foo", "bar"])
    with pytest.raises(TrainingDataError, match="checked_at"):
        read_raw_checks(path)


def test_empty_data_is_skipped_not_crashed(tmp_path):
    path = str(tmp_path / "empty.xlsx")
    _make_excel([], path)
    assert train_and_save(path, 1, output_dir=str(tmp_path / "m")) is None


def test_all_down_rows_without_response_time_is_skipped(tmp_path):
    path = str(tmp_path / "down.xlsx")
    rows = [("T", (datetime(2026, 1, 1) + timedelta(hours=i)).isoformat(), None, None, False, "err", False, None)
            for i in range(80)]
    _make_excel(rows, path)
    assert train_and_save(path, 1, output_dir=str(tmp_path / "m")) is None


def test_excel_native_datetime_cells_are_supported(tmp_path):
    path = str(tmp_path / "dt.xlsx")
    base = datetime(2026, 1, 1)
    rows = [("T", base + timedelta(hours=i), 200, 100.0 + (i % 24), True, "", False, None)
            for i in range(MIN_DATA_POINTS + 12)]
    _make_excel(rows, path)
    assert train_and_save(path, 1, output_dir=str(tmp_path / "m")) is not None


def test_long_gap_is_not_interpolated_only_latest_contiguous_segment_is_used():
    base = datetime(2026, 1, 1)
    rows = [{"checked_at": r[1], "response_time_ms": r[3]} for r in _hourly_rows(base, 30)]
    after_gap = base + timedelta(hours=30 + 40)  # celah 40 jam (laptop mati)
    rows += [{"checked_at": r[1], "response_time_ms": r[3]} for r in _hourly_rows(after_gap, 55)]

    series = prepare_training_series(rows)

    assert len(series) == 55
    assert series.index[0] == pd.Timestamp(after_gap)
    assert series.isna().sum() == 0


def test_short_gap_is_interpolated():
    base = datetime(2026, 1, 1)
    rows = [{"checked_at": r[1], "response_time_ms": r[3]} for r in _hourly_rows(base, 20)]
    rows += [{"checked_at": r[1], "response_time_ms": r[3]}
             for r in _hourly_rows(base + timedelta(hours=23), 30)]  # celah 3 jam

    series = prepare_training_series(rows)

    assert len(series) == 53
    assert series.isna().sum() == 0


def test_training_skipped_when_latest_segment_too_short_after_gap(tmp_path):
    path = str(tmp_path / "gap.xlsx")
    base = datetime(2026, 1, 1)
    rows = _hourly_rows(base, 100) + _hourly_rows(base + timedelta(hours=100 + 50), 20)
    _make_excel(rows, path)

    assert train_and_save(path, 1, output_dir=str(tmp_path / "m")) is None


def test_outliers_are_clipped():
    values = [100.0] * 99 + [9_999_999.0]
    clipped = clip_outliers(pd.Series(values))
    assert clipped.max() < 1_000
    assert clipped.min() >= 0


def test_saved_bundle_contains_metadata_and_damped_trend(tmp_path):
    path = str(tmp_path / "ok.xlsx")
    base = datetime(2026, 1, 1)
    _make_excel(_hourly_rows(base, MIN_DATA_POINTS + 24), path)

    out = train_and_save(path, 7, output_dir=str(tmp_path / "m"))

    bundle = _bundle(out)
    assert bundle["version"] == 1
    assert pd.Timestamp(bundle["last_data_at"]) == pd.Timestamp(base + timedelta(hours=MIN_DATA_POINTS + 23))
    assert bundle["n_points"] == MIN_DATA_POINTS + 24
    assert bundle["trained_at"]
    assert bundle["model"].model.damped_trend is True


def test_no_temp_files_left_and_failed_training_keeps_previous_model(tmp_path, monkeypatch):
    path = str(tmp_path / "ok.xlsx")
    out_dir = tmp_path / "m"
    _make_excel(_hourly_rows(datetime(2026, 1, 1), MIN_DATA_POINTS + 24), path)
    out = train_and_save(path, 3, output_dir=str(out_dir))
    before = open(out, "rb").read()
    assert [p.name for p in out_dir.iterdir()] == ["3.pkl"]

    def boom(*a, **k):
        raise RuntimeError("fit exploded")

    monkeypatch.setattr(train_forecast, "_fit_model", boom)
    with pytest.raises(TrainingDataError, match="fit exploded"):
        train_and_save(path, 3, output_dir=str(out_dir))

    assert open(out, "rb").read() == before
    assert [p.name for p in out_dir.iterdir()] == ["3.pkl"]


def test_non_finite_fit_is_rejected_and_not_saved(tmp_path, monkeypatch):
    path = str(tmp_path / "ok.xlsx")
    _make_excel(_hourly_rows(datetime(2026, 1, 1), MIN_DATA_POINTS + 24), path)

    class FakeResults:
        fittedvalues = pd.Series([1.0, float("nan"), 3.0])

    monkeypatch.setattr(train_forecast, "_fit_model", lambda series: FakeResults())
    with pytest.raises(TrainingDataError, match="tidak stabil"):
        train_and_save(path, 4, output_dir=str(tmp_path / "m"))
    assert not os.path.exists(tmp_path / "m" / "4.pkl")


def test_constant_series_does_not_crash(tmp_path):
    path = str(tmp_path / "flat.xlsx")
    _make_excel(_hourly_rows(datetime(2026, 1, 1), MIN_DATA_POINTS + 24, value=lambda i: 250.0), path)
    try:
        result = train_and_save(path, 5, output_dir=str(tmp_path / "m"))
    except TrainingDataError:
        return  # ditolak dengan pesan jelas juga dapat diterima
    assert result is None or os.path.exists(result)
