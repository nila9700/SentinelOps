"""Layered health checks for one URL.

A check walks the same path a visitor's browser does: find the address, reach
the server, secure the connection, load the page, measure the speed. The first
step that fails names the real problem, and later steps are skipped.
"""

import ipaddress
import socket
import ssl
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Optional
from urllib.parse import urljoin, urlsplit

import requests

from monitor import USER_AGENT, describe_connection_error

TIMEOUT = 4.0
SLOW_MS = 8000  # Includes redirects, which take big sites several seconds.
CERT_WARNING_DAYS = 3  # Big sites rotate short-lived certificates weekly.
MAX_REDIRECTS = 5
RETRY_DELAY = 0.5

OK, WARN, FAIL, SKIPPED = "ok", "warn", "fail", "skipped"
OPERATIONAL, DEGRADED, DOWN = "operational", "degraded", "down"

# These mean the server is alive and chose to refuse an automated visitor,
# which is common on large sites and is not an outage. 999 is LinkedIn's.
REFUSES_BOTS = {401, 403, 405, 406, 429, 999}

STEP_LABELS = {
    "dns": "Finding the website",
    "connect": "Reaching the server",
    "tls": "Securing the connection",
    "http": "Loading the page",
    "speed": "Responding quickly",
}


@dataclass
class Step:
    key: str
    label: str
    state: str
    summary: str
    detail: str = ""
    ms: Optional[float] = None
    fault: Optional[str] = None


@dataclass
class ComponentResult:
    name: str
    url: str
    state: str
    steps: list = field(default_factory=list)
    status_code: Optional[int] = None
    response_ms: Optional[float] = None
    fault: Optional[str] = None
    summary: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def make_step(key, state, summary, detail="", ms=None, fault=None) -> Step:
    return Step(key, STEP_LABELS[key], state, summary, detail, ms, fault)


def lookup(host: str, port: int, timeout: float = TIMEOUT) -> list:
    """Resolve host to IPs. getaddrinfo has no timeout, so it runs in a thread."""
    box = {}

    def work():
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            box["ips"] = sorted({info[4][0] for info in infos})
        except (OSError, UnicodeError) as exc:
            box["error"] = exc

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise TimeoutError("DNS lookup timed out")
    if "error" in box:
        raise box["error"]
    return box["ips"]


def is_public(ip: str) -> bool:
    return ipaddress.ip_address(ip.split("%", 1)[0]).is_global


def host_is_public(host: str, port: int = 443) -> bool:
    """False if any address is private, so a typed URL can't probe this network."""
    return all(is_public(ip) for ip in lookup(host, port))


class BlockedRedirect(Exception):
    pass


def default_port(parts) -> int:
    return parts.port or (443 if parts.scheme == "https" else 80)


def fetch(url: str, timeout: float, allow_private: bool):
    """GET url, checking every redirect hop against the private-address rule."""
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        with requests.get(
            current,
            timeout=timeout,
            stream=True,
            allow_redirects=False,
            headers={"User-Agent": USER_AGENT},
        ) as response:
            if not response.is_redirect:
                return response.status_code, response.reason
            target = urljoin(current, response.headers["Location"])
        parts = urlsplit(target)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise BlockedRedirect(target)
        if not allow_private and not host_is_public(parts.hostname, default_port(parts)):
            raise BlockedRedirect(target)
        current = target
    raise requests.exceptions.TooManyRedirects()


def step_dns(host, port, timeout, allow_private) -> Step:
    start = time.perf_counter()
    try:
        ips = lookup(host, port, timeout)
    except (OSError, UnicodeError) as exc:
        return make_step(
            "dns", FAIL, "We couldn't find this website's address.", str(exc), fault="dns"
        )
    ms = (time.perf_counter() - start) * 1000
    if not allow_private and not all(is_public(ip) for ip in ips):
        return make_step(
            "dns",
            FAIL,
            "This address points to a private network, so we didn't check it.",
            ", ".join(ips),
            ms,
            "blocked",
        )
    return make_step("dns", OK, "Website address found.", ", ".join(ips[:4]), ms)


def step_connect(host, port, timeout):
    """Return (step, socket). The socket is None unless the step passed."""
    start = time.perf_counter()
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except ConnectionRefusedError as exc:
        step = make_step(
            "connect", FAIL, "The server refused the connection.", str(exc), fault="connection"
        )
        return step, None
    except OSError as exc:
        step = make_step(
            "connect", FAIL, "The server isn't answering.", str(exc), fault="connection"
        )
        return step, None
    ms = (time.perf_counter() - start) * 1000
    return make_step("connect", OK, "Server reached.", f"Port {port}", ms), sock


def step_tls(sock, host) -> Step:
    start = time.perf_counter()
    try:
        context = ssl.create_default_context()
        with context.wrap_socket(sock, server_hostname=host) as tls:
            cert = tls.getpeercert()
            version = tls.version()
    except ssl.SSLCertVerificationError as exc:
        return make_step(
            "tls",
            FAIL,
            "The website's security certificate isn't valid.",
            exc.verify_message,
            fault="tls",
        )
    except OSError as exc:  # ssl.SSLError is an OSError, and so are timeouts.
        return make_step(
            "tls", FAIL, "A secure connection couldn't be set up.", str(exc), fault="tls"
        )
    ms = (time.perf_counter() - start) * 1000
    days = int((ssl.cert_time_to_seconds(cert["notAfter"]) - time.time()) / 86400)
    detail = f"{version}, certificate expires {cert['notAfter']}"
    if days < CERT_WARNING_DAYS:
        return make_step(
            "tls",
            WARN,
            f"The security certificate expires in {days} days.",
            detail,
            ms,
            "tls_expiring",
        )
    return make_step("tls", OK, f"Connection is secure for another {days} days.", detail, ms)


def step_http(url, timeout, allow_private):
    """Return (step, status_code)."""
    start = time.perf_counter()

    def failed(summary, detail, fault):
        return make_step("http", FAIL, summary, detail, fault=fault), None

    try:
        code, reason = fetch(url, timeout, allow_private)
    except BlockedRedirect as exc:
        return failed("The site redirected to an address we won't follow.", str(exc), "blocked")
    except requests.exceptions.Timeout:
        return failed(
            "The server connected but didn't answer in time.",
            f"No response within {timeout:g}s",
            "server_error",
        )
    except requests.exceptions.SSLError as exc:
        return failed("A secure connection couldn't be set up.", str(exc), "tls")
    except requests.exceptions.ConnectionError as exc:
        return failed("The connection dropped.", describe_connection_error(exc), "connection")
    except requests.exceptions.TooManyRedirects:
        return failed("The page keeps redirecting in a loop.", "Too many redirects", "client_error")
    except (requests.exceptions.RequestException, OSError, UnicodeError) as exc:
        return failed("The page couldn't be loaded.", type(exc).__name__, "client_error")

    ms = (time.perf_counter() - start) * 1000
    detail = f"HTTP {code} {reason}"
    if code < 400:
        return make_step("http", OK, "Page loaded.", detail, ms), code
    if code in REFUSES_BOTS:
        summary = "The site is answering, but it doesn't allow automated visits."
        return make_step("http", OK, summary, detail, ms), code
    if code < 500:
        step = make_step(
            "http", WARN, "The page couldn't be loaded.", detail, ms, "client_error"
        )
        return step, code
    return make_step("http", FAIL, "The server reported an error.", detail, ms, "server_error"), code


def step_speed(total_ms) -> Step:
    seconds = total_ms / 1000
    if total_ms > SLOW_MS:
        return make_step(
            "speed", WARN, f"Slow: it took {seconds:.1f} seconds.", ms=total_ms, fault="slow"
        )
    return make_step("speed", OK, f"Responded in {seconds:.2f} seconds.", ms=total_ms)


def skipped_steps(keys, reason="Not tested because an earlier step failed.") -> list:
    return [make_step(key, SKIPPED, reason) for key in keys]


def summarize(result: ComponentResult) -> ComponentResult:
    """Fill in the overall state from the steps."""
    failed = [s for s in result.steps if s.state == FAIL]
    warned = [s for s in result.steps if s.state == WARN]
    first = (failed or warned or [None])[0]
    result.state = DOWN if failed else DEGRADED if warned else OPERATIONAL
    result.fault = first.fault if first else None
    result.summary = first.summary if first else "Working normally."
    return result


def run_steps(name, url, timeout, allow_private) -> ComponentResult:
    parts = urlsplit(url)
    host, port = parts.hostname, default_port(parts)
    result = ComponentResult(name, url, DOWN)

    dns = step_dns(host, port, timeout, allow_private)
    result.steps.append(dns)
    if dns.state == FAIL:
        result.steps += skipped_steps(["connect", "tls", "http", "speed"])
        return summarize(result)

    connect, sock = step_connect(host, port, timeout)
    result.steps.append(connect)
    if sock is None:
        result.steps += skipped_steps(["tls", "http", "speed"])
        return summarize(result)

    if parts.scheme == "https":
        tls = step_tls(sock, host)
        result.steps.append(tls)
        if tls.state == FAIL:
            result.steps += skipped_steps(["http", "speed"])
            return summarize(result)
    else:
        sock.close()
        result.steps += skipped_steps(["tls"], "This site doesn't use a secure connection.")

    http, code = step_http(url, timeout, allow_private)
    result.steps.append(http)
    result.status_code = code
    if code is None:
        result.steps += skipped_steps(["speed"])
        return summarize(result)

    result.response_ms = http.ms
    result.steps.append(step_speed(http.ms))
    return summarize(result)


def check_component(name, url, timeout=TIMEOUT, allow_private=False) -> ComponentResult:
    """Check one URL, retrying once so a brief blip doesn't open an incident."""
    result = run_steps(name, url, timeout, allow_private)
    if result.state == DOWN and result.fault != "blocked":
        time.sleep(RETRY_DELAY)
        result = run_steps(name, url, timeout, allow_private)
    return result


# Portfolio demo: what each simulated outage looks like.
# Each entry is (step key, summary, detail).
FAULTS = {
    "dns": ("dns", "We couldn't find this website's address.", "NXDOMAIN (simulated)"),
    "connection": ("connect", "The server isn't answering.", "Connection timed out (simulated)"),
    "tls": ("tls", "The website's security certificate isn't valid.", "certificate has expired (simulated)"),
    "server_error": ("http", "The server reported an error.", "HTTP 503 Service Unavailable (simulated)"),
    "slow": ("speed", "Slow: it took 6.4 seconds.", "Simulated delay"),
}


def apply_fault(result: ComponentResult, fault: str) -> ComponentResult:
    """Rewrite a real result so it shows the simulated fault."""
    key, summary, detail = FAULTS[fault]
    keys = list(STEP_LABELS)
    position = keys.index(key)
    state = WARN if fault == "slow" else FAIL
    steps = [s for s in result.steps if keys.index(s.key) < position]
    steps = [
        s if s.state == OK else make_step(s.key, OK, "Working.")
        for s in steps
    ]
    steps.append(make_step(key, state, summary, detail, fault=fault))
    steps += skipped_steps(keys[position + 1:])
    result.steps = steps
    if fault == "server_error":
        result.status_code = 503
    elif fault == "slow":
        result.response_ms = 6400.0
    else:
        result.status_code = None
        result.response_ms = None
    return summarize(result)


def overall_status(results) -> str:
    states = [r.state for r in results]
    if all(s == OPERATIONAL for s in states):
        return "operational"
    if all(s == DOWN for s in states):
        return "down"
    return "partial" if DOWN in states else "degraded"
