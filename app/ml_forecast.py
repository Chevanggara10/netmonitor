"""
Serving model forecast yang sudah dilatih offline (training/train_forecast.py).

Kalau model untuk sebuah target belum ada / basi / rusak / menghasilkan angka
tidak valid, fallback ke app/prediction.py (regresi linear sederhana) --
endpoint forecast tidak boleh pernah 500 hanya karena masalah model, dan
response selalu menyebut alasan fallback (`fallback_reason`).

Catatan keamanan: file .pkl dimuat lewat joblib (pickle) -- HANYA muat dari
folder model milik sendiri; siapa pun yang bisa menulis ke training/models
bisa mengeksekusi kode di server ini. Jangan pernah menaruh .pkl dari luar.
"""
import asyncio
import logging
import math
import os
import threading
from datetime import datetime, timezone

import joblib
import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import PROJECT_ROOT
from app.prediction import get_trend_for_target

logger = logging.getLogger("netmonitor.ml_forecast")

# Absolut (bukan relatif ke cwd) supaya web server yang dijalankan dari folder
# lain tetap menemukan model yang dilatih di project ini.
MODELS_DIR = str(PROJECT_ROOT / "training" / "models")
MAX_HORIZON_HOURS = 168
# Model yang datanya lebih tua dari ini dianggap basi: ekstrapolasi terlalu jauh
# dari data terakhir menyesatkan, lebih baik fallback + minta retrain.
MAX_MODEL_AGE_HOURS = 168

# target_id -> ((mtime_ns, size), bundle). Kunci (mtime, size) membuat model
# hasil retrain langsung terpakai tanpa restart server.
_MODEL_CACHE: dict[int, tuple[tuple[int, int], dict]] = {}
_cache_lock = threading.Lock()


class _ModelUnreadable(Exception):
    pass


def _normalize(raw) -> dict:
    """Terima bundle v1 (dict) maupun file lama berisi model polos."""
    if isinstance(raw, dict) and "model" in raw:
        model, last, trained_at = raw["model"], raw.get("last_data_at"), raw.get("trained_at")
    else:
        model, last, trained_at = raw, None, None

    if not hasattr(model, "forecast"):
        raise ValueError("objek model tidak punya method forecast")
    if last is None:
        last = model.fittedvalues.index[-1]

    last_ts = pd.Timestamp(last)
    if last_ts.tzinfo is not None:
        last_ts = last_ts.tz_convert("UTC").tz_localize(None)
    return {"model": model, "last_data_at": last_ts, "trained_at": trained_at}


def _load_bundle(target_id: int) -> dict | None:
    path = os.path.join(MODELS_DIR, f"{target_id}.pkl")
    try:
        stat = os.stat(path)
    except FileNotFoundError:
        with _cache_lock:
            _MODEL_CACHE.pop(target_id, None)
        return None

    key = (stat.st_mtime_ns, stat.st_size)
    with _cache_lock:
        cached = _MODEL_CACHE.get(target_id)
    if cached and cached[0] == key:
        return cached[1]

    try:
        bundle = _normalize(joblib.load(path))
    except Exception as exc:
        logger.warning(f"Model target {target_id} tidak bisa dibaca: {exc}")
        with _cache_lock:
            _MODEL_CACHE.pop(target_id, None)
        raise _ModelUnreadable(str(exc)) from exc

    with _cache_lock:
        _MODEL_CACHE[target_id] = (key, bundle)
    return bundle


def _predict_sync(target_id: int, horizon: int, now: datetime) -> tuple[dict | None, str]:
    """Berjalan di thread (bukan event loop): load pickle + statsmodels bisa detik-an."""
    try:
        bundle = _load_bundle(target_id)
    except _ModelUnreadable:
        return None, "model_unreadable"
    if bundle is None:
        return None, "no_model"

    one_hour = pd.Timedelta(hours=1)
    last = bundle["last_data_at"].floor("h")
    now_h = pd.Timestamp(now).floor("h")

    if (now_h - last) / one_hour > MAX_MODEL_AGE_HOURS:
        return None, "model_stale"

    # Prediksi dimulai jam berikutnya dari SEKARANG, bukan dari akhir data training.
    first = max(now_h + one_hour, last + one_hour)
    steps_to_first = int((first - last) / one_hour)

    try:
        values = list(bundle["model"].forecast(steps_to_first + horizon - 1))[-horizon:]
        floats = [float(v) for v in values]
    except Exception as exc:
        logger.warning(f"Forecast model target {target_id} gagal: {exc}")
        return None, "forecast_failed"

    if len(floats) != horizon or any(not math.isfinite(v) for v in floats):
        return None, "invalid_forecast"

    return {
        "forecast_ms": [round(max(0.0, v), 2) for v in floats],
        "timestamps": [(first + i * one_hour).isoformat() for i in range(horizon)],
        "trained_until": last.isoformat(),
        "trained_at": bundle["trained_at"],
    }, "ok"


async def get_forecast(target_id: int, db: AsyncSession, horizon_hours: int = 24,
                       now: datetime | None = None) -> dict:
    horizon = max(1, min(int(horizon_hours), MAX_HORIZON_HOURS))
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)

    result, reason = await asyncio.to_thread(_predict_sync, target_id, horizon, now)

    if result is not None:
        return {"source": "trained_model", "target_id": target_id, "horizon_hours": horizon, **result}

    trend = await get_trend_for_target(target_id, db)
    return {"source": "fallback_linear", "target_id": target_id, "fallback_reason": reason, "trend": trend}
