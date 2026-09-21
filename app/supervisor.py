"""
Supervisor sederhana: menjalankan beberapa subproses (web + bot) dan
menghidupkannya kembali otomatis kalau mati, dengan jeda yang makin lama
(backoff) agar tidak restart-loop yang membanjiri CPU/log.

Dipakai oleh run_all.py; logikanya dipisah di sini supaya bisa diuji.
"""
import subprocess
import threading
import time
from typing import Callable, Sequence

MAX_BACKOFF_SECONDS = 30
STABLE_RUN_SECONDS = 60  # jalan selama ini tanpa mati = hitungan restart direset


def backoff_delay(restarts: int) -> float:
    """1, 2, 4, 8, 16, 30, 30, ... detik."""
    return min(2 ** max(restarts, 0), MAX_BACKOFF_SECONDS)


class ManagedProcess:
    def __init__(self, name: str, cmd: Sequence[str], cwd: str | None = None,
                 no_restart_codes: Sequence[int] = ()):
        self.name = name
        self.cmd = list(cmd)
        self.cwd = cwd
        self.no_restart_codes = tuple(no_restart_codes)
        self.proc: subprocess.Popen | None = None
        self.restarts = 0
        self.started_at = 0.0
        self.next_start_at = 0.0
        self.gave_up = False

    def start(self) -> None:
        self.proc = subprocess.Popen(self.cmd, cwd=self.cwd)
        self.started_at = time.monotonic()

    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def note_stable_run(self, seconds: float) -> None:
        if seconds >= STABLE_RUN_SECONDS:
            self.restarts = 0

    def stop(self, timeout: float = 8.0) -> None:
        if not self.is_running():
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=timeout)


def supervise(processes: Sequence[ManagedProcess], stop: threading.Event,
              poll: float = 1.0, delay_fn: Callable[[int], float] = backoff_delay,
              log: Callable[[str], None] = print) -> None:
    """Blokir sampai `stop` di-set; semua subproses dihentikan sebelum kembali."""
    for p in processes:
        p.start()
        log(f"[{p.name}] dimulai")
    try:
        while not stop.wait(poll):
            now = time.monotonic()
            for p in processes:
                if p.gave_up or p.is_running():
                    continue
                if p.next_start_at == 0.0:
                    code = p.proc.returncode
                    p.note_stable_run(now - p.started_at)
                    if code in p.no_restart_codes:
                        p.gave_up = True
                        log(f"[{p.name}] berhenti (kode {code}) dan tidak akan dihidupkan ulang")
                        continue
                    delay = delay_fn(p.restarts)
                    p.next_start_at = now + delay
                    log(f"[{p.name}] mati (kode {code}); mulai ulang dalam {delay:.0f} dtk")
                if now >= p.next_start_at:
                    p.restarts += 1
                    p.next_start_at = 0.0
                    p.start()
                    log(f"[{p.name}] dimulai ulang (#{p.restarts})")
    finally:
        for p in processes:
            p.stop()
