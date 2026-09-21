from datetime import datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from app.auth import create_access_token
from app.database import get_db
from app.main import app, limiter
from app.models import MonitorTarget, TelegramLinkToken, User
from tests.conftest import add_check_results


@pytest_asyncio.fixture
async def client(db_session):
    async def _override():
        yield db_session

    app.dependency_overrides[get_db] = _override
    limiter.reset()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
    limiter.reset()


def _auth(user):
    return {"Authorization": f"Bearer {create_access_token(user.id, user.email)}"}


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/targets/1/forecast"),
    ("GET", "/api/targets/1/export/excel"),
    ("POST", "/api/telegram/link-token"),
    ("GET", "/api/telegram/status"),
    ("DELETE", "/api/telegram/link"),
])
async def test_new_endpoints_require_authentication(client, method, path):
    response = await client.request(method, path)
    assert response.status_code == 401


async def test_other_users_target_is_hidden_as_404(client, db_session, sample_user):
    intruder = User(email="intruder@example.com", hashed_password="x")
    db_session.add(intruder)
    await db_session.commit()
    await db_session.refresh(intruder)
    target = MonitorTarget(user_id=sample_user.id, name="Milik Orang", url="https://example.com")
    db_session.add(target)
    await db_session.commit()
    await db_session.refresh(target)

    forecast = await client.get(f"/api/targets/{target.id}/forecast", headers=_auth(intruder))
    export = await client.get(f"/api/targets/{target.id}/export/excel", headers=_auth(intruder))

    assert forecast.status_code == 404
    assert export.status_code == 404


@pytest.mark.parametrize("horizon", [0, -5, 99999])
async def test_forecast_survives_absurd_horizon(client, sample_user, sample_target, horizon):
    response = await client.get(f"/api/targets/{sample_target.id}/forecast?horizon={horizon}", headers=_auth(sample_user))
    assert response.status_code == 200
    assert response.json()["source"] in ("trained_model", "fallback_linear")


@pytest.mark.parametrize("days", [-3, 0, 10**9])
async def test_export_survives_absurd_days(client, db_session, sample_user, sample_target, days):
    await add_check_results(db_session, sample_target.id, count=3)
    response = await client.get(f"/api/targets/{sample_target.id}/export/excel?days={days}", headers=_auth(sample_user))
    assert response.status_code == 200
    assert "spreadsheetml" in response.headers["content-type"]
    assert response.content[:2] == b"PK"


async def test_link_token_returns_ready_to_copy_command(client, sample_user):
    response = await client.post("/api/telegram/link-token", headers=_auth(sample_user))
    body = response.json()
    assert response.status_code == 200
    assert body["command"] == f"/link {body['token']}"


async def test_new_link_token_replaces_old_and_purges_expired(client, db_session, sample_user):
    db_session.add(TelegramLinkToken(token="old-active", user_id=sample_user.id, expires_at=datetime.utcnow() + timedelta(minutes=5)))
    db_session.add(TelegramLinkToken(token="old-expired", user_id=999, expires_at=datetime.utcnow() - timedelta(minutes=5)))
    await db_session.commit()

    response = await client.post("/api/telegram/link-token", headers=_auth(sample_user))

    tokens = [t.token for t in (await db_session.execute(select(TelegramLinkToken))).scalars().all()]
    assert tokens == [response.json()["token"]]


async def test_link_token_is_rate_limited(client, sample_user):
    codes = [(await client.post("/api/telegram/link-token", headers=_auth(sample_user))).status_code for _ in range(7)]
    assert codes[:5] == [200] * 5
    assert 429 in codes[5:]


async def test_deleting_target_removes_its_history_and_alert_rule(client, db_session, sample_user, sample_target):
    from app.models import AlertRule, CheckResult
    await add_check_results(db_session, sample_target.id, count=4)
    db_session.add(AlertRule(target_id=sample_target.id))
    await db_session.commit()
    target_id = sample_target.id

    response = await client.delete(f"/api/targets/{target_id}", headers=_auth(sample_user))

    assert response.status_code == 200
    left_checks = (await db_session.execute(select(CheckResult).where(CheckResult.target_id == target_id))).scalars().all()
    left_rules = (await db_session.execute(select(AlertRule).where(AlertRule.target_id == target_id))).scalars().all()
    assert left_checks == [] and left_rules == []


async def test_telegram_status_and_unlink_roundtrip(client, db_session, sample_user):
    assert (await client.get("/api/telegram/status", headers=_auth(sample_user))).json() == {"linked": False}

    sample_user.telegram_chat_id = "chat-1"
    await db_session.commit()
    assert (await client.get("/api/telegram/status", headers=_auth(sample_user))).json() == {"linked": True}

    first = await client.delete("/api/telegram/link", headers=_auth(sample_user))
    second = await client.delete("/api/telegram/link", headers=_auth(sample_user))
    assert first.json() == second.json() == {"linked": False}
    await db_session.refresh(sample_user)
    assert sample_user.telegram_chat_id is None
