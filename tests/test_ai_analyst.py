import json

import httpx
import pytest

from app.ai_analyst import (
    analyze, anonymize_targets, build_evaluation, deanonymize, llm_narrative, render_evaluation_text,
)


def _t(name="Situs A", **over):
    base = {
        "name": name, "url": "https://rahasia.mil.id/", "total_checks": 1000, "up_checks": 1000,
        "uptime_pct": 100.0, "avg_rt": 300.0, "p95_rt": 500.0, "incident_count": 0,
        "downtime_minutes": 0.0, "longest_incident_minutes": 0.0, "anomaly_count": 0,
        "prev_uptime_pct": 100.0, "prev_avg_rt": 300.0, "busiest_hour": 14, "forecast_avg_ms": None,
    }
    base.update(over)
    return base


def test_perfect_month_is_very_good_with_generic_recommendation():
    ev = build_evaluation("Agustus 2026", [_t()])
    assert ev["level"] == "Sangat baik"
    assert "100.00%" in ev["headline"]
    assert ev["recommendations"]


def test_incidents_are_reported_with_name_and_duration():
    ev = build_evaluation("Agustus 2026", [_t(uptime_pct=97.0, up_checks=970, incident_count=3,
                                             downtime_minutes=400.0, longest_incident_minutes=240.0)])
    text = " ".join(ev["findings"] + ev["recommendations"])
    assert ev["level"] == "Perlu perhatian"
    assert "Situs A" in text and "240" in text and "3" in text


def test_critical_level_below_95_percent():
    ev = build_evaluation("Agustus 2026", [_t(uptime_pct=90.0, up_checks=900)])
    assert ev["level"] == "Kritis"


def test_slower_and_faster_response_versus_previous_month():
    slow = build_evaluation("X", [_t(avg_rt=450.0, prev_avg_rt=300.0)])
    fast = build_evaluation("X", [_t(avg_rt=200.0, prev_avg_rt=300.0)])
    assert any("melambat" in f for f in slow["findings"])
    assert any("membaik" in f for f in fast["findings"])


def test_small_response_change_is_not_flagged():
    ev = build_evaluation("X", [_t(avg_rt=310.0, prev_avg_rt=300.0)])
    assert not any("melambat" in f or "membaik" in f for f in ev["findings"])


def test_high_anomaly_ratio_is_flagged():
    ev = build_evaluation("X", [_t(anomaly_count=50)])  # 5% dari 1000
    assert any("anomali" in f.lower() for f in ev["findings"])


def test_forecast_warning_when_expected_much_slower():
    ev = build_evaluation("X", [_t(avg_rt=300.0, forecast_avg_ms=450.0)])
    assert any("prediksi" in f.lower() for f in ev["findings"])
    assert any("kapasitas" in r.lower() or "beban" in r.lower() for r in ev["recommendations"])


def test_no_data_gets_explicit_evaluation_not_a_crash():
    ev = build_evaluation("X", [_t(total_checks=0, up_checks=0, uptime_pct=None, avg_rt=None, p95_rt=None)])
    assert ev["level"] == "Tidak ada data"
    assert build_evaluation("X", [])["level"] == "Tidak ada data"


def test_fleet_uptime_is_weighted_by_checks():
    big = _t("Besar", total_checks=9000, up_checks=9000, uptime_pct=100.0)
    small = _t("Kecil", total_checks=1000, up_checks=500, uptime_pct=50.0)
    ev = build_evaluation("X", [big, small])
    assert "95.00%" in ev["headline"]


def test_rendered_text_has_sections_and_respects_telegram_limit():
    ev = build_evaluation("Agustus 2026", [_t(uptime_pct=97.0, up_checks=970, incident_count=2,
                                             downtime_minutes=90.0, longest_incident_minutes=60.0)])
    text = render_evaluation_text("Agustus 2026", ev, narrative="Ringkasan AI." * 500)
    assert "Temuan" in text and "Rekomendasi" in text and "Analisis AI" in text
    assert len(text) <= 3900


def test_anonymize_removes_names_and_urls_and_can_be_reversed():
    payload, mapping = anonymize_targets([_t("Satsiber TNI"), _t("Rekrutmen TNI")])
    blob = json.dumps(payload)
    assert "Satsiber" not in blob and "Rekrutmen" not in blob and "rahasia.mil.id" not in blob
    assert sorted(mapping.values()) == ["Rekrutmen TNI", "Satsiber TNI"]
    label = next(iter(mapping))
    assert mapping[label] in deanonymize(f"{label} lambat", mapping)


def _mock_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_llm_narrative_sends_only_anonymised_numbers_and_parses_reply():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = request.headers
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"content": [{"type": "text", "text": "Target-A stabil."}]})

    payload, _ = anonymize_targets([_t("Satsiber TNI")])
    text = await llm_narrative(payload, "Agustus 2026", api_key="sk-test", model="claude-test", client=_mock_client(handler))

    assert text == "Target-A stabil."
    assert seen["headers"]["x-api-key"] == "sk-test"
    assert seen["headers"]["anthropic-version"]
    assert "Satsiber" not in seen["body"] and "rahasia.mil.id" not in seen["body"]
    assert json.loads(seen["body"])["model"] == "claude-test"


@pytest.mark.parametrize("response", [
    httpx.Response(500, json={"error": "x"}),
    httpx.Response(200, json={"content": []}),
    httpx.Response(200, text="bukan json"),
    httpx.Response(200, json={"content": [{"type": "text", "text": "   "}]}),
])
async def test_llm_narrative_returns_none_on_any_bad_response(response):
    client = _mock_client(lambda request: response)
    assert await llm_narrative([{"label": "Target-A"}], "X", api_key="k", model="m", client=client) is None


async def test_llm_narrative_returns_none_on_network_error():
    def boom(request):
        raise httpx.ConnectError("down")

    assert await llm_narrative([{"label": "Target-A"}], "X", api_key="k", model="m", client=_mock_client(boom)) is None


async def test_analyze_is_local_only_by_default(monkeypatch):
    monkeypatch.delenv("REPORT_AI_ENABLED", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-be-used")
    called = []
    client = _mock_client(lambda request: called.append(1) or httpx.Response(200, json={}))

    evaluation, narrative = await analyze("X", [_t()], client=client)

    assert evaluation["level"] == "Sangat baik"
    assert narrative is None and called == []


async def test_analyze_uses_llm_when_enabled_and_restores_real_names(monkeypatch):
    monkeypatch.setenv("REPORT_AI_ENABLED", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    def handler(request):
        return httpx.Response(200, json={"content": [{"type": "text", "text": "Target-A perlu dipantau."}]})

    evaluation, narrative = await analyze("X", [_t("Satsiber TNI")], client=_mock_client(handler))

    assert narrative == "Satsiber TNI perlu dipantau."


async def test_analyze_falls_back_silently_when_llm_fails(monkeypatch):
    monkeypatch.setenv("REPORT_AI_ENABLED", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    evaluation, narrative = await analyze("X", [_t()], client=_mock_client(lambda r: httpx.Response(503)))
    assert narrative is None and evaluation["level"] == "Sangat baik"


async def test_analyze_enabled_without_key_stays_local(monkeypatch):
    monkeypatch.setenv("REPORT_AI_ENABLED", "true")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _, narrative = await analyze("X", [_t()])
    assert narrative is None
