from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from contextlib import nullcontext
import json
import os
import signal
import sys
from time import perf_counter
from urllib.parse import urlsplit

from prometheus_client import Counter, Histogram, start_http_server
from opentelemetry import trace
from opentelemetry.trace import SpanKind
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter


tracer = trace.get_tracer(__name__)


def configure_tracing():
    if os.environ.get("OTEL_SDK_DISABLED", "").lower() == "true":
        return None
    if not (os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or
            os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")):
        return None
    provider = TracerProvider(resource=Resource.create())
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    return provider


REQUESTS = Counter(
    "app_http_requests_total", "HTTP requests, excluding health probes.", ["route", "status"]
)
REQUEST_DURATION = Histogram(
    "app_http_request_duration_seconds", "HTTP request duration, excluding health probes.", ["route"]
)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        entry = {"message": format % args}
        context = trace.get_current_span().get_span_context()
        if context.is_valid:
            entry["trace_id"] = f"{context.trace_id:032x}"
            entry["span_id"] = f"{context.span_id:016x}"
        sys.stderr.write(json.dumps(entry) + "\n")

    def do_GET(self):
        started = perf_counter()
        path = urlsplit(self.path).path
        route = path if path in ("/", "/healthz") else "unmatched"
        if path == "/healthz":
            status, body = 200, b"ok\n"
        elif path == "/":
            status, body = 200, (os.environ.get("MESSAGE", "blackstorm example v2") + "\n").encode()
        else:
            status, body = 404, b"not found\n"

        attributes = {"http.request.method": "GET", "http.response.status_code": status}
        if route != "unmatched":
            attributes["http.route"] = route
        span = nullcontext() if route == "/healthz" else tracer.start_as_current_span(
            "GET" if route == "unmatched" else f"GET {route}",
            context=TraceContextTextMapPropagator().extract(self.headers),
            kind=SpanKind.SERVER,
            attributes=attributes,
        )
        with span:
            try:
                self.send_response(status)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            finally:
                if route != "/healthz":
                    REQUESTS.labels(route=route, status=str(status)).inc()
                    REQUEST_DURATION.labels(route=route).observe(perf_counter() - started)


if __name__ == "__main__":
    def stop(signum, frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    provider = configure_tracing()
    port = int(os.environ.get("PORT", "8080"))
    start_http_server(int(os.environ.get("METRICS_PORT", "9090")))
    try:
        with ThreadingHTTPServer(("0.0.0.0", port), Handler) as server:
            server.serve_forever()
    finally:
        if provider is not None:
            provider.shutdown()
