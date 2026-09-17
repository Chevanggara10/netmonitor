from datetime import datetime, timedelta
from sqlalchemy import select

from app.models import TelegramLinkToken


async def test_generate_link_token_creates_row_with_10min_expiry(db_session, sample_user):
    from app.main import _generate_telegram_link_token

    token = await _generate_telegram_link_token(sample_user.id, db_session)

    assert isinstance(token, str) and len(token) > 10
    result = await db_session.execute(select(TelegramLinkToken).where(TelegramLinkToken.token == token))
    row = result.scalar_one()
    assert row.user_id == sample_user.id
    assert row.expires_at > datetime.utcnow()
    assert row.expires_at <= datetime.utcnow() + timedelta(minutes=10, seconds=5)
