"""Turns what a person types into something we can check.

Accepts a company name ("Spotify"), a domain ("spotify.com") or a full URL.
"""

import difflib
import ipaddress
import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import diagnostics

COMPANIES_FILE = Path(__file__).parent / "data" / "companies.json"
HOSTNAME = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}$")
MAX_QUERY = 200


@dataclass
class Target:
    key: str
    name: str
    source: str  # "directory", "website" or "guess"
    components: list  # [[name, url], ...]


class LookupFailed(Exception):
    def __init__(self, message, suggestions=()):
        super().__init__(message)
        self.message = message
        self.suggestions = list(suggestions)


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


class Catalog:
    def __init__(self, path=COMPANIES_FILE):
        self.companies = json.loads(Path(path).read_text(encoding="utf-8"))
        self.by_name = {}
        self.by_domain = {}
        for company in self.companies:
            self.by_domain[company["domain"]] = company
            for name in [company["name"], *company.get("aliases", [])]:
                self.by_name[normalize(name)] = company

    @staticmethod
    def to_target(company) -> Target:
        return Target(company["domain"], company["name"], "directory", company["components"])

    def featured(self) -> list:
        return [self.to_target(c) for c in self.companies if c.get("featured")]

    def listing(self) -> list:
        return [
            {"name": c["name"], "domain": c["domain"], "aliases": c.get("aliases", [])}
            for c in self.companies
        ]

    def resolve(self, query: str) -> Target:
        query = (query or "").strip()
        if not query:
            raise LookupFailed("Type a company name or website address.")
        if len(query) > MAX_QUERY:
            raise LookupFailed("That's too long to be a website address.")
        company = self.by_name.get(normalize(query))
        if company:
            return self.to_target(company)
        if "." in query or ":" in query or query.lower() == "localhost":
            return self.resolve_address(query)
        return self.resolve_name(query)

    def resolve_address(self, query: str) -> Target:
        url = query if "://" in query else f"https://{query}"
        try:
            parts = urlsplit(url)
            host = (parts.hostname or "").lower()
            port = diagnostics.default_port(parts)
        except ValueError:
            raise LookupFailed("That doesn't look like a website address.") from None
        if parts.scheme not in ("http", "https"):
            raise LookupFailed("Only http and https websites can be checked.")
        if self.is_private_literal(host):
            raise LookupFailed("Private and local addresses can't be checked.")
        if not HOSTNAME.match(host):
            raise LookupFailed("That doesn't look like a website address.")

        bare = host.removeprefix("www.")
        path = parts.path.rstrip("/")
        company = self.by_domain.get(bare)
        if company and not path and not parts.query:
            return self.to_target(company)

        try:
            if not diagnostics.host_is_public(host, port):
                raise LookupFailed("Private and local addresses can't be checked.")
        except (OSError, UnicodeError):
            pass  # It doesn't resolve: still worth showing as down.
        label = bare + path
        clean = f"{parts.scheme}://{parts.netloc.lower().split('@')[-1]}{parts.path}"
        if parts.query:
            clean += f"?{parts.query}"
        return Target(label, label, "website", [["Website", clean]])

    def resolve_name(self, query: str) -> Target:
        close = difflib.get_close_matches(normalize(query), list(self.by_name), n=3, cutoff=0.7)
        suggestions = list(dict.fromkeys(self.by_name[match]["name"] for match in close))
        if suggestions:
            raise LookupFailed(f'We couldn\'t find "{query}". Did you mean:', suggestions)

        slug = re.sub(r"[^a-z0-9-]", "", query.lower().replace(" ", ""))
        host = f"{slug}.com"
        missing = f'We couldn\'t find a website for "{query}". Try its address, like example.com.'
        if not slug or not HOSTNAME.match(host):
            raise LookupFailed(missing)
        try:
            if not diagnostics.host_is_public(host):
                raise LookupFailed(missing)
        except (OSError, UnicodeError):
            raise LookupFailed(missing) from None
        return Target(host, query, "guess", [["Website", f"https://{host}"]])

    @staticmethod
    def is_private_literal(host: str) -> bool:
        if host == "localhost" or host.endswith(".localhost"):
            return True
        try:
            return not ipaddress.ip_address(host).is_global
        except ValueError:
            return False
