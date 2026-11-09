#!/usr/bin/env python3
"""SentinelOps web app: serves the JSON API and the frontend."""

import argparse
import json
import re
import sys
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from catalog import Catalog, LookupFailed
from engine import Engine, NotFound
from store import Store

HOST = "127.0.0.1"  # Local only: the app isn't exposed to the network.
DEFAULT_PORT = 8000
BASE_DIR = Path(__file__).parent
DEFAULT_DB = BASE_DIR / "data" / "sentinelops.db"
MAX_BODY = 8 * 1024

# Only these files are served, so a crafted path can't reach anything else.
STATIC_FILES = {
    "/": ("index.html", "text/html"),
    "/static/app.css": ("app.css", "text/css"),
    "/static/app.js": ("app.js", "text/javascript"),
    "/static/favicon.svg": ("favicon.svg", "image/svg+xml"),
}
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; base-uri 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}
FAULT_LABELS = {
    "dns": "Website address can't be found",
    "connection": "Server stops answering",
    "tls": "Security certificate is invalid",
    "server_error": "Server returns errors",
    "slow": "Website becomes slow",
}


class ApiError(Exception):
    def __init__(self, status, message, suggestions=()):
        super().__init__(message)
        self.status = status
        self.message = message
        self.suggestions = list(suggestions)


class Handler(BaseHTTPRequestHandler):
    @property
    def engine(self) -> Engine:
        return self.server.engine

    # Routing

    def handle_request(self, method):
        path = self.path.split("?", 1)[0]
        try:
            if not self.host_allowed():
                raise ApiError(403, "Requests must use the local address.")
            for route_method, pattern, handler in ROUTES:
                match = pattern.fullmatch(path)
                if match and route_method == method:
                    self.send_json(200, handler(self, *match.groups()))
                    return
            if method == "GET" and path in STATIC_FILES:
                self.send_static(path)
                return
            raise ApiError(404, "Not found.")
        except ApiError as exc:
            self.send_json(exc.status, {"error": exc.message, "suggestions": exc.suggestions})
        except LookupFailed as exc:
            self.send_json(422, {"error": exc.message, "suggestions": exc.suggestions})
        except NotFound as exc:
            self.send_json(404, {"error": str(exc), "suggestions": []})
        except ValueError as exc:
            self.send_json(400, {"error": str(exc), "suggestions": []})
        except (BrokenPipeError, ConnectionResetError):
            pass  # The browser went away.
        except Exception:
            traceback.print_exc()
            self.send_json(500, {"error": "Something went wrong.", "suggestions": []})

    def do_GET(self):
        self.handle_request("GET")

    def do_POST(self):
        self.handle_request("POST")

    def do_DELETE(self):
        self.handle_request("DELETE")

    def host_allowed(self) -> bool:
        """Reject other sites reaching this local app through DNS rebinding."""
        host = self.headers.get("Host", "").rsplit(":", 1)[0]
        return host in (HOST, "localhost")

    def read_json(self) -> dict:
        # A JSON content type forces a CORS preflight, which this server never
        # approves, so other websites can't post to it from a visitor's browser.
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            raise ApiError(415, "Send JSON.")
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            raise ApiError(400, "Invalid request.") from None
        if length < 0 or length > MAX_BODY:
            raise ApiError(413, "Request is too large.")
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            raise ApiError(400, "Invalid JSON.") from None
        if not isinstance(body, dict):
            raise ApiError(400, "Invalid JSON.")
        return body

    # Responses

    def send_body(self, status, content_type, data):
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, status, payload):
        self.send_body(status, "application/json", json.dumps(payload).encode("utf-8"))

    def send_static(self, path):
        name, content_type = STATIC_FILES[path]
        self.send_body(200, content_type, (BASE_DIR / "static" / name).read_bytes())

    def log_message(self, *args):
        pass

    # API

    def api_overview(self):
        return {
            "services": self.engine.list_views(),
            "active_incidents": [
                self.engine.incident_view(i)
                for i in self.engine.store.list_incidents(active=True)
            ],
        }

    def api_directory(self):
        return {"companies": self.engine.catalog.listing()}

    def api_check(self):
        query = self.read_json().get("query")
        if not isinstance(query, str):
            raise ApiError(400, "Type a company name or website address.")
        return self.engine.detail_view(self.engine.lookup(query))

    def api_service(self, service_id):
        return self.engine.detail_view(int(service_id))

    def api_recheck(self, service_id):
        self.engine.check_service(int(service_id))
        return self.engine.detail_view(int(service_id))

    def api_remove(self, service_id):
        self.engine.remove_service(int(service_id))
        return {"removed": True}

    def api_simulate(self, service_id):
        body = self.read_json()
        self.engine.simulate(int(service_id), body.get("fault"), body.get("component"))
        return self.engine.detail_view(int(service_id))

    def api_end_simulation(self, service_id):
        self.engine.end_simulation(int(service_id))
        return self.engine.detail_view(int(service_id))

    def api_faults(self):
        return {"faults": [{"key": k, "label": v} for k, v in FAULT_LABELS.items()]}

    def api_incidents(self):
        incidents = self.engine.store.list_incidents()
        return {"incidents": [self.engine.incident_view(i) for i in incidents]}

    def api_incident(self, incident_id):
        return self.engine.incident_detail(int(incident_id))

    def api_incident_start(self, incident_id):
        self.engine.start_incident(int(incident_id))
        return self.engine.incident_detail(int(incident_id))

    def api_incident_resolve(self, incident_id):
        self.engine.resolve_incident(int(incident_id))
        return self.engine.incident_detail(int(incident_id))

    def api_teams(self):
        return {"teams": self.engine.teams_view()}


ROUTES = [
    (method, re.compile(pattern), handler)
    for method, pattern, handler in [
        ("GET", r"/api/overview", Handler.api_overview),
        ("GET", r"/api/directory", Handler.api_directory),
        ("GET", r"/api/faults", Handler.api_faults),
        ("POST", r"/api/check", Handler.api_check),
        ("GET", r"/api/services/(\d{1,9})", Handler.api_service),
        ("DELETE", r"/api/services/(\d{1,9})", Handler.api_remove),
        ("POST", r"/api/services/(\d{1,9})/recheck", Handler.api_recheck),
        ("POST", r"/api/services/(\d{1,9})/simulate", Handler.api_simulate),
        ("DELETE", r"/api/services/(\d{1,9})/simulate", Handler.api_end_simulation),
        ("GET", r"/api/incidents", Handler.api_incidents),
        ("GET", r"/api/incidents/(\d{1,9})", Handler.api_incident),
        ("POST", r"/api/incidents/(\d{1,9})/start", Handler.api_incident_start),
        ("POST", r"/api/incidents/(\d{1,9})/resolve", Handler.api_incident_resolve),
        ("GET", r"/api/teams", Handler.api_teams),
    ]
]


def create_server(port, engine) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((HOST, port), Handler)
    server.engine = engine
    return server


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SentinelOps web app.")
    parser.add_argument("-p", "--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite database path")
    parser.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    store = Store(args.db)
    engine = Engine(store, Catalog())
    engine.seed()
    try:
        server = create_server(args.port, engine)
    except OSError as exc:
        print(f"error: cannot use port {args.port}: {exc}", file=sys.stderr)
        return 2

    stop = threading.Event()
    threading.Thread(target=engine.run_forever, args=(stop,), daemon=True).start()
    url = f"http://{HOST}:{args.port}"
    print(f"SentinelOps running at {url} (Ctrl+C to stop)", flush=True)
    if not args.no_browser:
        threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        stop.set()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
