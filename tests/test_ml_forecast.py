import os
import joblib
import pandas as pd
from statsmodels.tsa.holtwinters import ExponentialSmoothing

from app.ml_forecast import get_forecast, _MODEL_CACHE


async def test_get_forecast_falls_back_when_no_model_file(db_session, sample_target, monkeypatch, tmp_path):
    _MODEL_CACHE.clear()
    monkeypatch.setattr("app.ml_forecast.MODELS_DIR", str(tmp_path))

    result = await get_forecast(sample_target.id, db_session, horizon_hours=24)

    assert result["source"] == "fallback_linear"
    assert result["target_id"] == sample_target.id


async def test_get_forecast_uses_trained_model_when_present(db_session, sample_target, monkeypatch, tmp_path):
    _MODEL_CACHE.clear()
    monkeypatch.setattr("app.ml_forecast.MODELS_DIR", str(tmp_path))

    end = pd.Timestamp.now("UTC").tz_localize(None).floor("h")
    dates = pd.date_range(end=end, periods=72, freq="h")
    series = pd.Series([100 + (i % 24) for i in range(72)], index=dates)
    model = ExponentialSmoothing(series, trend="add", seasonal="add", seasonal_periods=24).fit()
    joblib.dump(model, os.path.join(str(tmp_path), f"{sample_target.id}.pkl"))

    result = await get_forecast(sample_target.id, db_session, horizon_hours=12)

    assert result["source"] == "trained_model"
    assert result["target_id"] == sample_target.id
    assert len(result["forecast_ms"]) == 12
