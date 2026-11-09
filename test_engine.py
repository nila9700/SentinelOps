"""Tests for incident routing and the ticket lifecycle."""

import unittest
from pathlib import Path

import diagnostics
import routing
from catalog import Catalog, LookupFailed
from diagnostics import ComponentResult
from engine import MAX_SERVICES, Engine, NotFound
from store import Store


def healthy(name, url):
    steps = [diagnostics.make_step(k, diagnostics.OK, "Fine.") for k in diagnostics.STEP_LABELS]
    result = ComponentResult(name, url, diagnostics.OPERATIONAL, steps, 200, 120.0)
    return diagnostics.summarize(result)


class Harness(unittest.TestCase):
    def setUp(self):
        self.faults = {}  # component name -> fault to inject as a real failure

        def check(name, url):
            result = healthy(name, url)
            if name in self.faults:
                diagnostics.apply_fault(result, self.faults[name])
            return result

        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.engine = Engine(self.store, Catalog(), check=check)
        self.service = self.store.add_service(
            "acme.test", "Acme", "website",
            [["Website", "https://a.test"], ["API", "https://b.test"]],
        )

    def active(self):
        return self.store.list_incidents(active=True)


class IncidentTests(Harness):
    def test_healthy_service_opens_nothing(self):
        self.engine.check_service(self.service)
        self.assertEqual(self.store.get_service(self.service)["status"], "operational")
        self.assertEqual(self.active(), [])

    def test_each_fault_reaches_the_right_department(self):
        expected = {"dns": "network", "connection": "network", "tls": "security",
                    "server_error": "platform", "slow": "performance"}
        for fault, department in expected.items():
            with self.subTest(fault=fault):
                self.faults = {"Website": fault}
                self.engine.check_service(self.service)
                self.assertEqual([i["department"] for i in self.active()], [department])
                self.faults = {}
                self.engine.check_service(self.service)
                self.assertEqual(self.active(), [])

    def test_every_route_has_a_real_department(self):
        for fault in routing.ROUTES:
            self.assertIn(routing.route(fault)["department"], routing.DEPARTMENTS)

    def test_repeated_failures_keep_one_ticket(self):
        self.faults = {"Website": "dns"}
        for _ in range(3):
            self.engine.check_service(self.service)
        self.assertEqual(len(self.active()), 1)

    def test_tickets_start_at_1001_and_read_well(self):
        self.faults = {"Website": "dns"}
        self.engine.check_service(self.service)
        view = self.engine.incident_detail(self.active()[0]["id"])
        self.assertEqual(view["ticket"], "INC-1001")
        self.assertEqual(view["department_name"], "Network Operations")
        self.assertEqual([e["kind"] for e in view["events"]], ["detected", "routed", "assigned"])

    def test_recovery_closes_the_ticket(self):
        self.faults = {"Website": "server_error"}
        self.engine.check_service(self.service)
        incident_id = self.active()[0]["id"]
        self.faults = {}
        self.engine.check_service(self.service)
        view = self.engine.incident_detail(incident_id)
        self.assertEqual(view["status"], "resolved")
        self.assertIsNotNone(view["resolved_at"])
        self.assertEqual(view["events"][-1]["kind"], "resolved")

    def test_agents_share_the_work(self):
        self.faults = {"Website": "dns", "API": "connection"}
        self.engine.check_service(self.service)
        agents = {i["agent"] for i in self.active()}
        self.assertEqual(agents, {"Elin Berg", "Omar Haddad"})

    def test_partly_down_status(self):
        self.faults = {"API": "server_error"}
        self.engine.check_service(self.service)
        self.assertEqual(self.store.get_service(self.service)["status"], "partial")

    def test_ticket_workflow(self):
        self.faults = {"Website": "dns"}
        self.engine.check_service(self.service)
        incident_id = self.active()[0]["id"]
        self.engine.start_incident(incident_id)
        self.assertEqual(self.store.get_incident(incident_id)["status"], "in_progress")
        with self.assertRaises(ValueError):
            self.engine.start_incident(incident_id)
        self.engine.resolve_incident(incident_id)
        self.assertEqual(self.store.get_incident(incident_id)["status"], "resolved")
        with self.assertRaises(ValueError):
            self.engine.resolve_incident(incident_id)

    def test_real_problem_reopens_after_manual_resolve(self):
        self.faults = {"Website": "dns"}
        self.engine.check_service(self.service)
        self.engine.resolve_incident(self.active()[0]["id"])
        self.engine.check_service(self.service)
        self.assertEqual(len(self.active()), 1)

    def test_missing_ticket(self):
        with self.assertRaises(NotFound):
            self.engine.incident_detail(9999)


class SimulationTests(Harness):
    def test_simulation_opens_a_flagged_ticket(self):
        self.engine.simulate(self.service, "tls", "API")
        [incident] = self.active()
        self.assertEqual(incident["department"], "security")
        self.assertEqual(incident["simulated"], 1)
        self.assertEqual(self.engine.detail_view(self.service)["simulation"]["component"], "API")

    def test_ending_simulation_closes_the_ticket(self):
        self.engine.simulate(self.service, "dns", "Website")
        self.engine.end_simulation(self.service)
        self.assertEqual(self.active(), [])
        self.assertEqual(self.store.get_service(self.service)["status"], "operational")

    def test_resolving_a_simulated_ticket_ends_the_simulation(self):
        self.engine.simulate(self.service, "dns", "Website")
        self.engine.resolve_incident(self.active()[0]["id"])
        self.assertEqual(self.active(), [])
        self.assertIsNone(self.store.get_service(self.service)["sim_fault"])

    def test_simulation_survives_the_next_check(self):
        self.engine.simulate(self.service, "dns", "Website")
        self.engine.check_service(self.service)
        self.assertEqual(len(self.active()), 1)

    def test_simulated_checks_do_not_count_against_uptime(self):
        self.engine.check_service(self.service)
        self.engine.simulate(self.service, "dns", "Website")
        self.engine.simulate(self.service, "dns", "API")
        self.assertEqual(self.store.uptime(self.service), {"percent": 100.0, "checks": 1})

    def test_rejects_bad_simulation_input(self):
        with self.assertRaises(ValueError):
            self.engine.simulate(self.service, "meteor", "Website")
        with self.assertRaises(ValueError):
            self.engine.simulate(self.service, "dns", "Nope")


class ServiceTests(Harness):
    def test_lookup_adds_then_reuses(self):
        first = self.engine.lookup("Spotify")
        second = self.engine.lookup("spotify.com")
        self.assertEqual(first, second)

    def test_service_limit(self):
        for number in range(MAX_SERVICES - 1):
            self.store.add_service(f"s{number}.test", "S", "website", [["Website", "https://s.test"]])
        with self.assertRaises(LookupFailed):
            self.engine.lookup("Spotify")

    def test_remove_closes_tickets_and_keeps_history(self):
        self.faults = {"Website": "dns"}
        self.engine.check_service(self.service)
        incident_id = self.active()[0]["id"]
        self.engine.remove_service(self.service)
        self.assertIsNone(self.store.get_service(self.service))
        view = self.engine.incident_detail(incident_id)
        self.assertEqual(view["status"], "resolved")
        self.assertIsNone(view["service_id"])

    def test_seed_runs_once(self):
        self.engine.seed()
        count = self.store.count_services()
        self.assertGreater(count, 1)
        self.store.delete_service(self.store.find_service("github.com")["id"])
        self.engine.seed()
        self.assertEqual(self.store.count_services(), count - 1)

    def test_history_is_trimmed(self):
        for _ in range(5):
            self.store.save_result(self.service, "operational", 1.0, [], False)
        self.assertEqual(len(self.store.history(self.service, limit=3)), 3)

    def test_teams_view_counts_open_tickets(self):
        self.faults = {"Website": "dns"}
        self.engine.check_service(self.service)
        network = self.engine.teams_view()[0]
        self.assertEqual(network["agents"][0], {"name": "Elin Berg", "open": 1, "resolved": 0})


if __name__ == "__main__":
    unittest.main()
