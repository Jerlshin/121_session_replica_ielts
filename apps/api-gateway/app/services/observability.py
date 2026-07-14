"""OpenTelemetry spans + metrics for the live Gemini relay's server-
observable latency legs (Spec 01 §4.4, Spec 04 §2 Phase 8).

Hops 1/2/7 (mic -> worklet, client<->gateway network, client jitter buffer)
happen in the browser and are not observable from here. What *is*
observable inside `ws_exam.py`'s activity_end / AudioDelta handling
collapses to four timestamps bracketing a single PTT turn:

    t0 -- gateway receives the `activity_end` control message
    t1 -- `bridge.send_activity_end()` returns (hop 3 proxy: gateway->Gemini write)
    t2 -- first AudioDelta arrives from `bridge.receive_events()`
          (hop 4 + hop 5 *combined* -- Gemini's own generation time and the
          Gemini->gateway network hop are not separable server-side; the
          `gemini_response_ms` histogram is documented as such rather than
          faking hop-level precision the platform doesn't have)
    t3 -- that delta is queued onto `outbox` (hop 6 proxy: gateway-internal
          handoff before `writer()` actually sends it to the client)

Client<->gateway RTT (a hop 2 proxy) can't be measured unilaterally from the
server -- there's no reply loop -- so it's *client-reported*: the client
sends `{type: "client_ping", client_ts}`, the gateway echoes
`{type: "pong", client_ts, server_ts}`, the client computes its own RTT and
reports it back via `{type: "rtt_report", rtt_ms}`, which lands in
`client_gateway_rtt_ms` via `record_client_rtt`. This is scoped purely to
measurement -- it does not resurrect Spec 01 §5.4's full disconnect/pause
heartbeat policy.

Exporter selection: metrics always export via `PrometheusMetricReader`
(scraped at `/metrics`, see `metrics_asgi_app()`); if
`OTEL_EXPORTER_OTLP_ENDPOINT` is set, metrics *and* spans additionally
export via OTLP to a real collector. Without that env var, spans are only
exported to the console when `OTEL_CONSOLE_EXPORT` is set -- otherwise
spans are still created (harmless, in-memory, matches the tracer API
contract) but go nowhere, so dev/CI never needs a collector running.
"""

from __future__ import annotations

import os
import uuid

from opentelemetry import metrics, trace
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SimpleSpanProcessor

_SERVICE_NAME = "ielts-api-gateway"
_resource = Resource.create({"service.name": _SERVICE_NAME})


def _build_tracer_provider() -> TracerProvider:
    provider = TracerProvider(resource=_resource)
    otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint)))
    elif os.environ.get("OTEL_CONSOLE_EXPORT"):
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    return provider


def _build_meter_provider() -> MeterProvider:
    readers = [PrometheusMetricReader()]
    otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

        readers.append(PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=otlp_endpoint)))
    return MeterProvider(resource=_resource, metric_readers=readers)


trace.set_tracer_provider(_build_tracer_provider())
metrics.set_meter_provider(_build_meter_provider())

tracer = trace.get_tracer("ielts.gateway")
_meter = metrics.get_meter("ielts.gateway")

gateway_to_gemini_send_ms = _meter.create_histogram(
    "gateway_to_gemini_send_ms",
    unit="ms",
    description="Hop 3 proxy (Spec 01 §4.4): gateway -> Gemini Live WS send latency.",
)
gemini_response_ms = _meter.create_histogram(
    "gemini_response_ms",
    unit="ms",
    description=(
        "Hop 4 + hop 5 combined (not separable server-side): Gemini "
        "generation time plus the Gemini->gateway network hop."
    ),
)
gateway_relay_enqueue_ms = _meter.create_histogram(
    "gateway_relay_enqueue_ms",
    unit="ms",
    description="Hop 6 proxy: gateway-internal handoff from receiving an AudioDelta to queuing it for the writer.",
)
ptt_release_to_first_audio_ms = _meter.create_histogram(
    "ptt_release_to_first_audio_ms",
    unit="ms",
    description=(
        "Total server-observable leg of the PTT-release -> first-audible-"
        "reply budget (hops 3-6 only; excludes client-side hops 1/2/7)."
    ),
)
client_gateway_rtt_ms = _meter.create_histogram(
    "client_gateway_rtt_ms",
    unit="ms",
    description="Client-reported RTT from a client_ping/pong exchange (hop 2 proxy; self-reported).",
)


def _ns(t: float) -> int:
    return int(t * 1_000_000_000)


def record_ptt_turn(
    *, session_id: uuid.UUID, t0: float, t1: float, t2: float, t3: float
) -> dict[str, float]:
    """Records all four hop histograms plus an `exam.ptt_turn` span (with
    per-hop child spans) for one PTT turn. `t0`..`t3` are `time.time()`
    epoch-second timestamps captured across `ws_exam.py`'s activity_end
    handling and first-AudioDelta handling -- two different coroutines, so
    spans are built after the fact via explicit start_time/end_time rather
    than a live `with` block. Returns the computed millisecond values so
    the caller can also log them without recomputing.
    """
    attrs = {"session_id": str(session_id)}
    values = {
        "gateway_to_gemini_send_ms": (t1 - t0) * 1000,
        "gemini_response_ms": (t2 - t1) * 1000,
        "gateway_relay_enqueue_ms": (t3 - t2) * 1000,
        "ptt_release_to_first_audio_ms": (t3 - t0) * 1000,
    }
    gateway_to_gemini_send_ms.record(values["gateway_to_gemini_send_ms"], attrs)
    gemini_response_ms.record(values["gemini_response_ms"], attrs)
    gateway_relay_enqueue_ms.record(values["gateway_relay_enqueue_ms"], attrs)
    ptt_release_to_first_audio_ms.record(values["ptt_release_to_first_audio_ms"], attrs)

    parent = tracer.start_span("exam.ptt_turn", start_time=_ns(t0), attributes=attrs)
    parent_ctx = trace.set_span_in_context(parent)
    for name, start, end in (
        ("gateway_to_gemini_send", t0, t1),
        ("gemini_response", t1, t2),
        ("gateway_relay_enqueue", t2, t3),
    ):
        child = tracer.start_span(name, context=parent_ctx, start_time=_ns(start))
        child.end(end_time=_ns(end))
    parent.end(end_time=_ns(t3))

    return values


def record_client_rtt(*, session_id: uuid.UUID, rtt_ms: float) -> None:
    client_gateway_rtt_ms.record(rtt_ms, {"session_id": str(session_id)})


def metrics_asgi_app():
    """Prometheus scrape target (`/metrics`) -- imported lazily so a bare
    `import observability` never requires `prometheus_client` to already
    have the default registry populated by something else."""
    from prometheus_client import make_asgi_app

    return make_asgi_app()
