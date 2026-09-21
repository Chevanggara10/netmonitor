"""
Analis evaluasi untuk laporan bulanan.

Dua lapis, supaya laporan SELALU punya evaluasi:
1. build_evaluation(): analis lokal deterministik (aturan + hasil model forecast).
   Tidak butuh internet, tidak ada data keluar dari server.
2. llm_narrative(): OPSIONAL, menulis narasi tambahan lewat Claude API. Hanya
   aktif kalau REPORT_AI_ENABLED=true DAN ANTHROPIC_API_KEY terisi. Yang
   dikirim hanya angka agregat yang dianonimkan (nama & URL target diganti
   "Target-A", "Target-B", ...). Gagal/timeout/jawaban aneh -> None, dan
   laporan tetap terkirim dengan analis lokal.
"""
import json
import logging
import os

import httpx

logger = logging.getLogger("netmonitor.ai_analyst")

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-sonnet-5"
MAX_NARRATIVE_CHARS = 1500
MAX_TEXT_CHARS = 3900  # batas pesan Telegram 4096

SYSTEM_PROMPT = (
    "Kamu analis operasional jaringan. Tulis evaluasi bulanan singkat dalam Bahasa Indonesia "
    "(maksimal 150 kata) berdasarkan data JSON. Pakai HANYA angka yang ada di data. Jangan "
    "mengarang penyebab pasti; beri hipotesis dengan kata 'kemungkinan'. Format: 1 kalimat "
    "ringkasan, 3 poin temuan, 2 poin rekomendasi."
)

_PAYLOAD_KEYS = (
    "total_checks", "uptime_pct", "avg_rt", "p95_rt", "incident_count", "downtime_minutes",
    "longest_incident_minutes", "anomaly_count", "prev_uptime_pct", "prev_avg_rt",
    "busiest_hour", "forecast_avg_ms",
)


def sla_label(uptime_pct: float) -> str:
    if uptime_pct >= 99.9:
        return "Sangat baik"
    if uptime_pct >= 99.0:
        return "Baik"
    if uptime_pct >= 95.0:
        return "Perlu perhatian"
    return "Kritis"


def _names(items: list[dict]) -> str:
    return ", ".join(t["name"] for t in items)


def build_evaluation(period_label: str, targets: list[dict]) -> dict:
    with_data = [t for t in targets if t.get("total_checks", 0) > 0]
    if not with_data:
        return {
            "level": "Tidak ada data",
            "headline": f"Evaluasi {period_label}: tidak ada data monitoring pada periode ini.",
            "findings": ["Tidak ada hasil pengecekan yang tersimpan untuk periode ini."],
            "recommendations": [
                "Pastikan server monitoring menyala terus dan target aktif (tidak dijeda) sepanjang bulan.",
            ],
        }

    total = sum(t["total_checks"] for t in with_data)
    up = sum(t["up_checks"] for t in with_data)
    fleet_uptime = up / total * 100
    level = sla_label(fleet_uptime)

    findings = [f"Ketersediaan rata-rata seluruh target {fleet_uptime:.2f}% ({level})."]
    recommendations: list[str] = []

    worst = min(with_data, key=lambda t: t["uptime_pct"])
    if worst["uptime_pct"] < 99.9 and len(with_data) > 1:
        findings.append(f"Ketersediaan terendah: {worst['name']} ({worst['uptime_pct']:.2f}%).")

    with_incidents = [t for t in with_data if t.get("incident_count", 0) > 0]
    for t in with_incidents:
        findings.append(
            f"{t['name']}: {t['incident_count']} insiden downtime, total {t['downtime_minutes']:.0f} menit, "
            f"terpanjang {t['longest_incident_minutes']:.0f} menit."
        )
    if with_incidents:
        longest = max(with_incidents, key=lambda t: t["longest_incident_minutes"])
        recommendations.append(
            f"Tinjau penyebab insiden downtime (terpanjang {longest['longest_incident_minutes']:.0f} menit "
            f"pada {longest['name']}) dan pastikan alert Telegram/email aktif agar penanganan lebih cepat."
        )

    slower, faster = [], []
    for t in with_data:
        if t.get("avg_rt") and t.get("prev_avg_rt"):
            change = (t["avg_rt"] - t["prev_avg_rt"]) / t["prev_avg_rt"] * 100
            if change >= 20:
                slower.append((t, change))
            elif change <= -20:
                faster.append((t, change))
    for t, change in slower:
        findings.append(f"{t['name']}: respons melambat {change:.0f}% dibanding bulan lalu ({t['avg_rt']:.0f} ms).")
    for t, change in faster:
        findings.append(f"{t['name']}: respons membaik {abs(change):.0f}% dibanding bulan lalu ({t['avg_rt']:.0f} ms).")
    if slower:
        recommendations.append(f"Periksa beban dan kapasitas server {_names([t for t, _ in slower])}: respons melambat.")

    anomalous = [t for t in with_data if t.get("anomaly_count", 0) / t["total_checks"] > 0.01]
    for t in anomalous:
        findings.append(f"{t['name']}: {t['anomaly_count']} lonjakan latency terdeteksi sebagai anomali.")
    if anomalous:
        recommendations.append(
            f"Selidiki lonjakan latency (anomali) pada {_names(anomalous)}; cocokkan dengan jadwal backup atau puncak trafik."
        )

    rated = [t for t in with_data if t.get("avg_rt")]
    if rated:
        slowest = max(rated, key=lambda t: t["avg_rt"])
        if slowest.get("busiest_hour") is not None:
            findings.append(
                f"{slowest['name']} (respons rata-rata tertinggi, {slowest['avg_rt']:.0f} ms) paling lambat "
                f"sekitar pukul {slowest['busiest_hour']:02d}:00."
            )

    worsening = [t for t in with_data if t.get("forecast_avg_ms") and t.get("avg_rt")
                 and t["forecast_avg_ms"] >= t["avg_rt"] * 1.25]
    for t in worsening:
        findings.append(
            f"Prediksi model AI: respons {t['name']} diperkirakan naik ke sekitar {t['forecast_avg_ms']:.0f} ms 7 hari ke depan."
        )
    if worsening:
        recommendations.append(f"Siapkan kapasitas/beban tambahan untuk {_names(worsening)} sebelum lonjakan terjadi.")

    if fleet_uptime < 95:
        recommendations.insert(0, "Prioritaskan perbaikan ketersediaan: ada target di bawah 95% sepanjang bulan.")
    if not recommendations:
        recommendations.append(
            "Pertahankan konfigurasi saat ini, lanjutkan monitoring, dan latih ulang model forecast tiap bulan."
        )

    return {
        "level": level,
        "headline": f"Evaluasi {period_label}: {level} - ketersediaan {fleet_uptime:.2f}%",
        "findings": findings,
        "recommendations": recommendations,
    }


def render_evaluation_text(period_label: str, evaluation: dict, narrative: str | None = None) -> str:
    lines = [f"LAPORAN BULANAN - {period_label}", evaluation["headline"], "", "Temuan:"]
    lines += [f"- {f}" for f in evaluation["findings"]]
    lines += ["", "Rekomendasi:"] + [f"- {r}" for r in evaluation["recommendations"]]
    if narrative:
        lines += ["", "Analisis AI:", narrative[:MAX_NARRATIVE_CHARS]]
    text = "\n".join(lines)
    return text if len(text) <= MAX_TEXT_CHARS else text[: MAX_TEXT_CHARS - 1] + "…"


def _label(index: int) -> str:
    return f"Target-{chr(65 + index)}" if index < 26 else f"Target-{index + 1}"


def anonymize_targets(targets: list[dict]) -> tuple[list[dict], dict[str, str]]:
    """Payload hanya berisi angka agregat + label kode; mapping (lokal) untuk memulihkan nama."""
    payload, mapping = [], {}
    for i, t in enumerate(targets):
        label = _label(i)
        mapping[label] = t["name"]
        payload.append({"label": label, **{k: t.get(k) for k in _PAYLOAD_KEYS}})
    return payload, mapping


def deanonymize(text: str, mapping: dict[str, str]) -> str:
    for label in sorted(mapping, key=len, reverse=True):
        text = text.replace(label, mapping[label])
    return text


async def llm_narrative(payload: list[dict], period_label: str, *, api_key: str, model: str,
                        timeout: float = 60.0, client: httpx.AsyncClient | None = None) -> str | None:
    """Narasi dari Claude API; None bila apa pun tidak beres (tidak pernah melempar)."""
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=timeout)
    try:
        response = await http.post(
            ANTHROPIC_URL,
            headers={"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION, "content-type": "application/json"},
            json={
                "model": model,
                "max_tokens": 700,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": f"Periode: {period_label}\nData: {json.dumps(payload, ensure_ascii=False)}"}],
            },
        )
        if response.status_code != 200:
            logger.warning(f"Claude API membalas HTTP {response.status_code}; memakai analis lokal.")
            return None
        blocks = response.json().get("content") or []
        text = "".join(b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text").strip()
        return text[:MAX_NARRATIVE_CHARS] if text else None
    except Exception as exc:
        logger.warning(f"Narasi AI gagal ({type(exc).__name__}); memakai analis lokal.")
        return None
    finally:
        if owns_client:
            await http.aclose()


def _ai_enabled() -> bool:
    return os.environ.get("REPORT_AI_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


async def analyze(period_label: str, targets: list[dict], *, client: httpx.AsyncClient | None = None) -> tuple[dict, str | None]:
    """(evaluasi lokal, narasi LLM atau None). Evaluasi lokal selalu ada."""
    evaluation = build_evaluation(period_label, targets)

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not (_ai_enabled() and api_key):
        return evaluation, None

    with_data = [t for t in targets if t.get("total_checks", 0) > 0]
    if not with_data:
        return evaluation, None

    payload, mapping = anonymize_targets(with_data)
    model = os.environ.get("REPORT_AI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    narrative = await llm_narrative(payload, period_label, api_key=api_key, model=model, client=client)
    return evaluation, (deanonymize(narrative, mapping) if narrative else None)
