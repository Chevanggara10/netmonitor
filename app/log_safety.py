"""
Pengaman log: token bot Telegram ada di URL request
(https://api.telegram.org/bot<TOKEN>/method), dan logger httpx mencetak URL
itu di level INFO -- artinya token bocor mentah ke file log / terminal /
screenshot. Modul ini (1) menurunkan level logger httpx dan (2) menambah
filter yang menyensor pola token di SEMUA handler root, sebagai lapis kedua.
"""
import logging
import re

_TOKEN_PATTERN = re.compile(r"bot\d{6,}:[A-Za-z0-9_-]{20,}")
_REDACTED = "bot<REDACTED>"


def redact(text: str) -> str:
    return _TOKEN_PATTERN.sub(_REDACTED, text)


class TokenRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        redacted = redact(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def install_token_redaction() -> None:
    """Panggil SETELAH logging.basicConfig(...) supaya handler root sudah ada."""
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    root = logging.getLogger()
    for handler in root.handlers:
        if not any(isinstance(f, TokenRedactionFilter) for f in handler.filters):
            handler.addFilter(TokenRedactionFilter())
