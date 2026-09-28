from http.server import ThreadingHTTPServer
from threading import Thread
import io
import json
import os
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app import Handler
from prometheus_client import start_http_server
from prometheus_client.parser import text_string_to_metric_families
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode


class HTTPTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"
        cls.metrics_server, cls.metrics_thread = start_http_server(0, addr="127.0.0.1")
        cls.metrics_url = f"http://127.0.0.1:{cls.metrics_server.server_port}/metrics"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.metrics_server.shutdown()
        cls.metrics_server.server_close()
        cls.metrics_thread.join()

    def test_homepage(self):
        with urlopen(f"{self.base_url}/") as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), b"blackstorm example v2\n")

    def test_configured_message(self):
        with patch.dict(os.environ, {"MESSAGE": "config from environment"}):
            with urlopen(f"{self.base_url}/") as response:
                self.assertEqual(response.read(), b"config from environment\n")

    def test_health(self):
        with urlopen(f"{self.base_url}/healthz") as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), b"ok\n")

    def test_unknown_path(self):
        with self.assertRaises(HTTPError) as error:
            urlopen(f"{self.base_url}/missing")
        self.assertEqual(error.exception.code, 404)

    def test_metrics(self):
        with urlopen(f"{self.base_url}/?customer=123") as response:
            self.assertEqual(response.status, 200)
            response.read()
        for path in ("/missing/123", "/missing/456", "/metrics"):
            with self.assertRaises(HTTPError) as error:
                urlopen(f"{self.base_url}{path}")
            self.assertEqual(error.exception.code, 404)
        with urlopen(f"{self.base_url}/healthz") as response:
            response.read()

        # Request accounting finishes after the response body has been sent.
        deadline = time.monotonic() + 2
        while True:
            with urlopen(self.metrics_url) as response:
                self.assertEqual(response.status, 200)
                self.assertIn("text/plain", response.headers["Content-Type"])
                body = response.read().decode()
            samples = [s for family in text_string_to_metric_families(body) for s in family.samples]
            requests = {
                (s.labels["route"], s.labels["status"]): s.value
                for s in samples if s.name == "app_http_requests_total"
            }
            durations = {
                s.labels["route"]: s.value
                for s in samples if s.name == "app_http_request_duration_seconds_count"
            }
            if requests.get(("unmatched", "404"), 0) >= 3 and durations.get("unmatched", 0) >= 3:
                break
            if time.monotonic() >= deadline:
                self.fail("Metrics did not account for the HTTP requests")
            time.sleep(0.01)

        self.assertGreaterEqual(requests[("/", "200")], 1)
        self.assertGreaterEqual(durations["/"], 1)
        self.assertEqual(set(requests), {("/", "200"), ("unmatched", "404")})
        self.assertEqual(set(durations), {"/", "unmatched"})

    def test_traces_propagate_context_and_exclude_health(self):
        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        trace_id = "0123456789abcdef0123456789abcdef"
        parent_id = "0123456789abcdef"
        logs = io.StringIO()
        try:
            with patch("app.tracer", provider.get_tracer("test")), patch("app.sys.stderr", logs):
                request = Request(f"{self.base_url}/?token=not-for-telemetry", headers={
                    "traceparent": f"00-{trace_id}-{parent_id}-01",
                })
                with urlopen(request) as response:
                    response.read()
                with urlopen(f"{self.base_url}/healthz") as response:
                    response.read()
                with self.assertRaises(HTTPError):
                    urlopen(f"{self.base_url}/missing/unique-id")

                deadline = time.monotonic() + 2
                while len(exporter.get_finished_spans()) < 2:
                    if time.monotonic() >= deadline:
                        self.fail("HTTP spans were not exported")
                    time.sleep(0.01)

            spans = exporter.get_finished_spans()
            self.assertEqual(len(spans), 2)
            home = next(s for s in spans if s.name == "GET /")
            self.assertEqual(home.context.trace_id, int(trace_id, 16))
            self.assertEqual(home.parent.span_id, int(parent_id, 16))
            self.assertEqual(home.kind, SpanKind.SERVER)
            self.assertEqual(home.attributes["http.response.status_code"], 200)
            self.assertEqual(home.attributes["http.route"], "/")
            self.assertGreaterEqual(home.end_time, home.start_time)
            entries = [json.loads(line) for line in logs.getvalue().splitlines()]
            self.assertEqual(len(entries), 3)
            home_log = next(e for e in entries if e.get("trace_id") == trace_id)
            self.assertEqual(home_log["span_id"], f"{home.context.span_id:016x}")
            health_log = next(e for e in entries if "/healthz" in e["message"])
            self.assertNotIn("trace_id", health_log)
            self.assertNotIn("span_id", health_log)
            missing = next(s for s in spans if s.name == "GET")
            self.assertEqual(missing.attributes["http.response.status_code"], 404)
            self.assertNotIn("http.route", missing.attributes)
            self.assertEqual(missing.status.status_code, StatusCode.UNSET)
            for span in spans:
                self.assertNotIn("not-for-telemetry", str(span.attributes))
                self.assertNotIn("unique-id", str(span.attributes))
        finally:
            provider.shutdown()


if __name__ == "__main__":
    unittest.main()
