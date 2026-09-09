"""
Validasi keamanan untuk URL target monitoring.

Mencegah SSRF (Server-Side Request Forgery): tanpa validasi ini, seseorang
bisa memasukkan URL seperti "http://localhost:5432" atau "http://169.254.169.254"
(metadata endpoint cloud) sebagai target, dan memakai server monitoring ini
sebagai proxy untuk mengintip jaringan internal yang seharusnya tidak bisa
diakses dari luar.
"""
import socket
import ipaddress
from urllib.parse import urlparse

# Hostname yang selalu diblokir apapun hasil resolusi DNS-nya.
BLOCKED_HOSTNAMES = {"localhost", "0.0.0.0"}


class UnsafeURLError(ValueError):
    """Dilempar saat URL target mengarah ke jaringan internal/privat."""
    pass


def _is_private_or_reserved(ip_str: str) -> bool:
    ip = ipaddress.ip_address(ip_str)
    return (
        ip.is_private        # 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, dst
        or ip.is_loopback     # 127.0.0.0/8, ::1
        or ip.is_link_local   # 169.254.0.0/16 (termasuk cloud metadata endpoint), fe80::/10
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified  # 0.0.0.0, ::
    )


def validate_target_url(url: str) -> None:
    """
    Memvalidasi URL target sebelum disimpan sebagai target monitoring.
    Melempar UnsafeURLError kalau URL mengarah ke jaringan internal/privat.

    Catatan: validasi dilakukan saat membuat target (waktu registrasi),
    bukan saat setiap kali check dijalankan. Ini cukup untuk kasus umum,
    tapi secara teori DNS rebinding (domain publik yang belakangan
    di-resolve ke IP privat) tidak tertutup 100% oleh pendekatan ini --
    itu di luar cakupan MVP keamanan Fase 3 ini.
    """
    parsed = urlparse(url)
    hostname = parsed.hostname

    if not hostname:
        raise UnsafeURLError("URL tidak valid: hostname tidak ditemukan")

    if hostname.lower() in BLOCKED_HOSTNAMES:
        raise UnsafeURLError(f"URL tidak diizinkan: '{hostname}' mengarah ke server itu sendiri")

    # Resolusi DNS untuk cek IP asli di baliknya -- blokir kalau hostname-nya
    # sendiri sudah berupa IP privat (mis. "http://192.168.1.1"), atau kalau
    # domain publik ternyata resolve ke IP privat.
    try:
        resolved_ips = {info[4][0] for info in socket.getaddrinfo(hostname, None)}
    except socket.gaierror:
        # Tidak bisa di-resolve sama sekali -- biarkan lolos di sini, nanti
        # checker.py yang akan mencatatnya sebagai DOWN saat pengecekan asli.
        return

    for ip_str in resolved_ips:
        # Buang zone index IPv6 (mis. "%eth0") kalau ada, ipaddress tidak terima itu.
        clean_ip = ip_str.split("%")[0]
        try:
            is_unsafe = _is_private_or_reserved(clean_ip)
        except ValueError:
            # clean_ip bukan format IP yang valid -- lewati, bukan berarti aman/tidak aman.
            continue
        if is_unsafe:
            raise UnsafeURLError(
                f"URL tidak diizinkan: '{hostname}' mengarah ke alamat jaringan "
                f"internal/privat ({clean_ip}). Target monitoring harus berupa "
                f"server yang bisa diakses publik."
            )
