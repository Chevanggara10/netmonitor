"""
Serving model forecast yang sudah dilatih offline (training/train_forecast.py).

Kalau model untuk sebuah target belum ada/gagal dimuat, fallback ke
app/prediction.py (regresi linear sederhana) -- endpoint forecast tidak
boleh pernah 500 hanya karena model belum dilatih atau file corrupt.
"""
import logging
import os
import joblib
from sqlalchemy.ext.asyncio import AsyncSession
from app.prediction import get_trend_for_target

logger = logging.getLogger("netmonitor.ml_forecast")

MODELS_DIR = "training/models"

# Cache in-memory supaya tidak reload file .pkl dari disk tiap request.
# Key: target_id, Value: model object.
_MODEL_CACHE: dict[int, object] = {}


def _load_model(target_id: int):
    if target_id in _MODEL_CACHE:
        return _MODEL_CACHE[target_id]

    path = os.path.join(MODELS_DIR, f"{target_id}.pkl")
    if not os.path.exists(path):
        return None

    try:
        model = joblib.load(path)
    except Exception as e:
        logger.warning(f"Gagal load model untuk target {target_id}: {e}")
        return None

    _MODEL_CACHE[target_id] = model
    return model


async def get_forecast(target_id: int, db: AsyncSession, horizon_hours: int = 24) -> dict:
    model = _load_model(target_id)

    if model is None:
        trend = await get_trend_for_target(target_id, db)
        return {
            "source": "fallback_linear",
            "target_id": target_id,
            "trend": trend,
        }

    try:
        forecast_values = model.forecast(horizon_hours)
    except Exception as e:
        logger.warning(f"Gagal forecast dengan model target {target_id}, fallback: {e}")
        trend = await get_trend_for_target(target_id, db)
        return {
            "source": "fallback_linear",
            "target_id": target_id,
            "trend": trend,
        }

    return {
        "source": "trained_model",
        "target_id": target_id,
        "horizon_hours": horizon_hours,
        "forecast_ms": [round(float(v), 2) for v in forecast_values],
    }
