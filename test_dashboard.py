"""Tests for the HTTP API. Runs a real server with fake site checks."""

import http.client
import json
import threading
import unittest

import dashboard
from catalog import Catalog
from engine import Engine
from store import Store
from test_engine import healthy


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.engine = Engine(self.store, Catalog(), check=healthy)
        self.server = dashboard.create_server(0, self.engine)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.store.close)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection(dashboard.HOST, self.port, timeout=5)
        sent = dict(headers or {})
        payload = None
        if body is not None:
            payload = json.dumps(body) if not isinstance(body, (bytes, str)) else body
            sent.setdefault("Content-Type", "application/json")
        connection.request(method, path, payload, sent)
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response, data

    def api(self, method, path, body=None):
        response, data = self.request(method, path, body)
        return response.status, json.loads(data)

    def test_frontend_files_are_served(self):
        for path, kind in (("/", "text/html"), ("/static/app.js", "text/javascript"),
                           ("/static/app.css", "text/css"), ("/static/favicon.svg", "image/svg+xml")):
            with self.subTest(path=path):
                response, data = self.request("GET", path)
                self.assertEqual(response.status, 200)
                self.assertTrue(response.getheader("Content-Type").startswith(kind))
                self.assertTrue(data)
                self.assertIn("default-src 'self'", response.getheader("Content-Security-Policy"))

    def test_only_listed_files_are_served(self):
        for path in ("/static/../dashboard.py", "/dashboard.py", "/data/sentinelops.db", "/static/"):
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path)[0].status, 404)

    def test_search_then_view(self):
        status, service = self.api("POST", "/api/check", {"query": "Spotify"})
        self.assertEqual(status, 200)
        self.assertEqual(service["name"], "Spotify")
        self.assertEqual(service["status"], "operational")
        status, overview = self.api("GET", "/api/overview")
        self.assertEqual([s["name"] for s in overview["services"]], ["Spotify"])

    def test_failed_search_is_a_friendly_error(self):
        status, body = self.api("POST", "/api/check", {"query": "Spotfy"})
        self.assertEqual(status, 422)
        self.assertEqual(body["suggestions"], ["Spotify"])

    def test_bad_requests(self):
        cases = [
            ("POST", "/api/check", b"{not json", 400),
            ("POST", "/api/check", b"[1]", 400),
            ("POST", "/api/check", {"query": 5}, 400),
            ("GET", "/api/services/999", None, 404),
            ("GET", "/api/services/99999999999999999999", None, 404),
            ("GET", "/api/incidents/999", None, 404),
            ("POST", "/api/nope", {}, 404),
        ]
        for method, path, body, expected in cases:
            with self.subTest(path=path, body=body):
                self.assertEqual(self.request(method, path, body)[0].status, expected)

    def test_oversized_body_is_refused(self):
        self.assertEqual(self.request("POST", "/api/check", b"x" * 9000)[0].status, 413)

    def test_form_posts_are_refused(self):
        response, _ = self.request(
            "POST", "/api/check", b"query=Spotify", {"Content-Type": "text/plain"}
        )
        self.assertEqual(response.status, 415)

    def test_foreign_host_header_is_refused(self):
        response, _ = self.request("GET", "/api/overview", headers={"Host": "evil.example"})
        self.assertEqual(response.status, 403)

    def test_demo_outage_flow(self):
        _, service = self.api("POST", "/api/check", {"query": "GitHub"})
        path = f"/api/services/{service['id']}"

        status, view = self.api("POST", f"{path}/simulate", {"fault": "tls", "component": "API"})
        self.assertEqual(status, 200)
        self.assertEqual(view["status"], "partial")
        [ticket] = view["incidents"]
        self.assertEqual(ticket["department_name"], "Security Operations")
        self.assertTrue(ticket["simulated"])

        status, started = self.api("POST", f"/api/incidents/{ticket['id']}/start", {})
        self.assertEqual(started["status"], "in_progress")
        status, _ = self.api("POST", f"/api/incidents/{ticket['id']}/start", {})
        self.assertEqual(status, 400)

        status, view = self.api("DELETE", f"{path}/simulate")
        self.assertEqual(view["status"], "operational")
        status, closed = self.api("GET", f"/api/incidents/{ticket['id']}")
        self.assertEqual(closed["status"], "resolved")

    def test_remove_service(self):
        _, service = self.api("POST", "/api/check", {"query": "GitHub"})
        status, _ = self.api("DELETE", f"/api/services/{service['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(self.api("GET", f"/api/services/{service['id']}")[0], 404)

    def test_teams_and_directory(self):
        self.assertEqual(len(self.api("GET", "/api/teams")[1]["teams"]), 5)
        self.assertGreater(len(self.api("GET", "/api/directory")[1]["companies"]), 30)


if __name__ == "__main__":
    unittest.main()
