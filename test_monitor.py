"""Tests for monitor.py. Run with: python3 -m unittest -v

Everything runs against a throwaway local server, so no internet is needed.
"""

import socket
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import requests

import monitor


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/slow":
            time.sleep(1)
        elif self.path == "/redirect-loop":
            self.send_response(302)
            self.send_header("Location", "/redirect-loop")
            self.end_headers()
            return
        status = {"/missing": 404, "/broken": 500}.get(self.path, 200)
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


def closed_port():
    """Return a local port with nothing listening on it."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class CheckUrlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_success_reports_code_and_time(self):
        result = monitor.check_url(f"{self.base}/")
        self.assertTrue(result.is_up)
        self.assertEqual(result.status_code, 200)
        self.assertGreater(result.response_ms, 0)
        self.assertIsNone(result.error)

    def test_client_and_server_errors_are_down_but_keep_the_code(self):
        for path, code in (("/missing", 404), ("/broken", 500)):
            with self.subTest(path=path):
                result = monitor.check_url(f"{self.base}{path}")
                self.assertFalse(result.is_up)
                self.assertEqual(result.status_code, code)

    def test_timeout(self):
        result = monitor.check_url(f"{self.base}/slow", timeout=0.1)
        self.assertFalse(result.is_up)
        self.assertEqual(result.error, "timed out after 0.1s")

    def test_connection_refused(self):
        result = monitor.check_url(f"http://127.0.0.1:{closed_port()}/")
        self.assertFalse(result.is_up)
        self.assertEqual(result.error, "connection refused")

    def test_dns_failure(self):
        # Faked at the resolver so it passes regardless of local DNS setup.
        failure = socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        with mock.patch("socket.getaddrinfo", side_effect=failure):
            result = monitor.check_url("http://sentinelops.invalid/")
        self.assertFalse(result.is_up)
        self.assertEqual(result.error, "DNS lookup failed")

    def test_redirect_loop(self):
        result = monitor.check_url(f"{self.base}/redirect-loop")
        self.assertFalse(result.is_up)
        self.assertEqual(result.error, "too many redirects")

    def test_invalid_urls_do_not_raise(self):
        for url in ("http://", "ftp://example.com", "https://"):
            with self.subTest(url=url):
                result = monitor.check_url(url)
                self.assertFalse(result.is_up)
                self.assertEqual(result.error, "invalid URL")

    def test_unexpected_request_error_does_not_raise(self):
        with mock.patch(
            "monitor.requests.get", side_effect=requests.exceptions.ChunkedEncodingError
        ):
            result = monitor.check_url("http://example.com")
        self.assertEqual(result.error, "request failed (ChunkedEncodingError)")

    def test_check_all_keeps_input_order(self):
        urls = [f"{self.base}/slow", f"{self.base}/", f"{self.base}/missing"]
        results = monitor.check_all(urls, timeout=5, workers=3)
        self.assertEqual([r.url for r in results], urls)


class HelperTests(unittest.TestCase):
    def test_normalize_url(self):
        self.assertEqual(monitor.normalize_url("example.com"), "https://example.com")
        self.assertEqual(monitor.normalize_url("http://a.io"), "http://a.io")

    def test_load_targets_skips_comments_and_blanks(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "targets.txt"
            path.write_text("# header\n\nhttps://a.io  # note\n  b.io\n")
            self.assertEqual(monitor.load_targets(path), ["https://a.io", "b.io"])

    def test_report_shows_up_down_and_summary(self):
        report = monitor.format_report(
            [
                monitor.CheckResult("https://a.io", True, 200, 12.3),
                monitor.CheckResult("https://b.io", False, error="DNS lookup failed"),
            ]
        )
        self.assertRegex(report, r"UP\s+200\s+12 ms\s+https://a\.io")
        self.assertRegex(report, r"DOWN\s+-\s+-\s+https://b\.io\s+DNS lookup failed")
        self.assertIn("1/2 up, 1 down", report)


class MainTests(unittest.TestCase):
    def run_main(self, argv):
        with mock.patch("builtins.print"):
            return monitor.main(argv)

    def test_exit_0_when_all_up(self):
        ok = monitor.CheckResult("https://a.io", True, 200, 1.0)
        with mock.patch("monitor.check_url", return_value=ok):
            self.assertEqual(self.run_main(["https://a.io"]), 0)

    def test_exit_1_when_any_down(self):
        bad = monitor.CheckResult("https://a.io", False, error="connection refused")
        with mock.patch("monitor.check_url", return_value=bad):
            self.assertEqual(self.run_main(["https://a.io"]), 1)

    def test_exit_2_when_file_missing(self):
        self.assertEqual(self.run_main(["-f", "/nonexistent/targets.txt"]), 2)

    def test_exit_2_when_file_has_no_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "targets.txt"
            path.write_text("# nothing here\n")
            self.assertEqual(self.run_main(["-f", str(path)]), 2)


if __name__ == "__main__":
    unittest.main()
