"""Tests for turning a search into a target."""

import socket
import unittest
from unittest import mock

import catalog
from catalog import Catalog, LookupFailed


class ResolveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog()

    def failure(self, query):
        with self.assertRaises(LookupFailed) as caught:
            self.catalog.resolve(query)
        return caught.exception

    def test_company_name_any_case(self):
        self.assertEqual(self.catalog.resolve("  sPoTiFy ").name, "Spotify")

    def test_alias(self):
        self.assertEqual(self.catalog.resolve("twitter").name, "X")

    def test_punctuation_in_names(self):
        self.assertEqual(self.catalog.resolve("h&m").name, "H&M")

    def test_domain_finds_company(self):
        self.assertEqual(self.catalog.resolve("https://www.github.com/").name, "GitHub")

    def test_company_url_with_a_path_checks_that_path(self):
        with mock.patch("diagnostics.lookup", return_value=["1.1.1.1"]):
            target = self.catalog.resolve("github.com/pricing")
        self.assertEqual(target.components, [["Website", "https://github.com/pricing"]])

    def test_unknown_domain(self):
        with mock.patch("diagnostics.lookup", return_value=["1.1.1.1"]):
            target = self.catalog.resolve("Example.org")
        self.assertEqual((target.key, target.source), ("example.org", "website"))

    def test_unresolvable_domain_can_still_be_shown_as_down(self):
        with mock.patch("diagnostics.lookup", side_effect=socket.gaierror()):
            target = self.catalog.resolve("no-such-host.invalid")
        self.assertEqual(target.key, "no-such-host.invalid")

    def test_credentials_are_dropped(self):
        with mock.patch("diagnostics.lookup", return_value=["1.1.1.1"]):
            target = self.catalog.resolve("https://user:pw@example.org/a")
        self.assertEqual(target.components[0][1], "https://example.org/a")

    def test_typo_gets_a_suggestion(self):
        self.assertIn("Spotify", self.failure("Spotfy").suggestions)

    def test_unknown_name_guesses_dotcom(self):
        with mock.patch("diagnostics.lookup", return_value=["1.1.1.1"]):
            target = self.catalog.resolve("Zephyr Quilts")
        self.assertEqual((target.key, target.source), ("zephyrquilts.com", "guess"))

    def test_unknown_name_with_no_website(self):
        with mock.patch("diagnostics.lookup", side_effect=socket.gaierror()):
            self.assertIn("couldn't find", self.failure("qqqzzzxxx").message)

    def test_private_addresses_are_refused(self):
        for query in ("localhost", "localhost:8000", "http://127.0.0.1", "192.168.1.1",
                      "http://[::1]/", "http://169.254.169.254/"):
            with self.subTest(query=query):
                self.failure(query)

    def test_domain_pointing_at_private_address_is_refused(self):
        with mock.patch("diagnostics.lookup", return_value=["10.0.0.5"]):
            self.assertIn("Private", self.failure("intranet.example.org").message)

    def test_rejects_bad_input(self):
        for query in ("", "   ", "ftp://example.com", "http://", "a" * 300, "http://exa mple.com",
                      "https://example.com:99999"):
            with self.subTest(query=query):
                self.failure(query)


class DirectoryFileTests(unittest.TestCase):
    def test_entries_are_well_formed(self):
        companies = Catalog().companies
        self.assertEqual(len({c["domain"] for c in companies}), len(companies))
        for company in companies:
            self.assertTrue(company["components"], company["name"])
            names = [c[0] for c in company["components"]]
            self.assertEqual(len(names), len(set(names)), company["name"])
            for _, url in company["components"]:
                self.assertTrue(url.startswith("https://"), url)


if __name__ == "__main__":
    unittest.main()
