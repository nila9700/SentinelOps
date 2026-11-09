"""SQLite storage for services, check history and incidents."""

import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS services (
    id INTEGER PRIMARY KEY,
    key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    source TEXT NOT NULL,
    components TEXT NOT NULL,
    added_at REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'checking',
    response_ms REAL,
    results TEXT NOT NULL DEFAULT '[]',
    checked_at REAL,
    sim_fault TEXT,
    sim_component TEXT
);
CREATE TABLE IF NOT EXISTS checks (
    id INTEGER PRIMARY KEY,
    service_id INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
    ts REAL NOT NULL,
    status TEXT NOT NULL,
    response_ms REAL,
    simulated INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS checks_by_service ON checks(service_id, ts);
CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY,
    service_id INTEGER REFERENCES services(id) ON DELETE SET NULL,
    service_name TEXT NOT NULL,
    component TEXT NOT NULL,
    fault TEXT NOT NULL,
    severity TEXT NOT NULL,
    summary TEXT NOT NULL,
    department TEXT NOT NULL,
    agent TEXT NOT NULL,
    status TEXT NOT NULL,
    simulated INTEGER NOT NULL DEFAULT 0,
    opened_at REAL NOT NULL,
    resolved_at REAL
);
CREATE TABLE IF NOT EXISTS incident_events (
    id INTEGER PRIMARY KEY,
    incident_id INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL
);
"""

HISTORY_KEPT = 200
FIRST_TICKET = 1000  # So tickets read INC-1001 rather than INC-1.


class Store:
    def __init__(self, path):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)

    def close(self):
        with self.lock:
            self.db.close()

    def _all(self, sql, params=()):
        with self.lock:
            return [dict(row) for row in self.db.execute(sql, params)]

    def _one(self, sql, params=()):
        rows = self._all(sql, params)
        return rows[0] if rows else None

    def _write(self, sql, params=()):
        with self.lock, self.db:
            return self.db.execute(sql, params)

    # Meta

    def get_meta(self, key):
        row = self._one("SELECT value FROM meta WHERE key = ?", (key,))
        return row["value"] if row else None

    def set_meta(self, key, value):
        self._write("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))

    # Services

    @staticmethod
    def _service(row):
        if row:
            row["components"] = json.loads(row["components"])
            row["results"] = json.loads(row["results"])
        return row

    def add_service(self, key, name, source, components) -> int:
        cursor = self._write(
            "INSERT INTO services (key, name, source, components, added_at) VALUES (?, ?, ?, ?, ?)",
            (key, name, source, json.dumps(components), time.time()),
        )
        return cursor.lastrowid

    def get_service(self, service_id):
        return self._service(self._one("SELECT * FROM services WHERE id = ?", (service_id,)))

    def find_service(self, key):
        return self._service(self._one("SELECT * FROM services WHERE key = ?", (key,)))

    def list_services(self):
        rows = self._all("SELECT * FROM services ORDER BY added_at, id")
        return [self._service(row) for row in rows]

    def count_services(self) -> int:
        return self._one("SELECT COUNT(*) AS n FROM services")["n"]

    def delete_service(self, service_id):
        self._write("DELETE FROM services WHERE id = ?", (service_id,))

    def save_result(self, service_id, status, response_ms, results, simulated):
        now = time.time()
        with self.lock, self.db:
            self.db.execute(
                "UPDATE services SET status = ?, response_ms = ?, results = ?, checked_at = ? "
                "WHERE id = ?",
                (status, response_ms, json.dumps(results), now, service_id),
            )
            self.db.execute(
                "INSERT INTO checks (service_id, ts, status, response_ms, simulated) "
                "VALUES (?, ?, ?, ?, ?)",
                (service_id, now, status, response_ms, int(simulated)),
            )
            self.db.execute(
                "DELETE FROM checks WHERE service_id = ? AND id NOT IN "
                "(SELECT id FROM checks WHERE service_id = ? ORDER BY id DESC LIMIT ?)",
                (service_id, service_id, HISTORY_KEPT),
            )

    def set_simulation(self, service_id, fault, component):
        self._write(
            "UPDATE services SET sim_fault = ?, sim_component = ? WHERE id = ?",
            (fault, component, service_id),
        )

    def history(self, service_id, limit=40):
        rows = self._all(
            "SELECT ts, status, response_ms, simulated FROM checks WHERE service_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (service_id, limit),
        )
        return rows[::-1]

    def uptime(self, service_id):
        """Share of real checks where the service was at least partly working."""
        row = self._one(
            "SELECT COUNT(*) AS total, SUM(status != 'down') AS good FROM checks "
            "WHERE service_id = ? AND simulated = 0",
            (service_id,),
        )
        if not row["total"]:
            return {"percent": None, "checks": 0}
        return {"percent": 100 * row["good"] / row["total"], "checks": row["total"]}

    # Incidents

    def open_incident(self, service_id, service_name, component, fault, severity, summary,
                      department, agent, simulated, events) -> int:
        now = time.time()
        with self.lock, self.db:
            incident_id = self.db.execute(
                "INSERT INTO incidents (id, service_id, service_name, component, fault, severity,"
                " summary, department, agent, status, simulated, opened_at)"
                " VALUES ((SELECT COALESCE(MAX(id), ?) + 1 FROM incidents),"
                " ?, ?, ?, ?, ?, ?, ?, ?, 'assigned', ?, ?)",
                (FIRST_TICKET, service_id, service_name, component, fault, severity, summary,
                 department, agent, int(simulated), now),
            ).lastrowid
            for kind, message in events:
                self.db.execute(
                    "INSERT INTO incident_events (incident_id, ts, kind, message) VALUES (?, ?, ?, ?)",
                    (incident_id, now, kind, message),
                )
        return incident_id

    def add_event(self, incident_id, kind, message):
        self._write(
            "INSERT INTO incident_events (incident_id, ts, kind, message) VALUES (?, ?, ?, ?)",
            (incident_id, time.time(), kind, message),
        )

    def set_incident_status(self, incident_id, status):
        resolved_at = time.time() if status == "resolved" else None
        self._write(
            "UPDATE incidents SET status = ?, resolved_at = ? WHERE id = ?",
            (status, resolved_at, incident_id),
        )

    def get_incident(self, incident_id):
        return self._one("SELECT * FROM incidents WHERE id = ?", (incident_id,))

    def events(self, incident_id):
        return self._all(
            "SELECT ts, kind, message FROM incident_events WHERE incident_id = ? ORDER BY id",
            (incident_id,),
        )

    def list_incidents(self, active=None, service_id=None, limit=100):
        where, params = [], []
        if active is not None:
            where.append("status != 'resolved'" if active else "status = 'resolved'")
        if service_id is not None:
            where.append("service_id = ?")
            params.append(service_id)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        return self._all(
            f"SELECT * FROM incidents {clause} ORDER BY id DESC LIMIT ?", (*params, limit)
        )

    def open_counts_by_agent(self) -> dict:
        rows = self._all(
            "SELECT agent, COUNT(*) AS n FROM incidents WHERE status != 'resolved' GROUP BY agent"
        )
        return {row["agent"]: row["n"] for row in rows}

    def resolved_counts_by_agent(self) -> dict:
        rows = self._all(
            "SELECT agent, COUNT(*) AS n FROM incidents WHERE status = 'resolved' GROUP BY agent"
        )
        return {row["agent"]: row["n"] for row in rows}
