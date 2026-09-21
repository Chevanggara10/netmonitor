"""
Penjaga single-instance untuk proses bot Telegram.

Telegram hanya mengizinkan 1 konsumen getUpdates per token; instance kedua
menyebabkan "Conflict: terminated by other getUpdates request" dan keduanya
saling menjatuhkan (terjadi saat pengujian). Kunci ini memakai socket TCP
di localhost: OS otomatis melepasnya kalau proses mati/crash, jadi tidak ada
file lock basi yang perlu dibersihkan, dan bekerja sama di Windows/Linux.
"""
import socket

DEFAULT_LOCK_PORT = 47831


class AlreadyRunningError(RuntimeError):
    pass


def acquire_single_instance_lock(port: int = DEFAULT_LOCK_PORT) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # Windows: tanpa EXCLUSIVEADDRUSE, bind kedua ke port yang sama bisa lolos.
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        sock.bind(("127.0.0.1", port))
        sock.listen(1)
    except OSError as exc:
        sock.close()
        raise AlreadyRunningError(
            f"Instance lain dari bot sudah berjalan (port kunci {port} terpakai)."
        ) from exc
    return sock
