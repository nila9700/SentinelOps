"""Tests for the layered checks. Uses a local server, so no internet is needed."""

import socket
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

import diagnostics
from diagnostics import DEGRADED, DOWN, FAIL, OK, OPERATIONAL, SKIPPED, WARN


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/hang":
            time.sleep(1)
        if self.path == "/loop":
            self.send_response(302)
            self.send_header("Location", "/loop")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path == "/to-private":
            self.send_response(302)
            self.send_header("Location", "http://10.0.0.1/secret")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        code = {"/missing": 404, "/broken": 500, "/forbidden": 403}.get(self.path, 200)
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


class CheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.delay = mock.patch("diagnostics.RETRY_DELAY", 0)
        cls.delay.start()

    @classmethod
    def tearDownClass(cls):
        cls.delay.stop()
        cls.server.shutdown()
        cls.server.server_close()

    def check(self, path, **kwargs):
        return diagnostics.check_component("Site", f"{self.base}{path}", allow_private=True, **kwargs)

    def states(self, result):
        return {step.key: step.state for step in result.steps}

    def test_healthy_site(self):
        result = self.check("/")
        self.assertEqual(result.state, OPERATIONAL)
        self.assertEqual(result.status_code, 200)
        self.assertIsNone(result.fault)
        self.assertEqual(self.states(result)["tls"], SKIPPED)

    def test_every_step_is_always_reported(self):
        self.assertEqual([s.key for s in self.check("/").steps], list(diagnostics.STEP_LABELS))

    def test_server_error_is_down(self):
        result = self.check("/broken")
        self.assertEqual((result.state, result.fault), (DOWN, "server_error"))
        self.assertEqual(result.status_code, 500)

    def test_missing_page_is_degraded(self):
        result = self.check("/missing")
        self.assertEqual((result.state, result.fault), (DEGRADED, "client_error"))

    def test_refusing_bots_is_not_an_outage(self):
        result = self.check("/forbidden")
        self.assertEqual(result.state, OPERATIONAL)
        self.assertEqual(result.status_code, 403)

    def test_timeout(self):
        result = self.check("/hang", timeout=0.1)
        self.assertEqual((result.state, result.fault), (DOWN, "server_error"))

    def test_redirect_loop(self):
        result = self.check("/loop")
        self.assertEqual((result.state, result.fault), (DOWN, "client_error"))

    def test_slow_site_is_degraded(self):
        with mock.patch("diagnostics.SLOW_MS", 0):
            result = self.check("/")
        self.assertEqual((result.state, result.fault), (DEGRADED, "slow"))

    def test_connection_refused_skips_later_steps(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        result = diagnostics.check_component("Site", f"http://127.0.0.1:{port}", allow_private=True)
        self.assertEqual((result.state, result.fault), (DOWN, "connection"))
        states = self.states(result)
        self.assertEqual(states["connect"], FAIL)
        self.assertEqual(states["http"], SKIPPED)

    def test_dns_failure(self):
        error = socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        with mock.patch("socket.getaddrinfo", side_effect=error):
            result = diagnostics.check_component("Site", "https://nope.invalid")
        self.assertEqual((result.state, result.fault), (DOWN, "dns"))

    def test_private_addresses_are_blocked(self):
        result = diagnostics.check_component("Site", f"{self.base}/")
        self.assertEqual((result.state, result.fault), (DOWN, "blocked"))

    def test_redirect_to_private_address_is_blocked(self):
        with self.assertRaises(diagnostics.BlockedRedirect):
            diagnostics.fetch(f"{self.base}/to-private", 2, allow_private=False)


class FaultTests(unittest.TestCase):
    def healthy(self):
        keys = diagnostics.STEP_LABELS
        steps = [diagnostics.make_step(key, OK, "Fine.") for key in keys]
        result = diagnostics.ComponentResult("Site", "https://a.io", OPERATIONAL, steps, 200, 100.0)
        return diagnostics.summarize(result)

    def test_each_fault_fails_at_its_own_step(self):
        for fault, (key, _, _) in diagnostics.FAULTS.items():
            with self.subTest(fault=fault):
                result = diagnostics.apply_fault(self.healthy(), fault)
                self.assertEqual(result.fault, fault)
                step = next(s for s in result.steps if s.key == key)
                self.assertEqual(step.state, WARN if fault == "slow" else FAIL)
                self.assertEqual(len(result.steps), len(diagnostics.STEP_LABELS))

    def test_slow_is_degraded_not_down(self):
        self.assertEqual(diagnostics.apply_fault(self.healthy(), "slow").state, DEGRADED)

    def test_overall_status(self):
        ok = self.healthy()
        down = diagnostics.apply_fault(self.healthy(), "dns")
        slow = diagnostics.apply_fault(self.healthy(), "slow")
        self.assertEqual(diagnostics.overall_status([ok, ok]), "operational")
        self.assertEqual(diagnostics.overall_status([ok, slow]), "degraded")
        self.assertEqual(diagnostics.overall_status([ok, down]), "partial")
        self.assertEqual(diagnostics.overall_status([down, down]), "down")


if __name__ == "__main__":
    unittest.main()
