"""OpenTelemetry instrumentation for the Spousal Approval System.

Implements the OpenTelemetry **GenAI semantic conventions** so every AI
operation in the pipeline — speech-to-text (Groq Whisper), the scoring /
persona LLM calls (Groq LLaMA), and neural text-to-speech (edge-tts) — shows
up as a properly attributed span, with token-usage and latency metrics.

Zero-config by default: spans and metrics print to the console. If the
standard ``OTEL_EXPORTER_OTLP_ENDPOINT`` environment variable is set, the same
telemetry is shipped to any OTLP backend (Jaeger, Grafana Tempo, Honeycomb,
Grafana Cloud, …) instead — no code change required.

The whole module degrades to no-ops if the OpenTelemetry packages are missing,
so the app never crashes just because observability isn't installed.
"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from typing import Any, Iterator

# ── Optional-dependency guard ─────────────────────────────────────────────────
# If OpenTelemetry isn't installed we fall back to a no-op implementation so the
# app keeps working. Install with:  pip install -r requirements.txt
try:
    from opentelemetry import trace, metrics
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import (
        ConsoleMetricExporter,
        PeriodicExportingMetricReader,
    )
    from opentelemetry.trace import Status, StatusCode

    _OTEL_AVAILABLE = True
except ImportError:  # pragma: no cover - only hit when deps are absent
    _OTEL_AVAILABLE = False


# ── GenAI semantic-convention attribute keys ──────────────────────────────────
# Mirrors https://opentelemetry.io/docs/specs/semconv/gen-ai/ so backends that
# understand the GenAI conventions (Jaeger, Honeycomb, Grafana) light up.
GEN_AI_SYSTEM = "gen_ai.system"
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_REQUEST_TEMPERATURE = "gen_ai.request.temperature"
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
GEN_AI_RESPONSE_ID = "gen_ai.response.id"
GEN_AI_RESPONSE_FINISH_REASONS = "gen_ai.response.finish_reasons"
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
GEN_AI_CONVERSATION_ID = "gen_ai.conversation.id"

SERVICE_NAME = "spousal-approval-system"

# Module-level handles, populated by init_telemetry().
_initialized = False
_tracer: Any = None
_token_usage_hist: Any = None
_op_duration_hist: Any = None
_tts_bytes_hist: Any = None


def _build_exporters():
    """Pick exporters based on the environment.

    OTLP if OTEL_EXPORTER_OTLP_ENDPOINT is set (the standard OTel switch),
    otherwise console. Returns (span_exporter, metric_exporter).
    """
    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        # Lazily imported so the OTLP exporter package is only required when
        # someone actually points at a collector/backend.
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
            OTLPMetricExporter,
        )

        return OTLPSpanExporter(), OTLPMetricExporter()

    return ConsoleSpanExporter(), ConsoleMetricExporter()


def init_telemetry() -> None:
    """Initialise tracing + metrics. Idempotent and safe under Streamlit reruns.

    Streamlit re-executes the whole script on every interaction, so this guards
    against re-registering global providers (which OTel warns about).
    """
    global _initialized, _tracer
    global _token_usage_hist, _op_duration_hist, _tts_bytes_hist

    if _initialized or not _OTEL_AVAILABLE:
        _initialized = True
        return

    resource = Resource.create(
        {
            "service.name": SERVICE_NAME,
            "service.version": os.environ.get("APP_VERSION", "1.0.0"),
            "deployment.environment": os.environ.get("APP_ENV", "development"),
        }
    )

    span_exporter, metric_exporter = _build_exporters()

    # ── Traces ────────────────────────────────────────────────────────────
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
    trace.set_tracer_provider(tracer_provider)
    _tracer = trace.get_tracer(SERVICE_NAME)

    # ── Metrics ───────────────────────────────────────────────────────────
    reader = PeriodicExportingMetricReader(metric_exporter, export_interval_millis=15000)
    meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
    metrics.set_meter_provider(meter_provider)
    meter = metrics.get_meter(SERVICE_NAME)

    _token_usage_hist = meter.create_histogram(
        name="gen_ai.client.token.usage",
        unit="{token}",
        description="Number of tokens used per GenAI request, by token type.",
    )
    _op_duration_hist = meter.create_histogram(
        name="gen_ai.client.operation.duration",
        unit="s",
        description="Duration of GenAI operations (STT / chat / TTS).",
    )
    _tts_bytes_hist = meter.create_histogram(
        name="tts.audio.bytes",
        unit="By",
        description="Size of synthesized neural-TTS audio.",
    )

    _initialized = True


# ── Instrumentation helpers ──────────────────────────────────────────────────


@contextmanager
def genai_span(
    operation: str,
    model: str,
    *,
    system: str = "groq",
    temperature: float | None = None,
    conversation_id: str | None = None,
    extra_attrs: dict | None = None,
) -> Iterator[Any]:
    """Context manager for a GenAI operation span (chat / transcribe / etc.).

    Yields the active span (or None when OTel is unavailable). Records latency
    against ``gen_ai.client.operation.duration`` on exit and marks the span
    errored if the body raises. Callers should attach response details (token
    counts, response id) via :func:`record_llm_response`.

    Span name follows the convention ``{operation} {model}``.
    """
    if not _OTEL_AVAILABLE or _tracer is None:
        yield None
        return

    attrs = {
        GEN_AI_SYSTEM: system,
        GEN_AI_OPERATION_NAME: operation,
        GEN_AI_REQUEST_MODEL: model,
    }
    if temperature is not None:
        attrs[GEN_AI_REQUEST_TEMPERATURE] = temperature
    if conversation_id:
        attrs[GEN_AI_CONVERSATION_ID] = conversation_id
    if extra_attrs:
        attrs.update(extra_attrs)

    start = time.perf_counter()
    with _tracer.start_as_current_span(f"{operation} {model}", attributes=attrs) as span:
        try:
            yield span
        except Exception as exc:
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            span.record_exception(exc)
            raise
        finally:
            duration = time.perf_counter() - start
            if _op_duration_hist is not None:
                _op_duration_hist.record(
                    duration,
                    {
                        GEN_AI_OPERATION_NAME: operation,
                        GEN_AI_REQUEST_MODEL: model,
                        GEN_AI_SYSTEM: system,
                    },
                )


def record_llm_response(span: Any, response: Any, *, model: str, system: str = "groq") -> None:
    """Attach response-side GenAI attributes + token metrics from a Groq/OpenAI
    -shaped chat completion response to ``span``."""
    if span is None or not _OTEL_AVAILABLE:
        return

    response_model = getattr(response, "model", None) or model
    response_id = getattr(response, "id", None)
    if response_model:
        span.set_attribute(GEN_AI_RESPONSE_MODEL, response_model)
    if response_id:
        span.set_attribute(GEN_AI_RESPONSE_ID, response_id)

    choices = getattr(response, "choices", None) or []
    finish_reasons = [c.finish_reason for c in choices if getattr(c, "finish_reason", None)]
    if finish_reasons:
        span.set_attribute(GEN_AI_RESPONSE_FINISH_REASONS, finish_reasons)

    usage = getattr(response, "usage", None)
    if usage is not None:
        input_tokens = getattr(usage, "prompt_tokens", None)
        output_tokens = getattr(usage, "completion_tokens", None)
        if input_tokens is not None:
            span.set_attribute(GEN_AI_USAGE_INPUT_TOKENS, input_tokens)
            _record_tokens(input_tokens, "input", model, system)
        if output_tokens is not None:
            span.set_attribute(GEN_AI_USAGE_OUTPUT_TOKENS, output_tokens)
            _record_tokens(output_tokens, "output", model, system)

        # Reasoning ("thinking") tokens — the main driver of token burn on
        # hybrid reasoning models. Groq nests these under completion_tokens_details.
        details = getattr(usage, "completion_tokens_details", None)
        reasoning_tokens = getattr(details, "reasoning_tokens", None) if details else None
        if reasoning_tokens:
            span.set_attribute("gen_ai.usage.reasoning_tokens", reasoning_tokens)
            _record_tokens(reasoning_tokens, "reasoning", model, system)


def _record_tokens(count: int, token_type: str, model: str, system: str) -> None:
    if _token_usage_hist is not None:
        _token_usage_hist.record(
            count,
            {
                "gen_ai.token.type": token_type,
                GEN_AI_REQUEST_MODEL: model,
                GEN_AI_SYSTEM: system,
            },
        )


def record_tts_audio(span: Any, *, num_bytes: int, voice: str) -> None:
    """Record synthesized-audio size for a TTS span + the tts.audio.bytes metric."""
    if span is not None and _OTEL_AVAILABLE:
        span.set_attribute("tts.audio.bytes", num_bytes)
        span.set_attribute("tts.voice", voice)
    if _tts_bytes_hist is not None:
        _tts_bytes_hist.record(num_bytes, {"tts.voice": voice})


@contextmanager
def app_span(name: str, attributes: dict | None = None) -> Iterator[Any]:
    """Generic application span for non-GenAI work (stage transitions, etc.)."""
    if not _OTEL_AVAILABLE or _tracer is None:
        yield None
        return
    with _tracer.start_as_current_span(name, attributes=attributes or {}) as span:
        yield span
