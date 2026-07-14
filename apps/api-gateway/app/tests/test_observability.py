"""Pure hop-math tests for observability.py's t0-t3 latency breakdown
(Spec 01 §4.4, Spec 04 §2 Phase 8) — no real Prometheus/OTLP backend
needed, `record_ptt_turn`'s histogram/span recording is in-memory unless
an exporter is configured, and its return value is exactly the millisecond
math this test pins.
"""
import uuid

import pytest

from app.services import observability
from app.services.observability import record_client_rtt, record_ptt_turn


def test_record_ptt_turn_computes_each_hop_leg_independently():
    session_id = uuid.uuid4()
    t0 = 1_000.000
    t1 = t0 + 0.020  # hop 3 proxy: 20ms
    t2 = t1 + 0.300  # hop 4+5 combined: 300ms
    t3 = t2 + 0.005  # hop 6 proxy: 5ms

    values = record_ptt_turn(session_id=session_id, t0=t0, t1=t1, t2=t2, t3=t3)

    assert values["gateway_to_gemini_send_ms"] == pytest.approx(20.0)
    assert values["gemini_response_ms"] == pytest.approx(300.0)
    assert values["gateway_relay_enqueue_ms"] == pytest.approx(5.0)
    assert values["ptt_release_to_first_audio_ms"] == pytest.approx(325.0)


def test_record_ptt_turn_total_equals_sum_of_hop_legs():
    session_id = uuid.uuid4()
    t0, t1, t2, t3 = 500.0, 500.05, 500.9, 500.91

    values = record_ptt_turn(session_id=session_id, t0=t0, t1=t1, t2=t2, t3=t3)

    hop_sum = (
        values["gateway_to_gemini_send_ms"]
        + values["gemini_response_ms"]
        + values["gateway_relay_enqueue_ms"]
    )
    assert values["ptt_release_to_first_audio_ms"] == pytest.approx(hop_sum)


def test_record_client_rtt_does_not_raise():
    # Recording is a pure side effect (an in-memory OTel histogram sample);
    # the only thing worth asserting without a real Prometheus scrape is
    # that it doesn't blow up on a legitimate value.
    record_client_rtt(session_id=uuid.uuid4(), rtt_ms=42.5)


def test_metrics_asgi_app_is_callable():
    app = observability.metrics_asgi_app()
    assert callable(app)
