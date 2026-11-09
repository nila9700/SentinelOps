"""Application logic: runs checks, keeps history, opens and closes incidents."""

import threading
from concurrent.futures import ThreadPoolExecutor

import diagnostics
import routing
from catalog import LookupFailed

CHECK_INTERVAL = 60
WORKERS = 4  # Small on purpose: this runs on a low-spec machine.
MAX_SERVICES = 30


class NotFound(Exception):
    pass


class Engine:
    def __init__(self, store, catalog, check=diagnostics.check_component):
        self.store = store
        self.catalog = catalog
        self.check = check

    # Services

    def seed(self):
        """Add the featured companies the first time the app runs."""
        if self.store.get_meta("seeded"):
            return
        for target in self.catalog.featured():
            self.store.add_service(target.key, target.name, target.source, target.components)
        self.store.set_meta("seeded", "1")

    def lookup(self, query: str) -> int:
        """Find or add the service for a search, check it, and return its id."""
        target = self.catalog.resolve(query)
        existing = self.store.find_service(target.key)
        if existing:
            service_id = existing["id"]
        else:
            if self.store.count_services() >= MAX_SERVICES:
                raise LookupFailed(
                    f"You're watching {MAX_SERVICES} services, the most this demo allows. "
                    "Remove one and try again."
                )
            service_id = self.store.add_service(
                target.key, target.name, target.source, target.components
            )
        self.check_service(service_id)
        return service_id

    def require_service(self, service_id) -> dict:
        service = self.store.get_service(service_id)
        if not service:
            raise NotFound("That service isn't being monitored.")
        return service

    def check_service(self, service_id):
        service = self.require_service(service_id)
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(lambda c: self.check(c[0], c[1]), service["components"]))

        with self.store.lock:
            service = self.store.get_service(service_id)
            if not service:  # Removed while the check was running.
                return
            fault, target = service["sim_fault"], service["sim_component"]
            simulated = set()
            if fault:
                for result in results:
                    if result.name == target:
                        diagnostics.apply_fault(result, fault)
                        simulated.add(result.name)
            status = diagnostics.overall_status(results)
            self.store.save_result(
                service_id,
                status,
                results[0].response_ms,
                [r.to_dict() for r in results],
                bool(simulated),
            )
            self._reconcile(service, results, simulated)

    def check_all(self):
        services = self.store.list_services()
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            list(pool.map(self._check_quietly, [s["id"] for s in services]))

    def _check_quietly(self, service_id):
        try:
            self.check_service(service_id)
        except NotFound:
            pass

    def run_forever(self, stop: threading.Event, interval=CHECK_INTERVAL):
        while not stop.is_set():
            self.check_all()
            stop.wait(interval)

    def remove_service(self, service_id):
        service = self.require_service(service_id)
        with self.store.lock:
            for incident in self.store.list_incidents(active=True, service_id=service_id):
                self._resolve(incident, "Closed because the service was removed from monitoring.")
            self.store.delete_service(service["id"])

    # Simulation

    def simulate(self, service_id, fault, component):
        service = self.require_service(service_id)
        if fault not in diagnostics.FAULTS:
            raise ValueError("Unknown problem type.")
        if component not in [c[0] for c in service["components"]]:
            raise ValueError("Unknown part of this service.")
        self.store.set_simulation(service_id, fault, component)
        self.check_service(service_id)

    def end_simulation(self, service_id):
        self.require_service(service_id)
        self.store.set_simulation(service_id, None, None)
        self.check_service(service_id)

    # Incidents

    def _reconcile(self, service, results, simulated):
        """Open a ticket for each new problem, and close those that went away."""
        active = {(r.name, r.fault): r for r in results if r.fault}
        open_incidents = self.store.list_incidents(active=True, service_id=service["id"])
        known = {(i["component"], i["fault"]) for i in open_incidents}

        for incident in open_incidents:
            if (incident["component"], incident["fault"]) not in active:
                self._resolve(incident, "An automatic check confirmed this is working again.")

        for key, result in active.items():
            if key not in known:
                self._open(service, result, result.name in simulated)

    def _open(self, service, result, simulated):
        route = routing.route(result.fault)
        department = routing.DEPARTMENTS[route["department"]]
        agent = routing.choose_agent(route["department"], self.store.open_counts_by_agent())
        self.store.open_incident(
            service["id"],
            service["name"],
            result.name,
            result.fault,
            "Critical" if result.state == diagnostics.DOWN else "Medium",
            result.summary,
            route["department"],
            agent,
            simulated,
            [
                ("detected", f"Problem found in {result.name}: {result.summary}"),
                ("routed", f"Sent to {department['name']}. {route['reason']}"),
                ("assigned", f"Assigned to {agent}, who had the fewest open tickets."),
            ],
        )

    def _resolve(self, incident, message):
        self.store.add_event(incident["id"], "resolved", message)
        self.store.set_incident_status(incident["id"], "resolved")

    def start_incident(self, incident_id):
        with self.store.lock:
            incident = self.require_incident(incident_id)
            if incident["status"] != "assigned":
                raise ValueError("Work on this ticket has already started.")
            self.store.add_event(
                incident_id, "started", f"{incident['agent']} started working on this."
            )
            self.store.set_incident_status(incident_id, "in_progress")

    def resolve_incident(self, incident_id):
        with self.store.lock:
            incident = self.require_incident(incident_id)
            if incident["status"] == "resolved":
                raise ValueError("This ticket is already resolved.")
            self._resolve(incident, f"{incident['agent']} marked this as resolved.")
            # A simulated fault would reappear on the next check unless it stops too.
            service_id = incident["service_id"] if incident["simulated"] else None
        if service_id and self.store.get_service(service_id):
            self.end_simulation(service_id)

    def require_incident(self, incident_id) -> dict:
        incident = self.store.get_incident(incident_id)
        if not incident:
            raise NotFound("That ticket doesn't exist.")
        return incident

    # Views sent to the frontend

    def service_view(self, service) -> dict:
        service_id = service["id"]
        active = self.store.list_incidents(active=True, service_id=service_id)
        return {
            "id": service_id,
            "name": service["name"],
            "key": service["key"],
            "source": service["source"],
            "status": service["status"],
            "response_ms": service["response_ms"],
            "checked_at": service["checked_at"],
            "components": service["results"]
            or [{"name": c[0], "url": c[1], "state": "checking", "steps": []}
                for c in service["components"]],
            "history": self.store.history(service_id),
            "uptime": self.store.uptime(service_id),
            "open_incidents": len(active),
            "simulation": {"fault": service["sim_fault"], "component": service["sim_component"]}
            if service["sim_fault"]
            else None,
        }

    def list_views(self) -> list:
        return [self.service_view(s) for s in self.store.list_services()]

    def detail_view(self, service_id) -> dict:
        view = self.service_view(self.require_service(service_id))
        view["incidents"] = [
            self.incident_view(i)
            for i in self.store.list_incidents(service_id=service_id, limit=10)
        ]
        return view

    def incident_view(self, incident) -> dict:
        route = routing.route(incident["fault"])
        return {
            **incident,
            "ticket": f"INC-{incident['id']}",
            "title": route["title"],
            "reason": route["reason"],
            "action": route["action"],
            "department_name": routing.DEPARTMENTS[incident["department"]]["name"],
            "simulated": bool(incident["simulated"]),
        }

    def incident_detail(self, incident_id) -> dict:
        view = self.incident_view(self.require_incident(incident_id))
        view["events"] = self.store.events(incident_id)
        return view

    def teams_view(self) -> list:
        open_counts = self.store.open_counts_by_agent()
        resolved = self.store.resolved_counts_by_agent()
        titles = {}
        for department, title, _, _ in routing.ROUTES.values():
            titles.setdefault(department, []).append(title)
        return [
            {
                "key": key,
                "name": department["name"],
                "handles": department["handles"],
                "problems": titles.get(key, []),
                "agents": [
                    {
                        "name": agent,
                        "open": open_counts.get(agent, 0),
                        "resolved": resolved.get(agent, 0),
                    }
                    for agent in department["agents"]
                ],
            }
            for key, department in routing.DEPARTMENTS.items()
        ]
