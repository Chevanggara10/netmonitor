import logging
import os
import socket

import pytest

from app.database import resolve_database_url, PROJECT_ROOT


def test_default_sqlite_url_is_absolute_and_independent_of_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    url = resolve_database_url(None)

    assert url.startswith("sqlite+aiosqlite:///")
    db_path = url[len("sqlite+aiosqlite:///"):]
    assert os.path.isabs(db_path)
    assert db_path.replace("\\", "/").endswith("netmonitor.db")
    assert str(PROJECT_ROOT).replace("\\", "/") in db_path.replace("\\", "/")


def test_relative_sqlite_url_is_resolved_against_project_root(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    url = resolve_database_url("sqlite+aiosqlite:///./custom.db")

    db_path = url[len("sqlite+aiosqlite:///"):]
    assert os.path.isabs(db_path)
    assert str(PROJECT_ROOT).replace("\\", "/") in db_path.replace("\\", "/")


def test_absolute_sqlite_url_is_left_untouched():
    url = resolve_database_url("sqlite+aiosqlite:////var/data/x.db")
    assert url == "sqlite+aiosqlite:////var/data/x.db"


def test_postgres_scheme_is_converted_to_asyncpg():
    assert resolve_database_url("postgres://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"
    assert resolve_database_url("postgresql://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"


def test_redaction_filter_hides_bot_token_in_message_and_args():
    from app.log_safety import TokenRedactionFilter

    filt = TokenRedactionFilter()
    fake_token = "123456789" + ":" + "AAEabc_DEF-ghi123456789012345678901"
    record = logging.LogRecord(
        "httpx", logging.INFO, __file__, 1,
        'HTTP Request: POST https://api.telegram.org/bot%s/getMe "HTTP/1.1 200 OK"',
        (fake_token,), None,
    )

    assert filt.filter(record) is True
    rendered = record.getMessage()
    assert "AAEabc" not in rendered
    assert "123456789:" not in rendered
    assert "bot<REDACTED>" in rendered


def test_install_token_redaction_quiets_httpx_and_filters_root_handlers():
    from app.log_safety import install_token_redaction, TokenRedactionFilter

    root = logging.getLogger()
    handler = logging.StreamHandler()
    root.addHandler(handler)
    try:
        install_token_redaction()
        assert logging.getLogger("httpx").level >= logging.WARNING
        assert any(isinstance(f, TokenRedactionFilter) for f in handler.filters)
    finally:
        root.removeHandler(handler)


def test_single_instance_lock_rejects_second_holder():
    from app.single_instance import acquire_single_instance_lock, AlreadyRunningError

    port = _free_port()
    first = acquire_single_instance_lock(port)
    try:
        with pytest.raises(AlreadyRunningError):
            acquire_single_instance_lock(port)
    finally:
        first.close()

    second = acquire_single_instance_lock(port)
    second.close()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
