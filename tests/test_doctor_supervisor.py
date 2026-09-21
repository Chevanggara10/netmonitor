"""Test logika doctor (pra-pemeriksaan) dan supervisor (auto-restart)."""
import socket
import sqlite3
import sys
import threading
import time

from app import doctor
from app.supervisor import ManagedProcess, backoff_delay, supervise


# ---------- doctor ----------

def test_python_version_ok():
    assert doctor.check_python().status == "ok"


def test_python_version_too_old():
    assert doctor.check_python(version=(3, 8, 0)).status == "fail"


def test_dependencies_missing_reports_fix():
    res = doctor.check_dependencies(modules=["os", "modul_yang_tidak_ada_xyz"])
    assert res.status == "fail"
    assert "modul_yang_tidak_ada_xyz" in res.message
    assert "pip install" in res.fix


def test_dependencies_all_present():
    assert doctor.check_dependencies(modules=["os", "sys"]).status == "ok"


def test_env_file_missing_is_warning(tmp_path):
    assert doctor.check_env_file(str(tmp_path)).status == "warn"
    (tmp_path / ".env").write_text("A=1")
    assert doctor.check_env_file(str(tmp_path)).status == "ok"


def test_secret_key_default_or_short_fails_in_strict():
    assert doctor.check_secret_key({}).status == "warn"
    assert doctor.check_secret_key({"NETMONITOR_SECRET_KEY": "pendek"}).status == "warn"
    assert doctor.check_secret_key({"NETMONITOR_SECRET_KEY": "x" * 40}).status == "ok"
    assert doctor.check_secret_key({}, strict=True).status == "fail"


def test_bot_token_absent_is_warning_not_fail():
    assert doctor.check_bot_token({}).status == "warn"


def test_bot_token_malformed_fails():
    assert doctor.check_bot_token({"TELEGRAM_BOT_TOKEN": "bukan-token"}).status == "fail"


def test_bot_token_valid_format_with_verifier():
    tok = "123456789" + ":" + "A" * 30
    ok = doctor.check_bot_token({"TELEGRAM_BOT_TOKEN": tok}, verify=lambda t: "monitorbot")
    assert ok.status == "ok" and "monitorbot" in ok.message
    bad = doctor.check_bot_token({"TELEGRAM_BOT_TOKEN": tok}, verify=lambda t: None)
    assert bad.status == "fail"
    assert tok not in bad.message  # token tidak boleh bocor ke output


def test_database_missing_file_is_ok_before_first_run(tmp_path):
    res = doctor.check_database(f"sqlite+aiosqlite:///{tmp_path / 'baru.db'}", head_revision="abc")
    assert res.status == "ok"


def test_database_needs_migration(tmp_path):
    path = tmp_path / "x.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE alembic_version (version_num TEXT)")
    con.execute("INSERT INTO alembic_version VALUES ('lama')")
    con.commit()
    con.close()
    res = doctor.check_database(f"sqlite+aiosqlite:///{path}", head_revision="baru")
    assert res.status == "warn"
    assert "alembic upgrade head" in res.fix
    con = sqlite3.connect(path)
    con.execute("UPDATE alembic_version SET version_num='baru'")
    con.commit()
    con.close()
    assert doctor.check_database(f"sqlite+aiosqlite:///{path}", head_revision="baru").status == "ok"


def test_port_free_and_busy():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    assert doctor.check_port(port, "web").status == "fail"
    s.close()
    assert doctor.check_port(port, "web").status == "ok"


def test_backup_check(tmp_path):
    assert doctor.check_backup(str(tmp_path)).status == "warn"


def test_summarize_exit_code():
    ok = doctor.CheckResult("a", "ok", "x")
    warn = doctor.CheckResult("b", "warn", "x")
    fail = doctor.CheckResult("c", "fail", "x", fix="perbaiki")
    assert doctor.exit_code([ok, warn]) == 0
    assert doctor.exit_code([ok, fail]) == 1
    text = doctor.format_report([ok, warn, fail])
    assert "perbaiki" in text


# ---------- supervisor ----------

def test_backoff_grows_and_caps():
    delays = [backoff_delay(n) for n in range(0, 10)]
    assert delays[0] == 1
    assert delays == sorted(delays)
    assert max(delays) == 30


def test_backoff_resets_after_stable_run():
    p = ManagedProcess("x", [sys.executable, "-c", "pass"])
    p.restarts = 5
    p.note_stable_run(seconds=120)
    assert p.restarts == 0


def test_supervise_restarts_crashing_process(tmp_path):
    counter = tmp_path / "n.txt"
    code = (
        "import pathlib;p=pathlib.Path(r'%s');"
        "p.write_text(p.read_text()+'x' if p.exists() else 'x')" % counter
    )
    proc = ManagedProcess("crasher", [sys.executable, "-c", code])
    stop = threading.Event()
    t = threading.Thread(target=supervise, args=([proc], stop),
                         kwargs={"poll": 0.05, "delay_fn": lambda n: 0.05})
    t.start()
    deadline = time.time() + 10
    while time.time() < deadline and (not counter.exists() or len(counter.read_text()) < 3):
        time.sleep(0.05)
    stop.set()
    t.join(timeout=10)
    assert not t.is_alive()
    assert len(counter.read_text()) >= 3


def test_supervise_stops_children_on_stop():
    proc = ManagedProcess("sleeper", [sys.executable, "-c", "import time; time.sleep(60)"])
    stop = threading.Event()
    t = threading.Thread(target=supervise, args=([proc], stop), kwargs={"poll": 0.05})
    t.start()
    time.sleep(0.5)
    assert proc.is_running()
    stop.set()
    t.join(timeout=15)
    assert not t.is_alive()
    assert not proc.is_running()


def test_fatal_exit_code_not_restarted():
    # exit code 3 = "jangan restart" (mis. bot ditolak karena instance lain)
    proc = ManagedProcess("once", [sys.executable, "-c", "raise SystemExit(3)"], no_restart_codes=(3,))
    stop = threading.Event()
    t = threading.Thread(target=supervise, args=([proc], stop), kwargs={"poll": 0.05})
    t.start()
    time.sleep(1.0)
    stop.set()
    t.join(timeout=10)
    assert proc.restarts == 0
    assert proc.gave_up
