import os
import time
from datetime import datetime, timedelta

import joblib
import pandas as pd
import pytest
from statsmodels.tsa.holtwinters import ExponentialSmoothing

from app import ml_forecast
from app.ml_forecast import get_forecast, _MODEL_CACHE

NOW = datetime(2026, 9, 21, 10, 30)  # jam terdekat ke bawah: 10:00


class NanModel:
    def forecast(self, steps):
        return pd.Series([float("nan")] * steps)


class NegModel:
    def forecast(self, steps):
        return pd.Series([-50.0] * steps)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    _MODEL_CACHE.clear()
    monkeypatch.setattr(ml_forecast, "MODELS_DIR", str(tmp_path))
    yield
    _MODEL_CACHE.clear()


def _fit(end: datetime, periods=72, base=100.0):
    idx = pd.date_range(end=pd.Timestamp(end).floor("h"), periods=periods, freq="h")
    series = pd.Series([base + (i % 24) for i in range(periods)], index=idx)
    return ExponentialSmoothing(series, trend="add", damped_trend=True, seasonal="add", seasonal_periods=24).fit()


def _save_bundle(tmp_path, target_id, end, base=100.0, **overrides):
    model = _fit(end, base=base)
    bundle = {
        "version": 1, "model": model, "last_data_at": pd.Timestamp(end).floor("h").isoformat(),
        "trained_at": datetime.utcnow().isoformat(), "n_points": 72,
    }
    bundle.update(overrides)
    path = os.path.join(str(tmp_path), f"{target_id}.pkl")
    joblib.dump(bundle, path)
    return path


async def test_forecast_starts_in_the_next_hour_from_now_not_from_training_end(db_session, sample_target, tmp_path):
    _save_bundle(tmp_path, sample_target.id, end=NOW - timedelta(hours=30))

    result = await get_forecast(sample_target.id, db_session, horizon_hours=6, now=NOW)

    assert result["source"] == "trained_model"
    assert len(result["forecast_ms"]) == 6 == len(result["timestamps"])
    assert result["timestamps"][0] == "2026-09-21T11:00:00"
    assert result["timestamps"][-1] == "2026-09-21T16:00:00"
    assert result["trained_until"].startswith("2026-09-20T04:00:00")


async def test_fresh_model_first_timestamp_is_next_hour(db_session, sample_target, tmp_path):
    _save_bundle(tmp_path, sample_target.id, end=NOW)

    result = await get_forecast(sample_target.id, db_session, horizon_hours=3, now=NOW)

    assert result["timestamps"][0] == "2026-09-21T11:00:00"


async def test_retrained_model_is_picked_up_without_restart(db_session, sample_target, tmp_path):
    path = _save_bundle(tmp_path, sample_target.id, end=NOW, base=100.0)
    first = await get_forecast(sample_target.id, db_session, horizon_hours=3, now=NOW)

    time.sleep(0.05)
    _save_bundle(tmp_path, sample_target.id, end=NOW, base=5000.0)
    os.utime(path, (time.time() + 5, time.time() + 5))  # pastikan mtime berbeda
    second = await get_forecast(sample_target.id, db_session, horizon_hours=3, now=NOW)

    assert second["forecast_ms"][0] > first["forecast_ms"][0] * 10


async def test_very_stale_model_falls_back_with_reason(db_session, sample_target, tmp_path):
    _save_bundle(tmp_path, sample_target.id, end=NOW - timedelta(days=10))

    result = await get_forecast(sample_target.id, db_session, horizon_hours=6, now=NOW)

    assert result["source"] == "fallback_linear"
    assert result["fallback_reason"] == "model_stale"


async def test_corrupt_model_file_falls_back_and_recovers_when_replaced(db_session, sample_target, tmp_path):
    path = os.path.join(str(tmp_path), f"{sample_target.id}.pkl")
    with open(path, "wb") as f:
        f.write(b"not a pickle at all")

    broken = await get_forecast(sample_target.id, db_session, horizon_hours=3, now=NOW)
    assert broken["source"] == "fallback_linear"
    assert broken["fallback_reason"] == "model_unreadable"

    _save_bundle(tmp_path, sample_target.id, end=NOW)
    os.utime(path, (time.time() + 5, time.time() + 5))
    fixed = await get_forecast(sample_target.id, db_session, horizon_hours=3, now=NOW)
    assert fixed["source"] == "trained_model"


async def test_non_finite_forecast_falls_back_instead_of_breaking_json(db_session, sample_target, tmp_path):
    joblib.dump({"version": 1, "model": NanModel(), "last_data_at": NOW.isoformat(),
                 "trained_at": NOW.isoformat(), "n_points": 72},
                os.path.join(str(tmp_path), f"{sample_target.id}.pkl"))

    result = await get_forecast(sample_target.id, db_session, horizon_hours=3, now=NOW)

    assert result["source"] == "fallback_linear"
    assert result["fallback_reason"] == "invalid_forecast"


async def test_negative_predictions_are_clamped_to_zero(db_session, sample_target, tmp_path):
    joblib.dump({"version": 1, "model": NegModel(), "last_data_at": NOW.isoformat(),
                 "trained_at": NOW.isoformat(), "n_points": 72},
                os.path.join(str(tmp_path), f"{sample_target.id}.pkl"))

    result = await get_forecast(sample_target.id, db_session, horizon_hours=3, now=NOW)

    assert result["source"] == "trained_model"
    assert result["forecast_ms"] == [0.0, 0.0, 0.0]


async def test_horizon_is_clamped_into_valid_range(db_session, sample_target, tmp_path):
    _save_bundle(tmp_path, sample_target.id, end=NOW)

    low = await get_forecast(sample_target.id, db_session, horizon_hours=0, now=NOW)
    negative = await get_forecast(sample_target.id, db_session, horizon_hours=-9, now=NOW)
    high = await get_forecast(sample_target.id, db_session, horizon_hours=10_000, now=NOW)

    assert len(low["forecast_ms"]) == 1
    assert len(negative["forecast_ms"]) == 1
    assert len(high["forecast_ms"]) == 168


async def test_legacy_bare_model_file_is_still_served(db_session, sample_target, tmp_path):
    joblib.dump(_fit(NOW), os.path.join(str(tmp_path), f"{sample_target.id}.pkl"))

    result = await get_forecast(sample_target.id, db_session, horizon_hours=4, now=NOW)

    assert result["source"] == "trained_model"
    assert len(result["forecast_ms"]) == 4


async def test_forecast_values_are_plain_python_floats_and_json_safe(db_session, sample_target, tmp_path):
    import json
    _save_bundle(tmp_path, sample_target.id, end=NOW)

    result = await get_forecast(sample_target.id, db_session, horizon_hours=5, now=NOW)

    json.dumps(result, allow_nan=False)
    assert all(type(v) is float for v in result["forecast_ms"])


async def test_model_loading_does_not_block_event_loop(db_session, sample_target, tmp_path, monkeypatch):
    import asyncio
    _save_bundle(tmp_path, sample_target.id, end=NOW)
    original = joblib.load

    def slow_load(path):
        time.sleep(0.4)
        return original(path)

    monkeypatch.setattr(ml_forecast.joblib, "load", slow_load)
    ticks = []

    async def ticker():
        for _ in range(6):
            ticks.append(time.monotonic())
            await asyncio.sleep(0.05)

    await asyncio.gather(get_forecast(sample_target.id, db_session, horizon_hours=3, now=NOW), ticker())

    gaps = [b - a for a, b in zip(ticks, ticks[1:])]
    assert max(gaps) < 0.3, f"event loop terblokir: gap {max(gaps):.2f}s"
