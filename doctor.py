"""Periksa kesiapan sistem:  python doctor.py   (kode keluar 1 bila ada yang GAGAL)."""
import sys

from dotenv import load_dotenv

load_dotenv()

from app.doctor import exit_code, format_report, run_all_checks  # noqa: E402

if __name__ == "__main__":
    results = run_all_checks(strict="--strict" in sys.argv)
    print(format_report(results))
    sys.exit(exit_code(results))
