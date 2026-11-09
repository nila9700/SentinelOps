#!/usr/bin/env python3
"""SentinelOps uptime monitor.

Checks a list of URLs over HTTP and reports which services are up or down,
along with each one's status code and response time.

Exit codes make it usable from cron, CI and health checks:
    0  every target is up
    1  at least one target is down
    2  bad usage (no targets, unreadable targets file)
"""

import argparse
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

DEFAULT_TARGETS_FILE = "targets.txt"
DEFAULT_TIMEOUT = 5.0
# Each worker is a thread holding one open connection, so this stays small
# to suit low-spec machines.
DEFAULT_WORKERS = 5
USER_AGENT = "SentinelOps-Monitor/1.0"


@dataclass
class CheckResult:
    url: str
    is_up: bool
    status_code: Optional[int] = None
    response_ms: Optional[float] = None
    error: Optional[str] = None


def normalize_url(target: str) -> str:
    """Default to https:// so a bare 'example.com' works."""
    return target if "://" in target else f"https://{target}"


def unique_urls(targets) -> list:
    """Normalize targets and drop duplicates, keeping the original order."""
    return list(dict.fromkeys(normalize_url(t) for t in targets))


def _exception_chain(exc):
    """Yield exc and everything it wraps.

    requests buries the real cause several layers down, e.g.
    ConnectionError -> MaxRetryError -> NameResolutionError -> gaierror.
    """
    seen = set()
    stack = [exc]
    while stack:
        current = stack.pop()
        if not isinstance(current, BaseException) or id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        stack.extend(current.args)
        stack.extend(
            [
                getattr(current, "reason", None),
                current.__cause__,
                current.__context__,
            ]
        )


def describe_connection_error(exc: requests.exceptions.ConnectionError) -> str:
    """Turn a requests ConnectionError into a short human-readable reason."""
    chain = list(_exception_chain(exc))
    if any(isinstance(e, socket.gaierror) for e in chain):
        return "DNS lookup failed"
    if any(isinstance(e, ConnectionRefusedError) for e in chain):
        return "connection refused"
    if any(isinstance(e, ConnectionResetError) for e in chain):
        return "connection reset"
    return "connection failed"


def check_url(url: str, timeout: float = DEFAULT_TIMEOUT) -> CheckResult:
    """Request one URL. Never raises for network or URL problems."""
    start = time.perf_counter()
    try:
        # stream=True stops after the headers: the body isn't needed, and
        # skipping it keeps memory use flat however large the page is.
        with requests.get(
            url,
            timeout=timeout,
            stream=True,
            headers={"User-Agent": USER_AGENT},
        ) as response:
            elapsed_ms = (time.perf_counter() - start) * 1000
            is_up = response.status_code < 400
            return CheckResult(
                url=url,
                is_up=is_up,
                status_code=response.status_code,
                response_ms=elapsed_ms,
                error=None if is_up else response.reason,
            )
    # Order matters: ConnectTimeout is both a Timeout and a ConnectionError,
    # and SSLError is a ConnectionError.
    except requests.exceptions.Timeout:
        error = f"timed out after {timeout:g}s"
    except requests.exceptions.SSLError:
        error = "SSL certificate error"
    except requests.exceptions.ConnectionError as exc:
        error = describe_connection_error(exc)
    except requests.exceptions.TooManyRedirects:
        error = "too many redirects"
    except (requests.exceptions.InvalidURL, requests.exceptions.InvalidSchema):
        error = "invalid URL"
    except requests.exceptions.RequestException as exc:
        error = f"request failed ({type(exc).__name__})"
    return CheckResult(url=url, is_up=False, error=error)


def check_all(urls, timeout: float, workers: int) -> list:
    """Check URLs concurrently, returning results in the input order."""
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda url: check_url(url, timeout), urls))


def load_targets(path: str) -> list:
    """Read one URL per line, skipping blank lines and # comments."""
    targets = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            targets.append(line)
    return targets


def format_report(results) -> str:
    width = max([len("TARGET")] + [len(r.url) for r in results])
    lines = [f"{'STATE':<6}{'CODE':<6}{'TIME':>9}  {'TARGET':<{width}}  DETAIL"]
    for r in results:
        state = "UP" if r.is_up else "DOWN"
        code = r.status_code if r.status_code is not None else "-"
        elapsed = f"{r.response_ms:.0f} ms" if r.response_ms is not None else "-"
        line = f"{state:<6}{code!s:<6}{elapsed:>9}  {r.url:<{width}}  {r.error or ''}"
        lines.append(line.rstrip())
    up = sum(r.is_up for r in results)
    lines.append("")
    lines.append(f"{up}/{len(results)} up, {len(results) - up} down")
    return "\n".join(lines)


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check whether websites are up and how fast they respond."
    )
    parser.add_argument(
        "urls",
        nargs="*",
        help=f"URLs to check (default: read from {DEFAULT_TARGETS_FILE})",
    )
    parser.add_argument(
        "-f",
        "--file",
        help=f"file of URLs, one per line (default: {DEFAULT_TARGETS_FILE})",
    )
    parser.add_argument(
        "-t",
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help="seconds to wait for connect and for each read (default: %(default)s)",
    )
    parser.add_argument(
        "-w",
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help="concurrent checks (default: %(default)s)",
    )
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be greater than 0")
    if args.workers < 1:
        parser.error("--workers must be at least 1")
    return args


def main(argv=None) -> int:
    args = parse_args(argv)

    targets = list(args.urls)
    if args.file or not targets:
        path = args.file or DEFAULT_TARGETS_FILE
        try:
            targets += load_targets(path)
        except (OSError, UnicodeDecodeError) as exc:
            print(f"error: cannot read targets file {path}: {exc}", file=sys.stderr)
            return 2

    urls = unique_urls(targets)
    if not urls:
        print("error: no targets to check", file=sys.stderr)
        return 2

    results = check_all(urls, args.timeout, args.workers)
    print(format_report(results))
    return 0 if all(r.is_up for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
