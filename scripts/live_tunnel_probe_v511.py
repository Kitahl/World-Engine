#!/usr/bin/env python3
"""Bounded live proof for the account-free Cloudflare Quick Tunnel path.

This is a release diagnostic, not application runtime code.  It deliberately
uses a throwaway loopback server, a random one-run API key, and an isolated
temporary World Engine data directory.  The only public information it emits is
the temporary tunnel hostname and non-secret fingerprints.

Run from the repository root (or any directory):

    .venv\\Scripts\\python.exe scripts\\live_tunnel_probe_v511.py

The probe needs Internet access because a real Quick Tunnel is a Cloudflare
service.  It never reads, writes, moves, or renames a user's cloudflared
configuration.  The child process receives only the temporary owned home that
``start_cloudflare_quick_endpoint`` creates under the temporary data directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import world_engine_permanent_endpoint as endpoint
from world_engine.process_guard import (
    is_api_key_rejection,
    open_no_redirect,
)

WRONG_KEY_BODY = '{"detail":"Invalid World Engine API key"}'


def _sha12(value: str | bytes) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()[:12]


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _probe_handler(api_key: str) -> type[BaseHTTPRequestHandler]:
    """Build a quiet, deliberately tiny World-Engine-shaped test service."""

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format: str, *_args: object) -> None:
            # Access logs can contain Authorization headers on some servers.  The
            # diagnostic intentionally emits only its structured JSON report.
            return

        def _send_json(self, status: int, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            path = urllib.parse.urlsplit(self.path).path
            if path == "/health":
                self._send_json(200, _json_bytes({"service": "world-engine", "status": "ok"}))
                return
            if path == "/api/context":
                expected = f"Bearer {api_key}"
                if not secrets.compare_digest(self.headers.get("Authorization", ""), expected):
                    self._send_json(401, WRONG_KEY_BODY.encode("utf-8"))
                    return
                self._send_json(200, _json_bytes({"campaign_id": "live-tunnel-probe", "ok": True}))
                return
            self._send_json(404, _json_bytes({"detail": "Not Found"}))

    return Handler


class QuietThreadingHTTPServer(ThreadingHTTPServer):
    """Suppress expected probe-client disconnect tracebacks from stderr."""

    def handle_error(self, _request: object, _client_address: object) -> None:
        return


@contextmanager
def _loopback_server(api_key: str) -> Iterator[tuple[ThreadingHTTPServer, int]]:
    server = QuietThreadingHTTPServer(("127.0.0.1", 0), _probe_handler(api_key))
    server.daemon_threads = True
    worker = threading.Thread(target=server.serve_forever, name="we-live-tunnel-probe", daemon=True)
    worker.start()
    try:
        yield server, int(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def _request(url: str, *, api_key: str | None, timeout: float) -> dict[str, Any]:
    headers = {"User-Agent": "WorldEngineLiveTunnelProbe/5.1.1"}
    if api_key is not None:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with open_no_redirect(request, timeout) as response:
            body = response.read(4096).decode("utf-8", errors="replace")
            return {
                "status": int(response.status),
                "body": body,
                "redirected": str(response.geturl()) != url,
                "category": "response",
            }
    except urllib.error.HTTPError as error:
        return {
            "status": int(error.code),
            "body": error.read(4096).decode("utf-8", errors="replace"),
            "redirected": str(error.geturl()) != url,
            "category": "http_error",
        }
    except urllib.error.URLError as error:
        return {
            "status": None,
            "body": "",
            "redirected": None,
            "category": "timeout" if isinstance(error.reason, TimeoutError) else "transport_error",
        }


def _await_external_contract(
    public_url: str,
    *,
    api_key: str,
    wrong_key: str,
    timeout_seconds: float,
    max_attempts: int = 45,
    delay_seconds: float = 1.0,
) -> tuple[bool, dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Poll the full external contract without exposing transport details."""
    deadline = time.monotonic() + timeout_seconds
    attempts = 0
    failure_counts: dict[str, int] = {}
    health: dict[str, Any] = {"status": None, "body": "", "redirected": None, "category": "not_attempted"}
    correct = dict(health)
    wrong = dict(health)

    def record_failure(value: dict[str, Any]) -> None:
        category = str(value.get("category") or "unknown")
        failure_counts[category] = failure_counts.get(category, 0) + 1

    def readiness() -> dict[str, Any]:
        ordered = dict(sorted(failure_counts.items()))
        return {
            "attempts": attempts,
            "failure_categories": list(ordered),
            "failure_category_counts": ordered,
        }

    while attempts < max_attempts and time.monotonic() < deadline:
        attempts += 1
        remaining = max(0.25, deadline - time.monotonic())
        request_timeout = min(5.0, remaining)
        health = _request(public_url + "/health", api_key=None, timeout=request_timeout)
        health_ok = health.get("status") == 200 and health.get("redirected") is False
        if not health_ok:
            record_failure(health)
        else:
            correct = _request(
                public_url + "/api/context?campaign_id=live-tunnel-probe",
                api_key=api_key,
                timeout=request_timeout,
            )
            wrong = _request(
                public_url + "/api/context?campaign_id=live-tunnel-probe",
                api_key=wrong_key,
                timeout=request_timeout,
            )
            correct_ok = (
                correct.get("status") == 200
                and correct.get("redirected") is False
            )
            wrong_ok = (
                is_api_key_rejection(wrong.get("status"), str(wrong.get("body") or ""))
                and wrong.get("redirected") is False
            )
            if correct_ok and wrong_ok:
                return True, health, correct, wrong, readiness()
            if not correct_ok:
                record_failure(correct)
            if not wrong_ok:
                record_failure(wrong)
        delay = min(delay_seconds, max(0.0, deadline - time.monotonic()))
        if delay:
            time.sleep(delay)
    return False, health, correct, wrong, readiness()


def _runtime_receipt_ok(data: Path, runtime: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    receipt_path = endpoint.quick_runtime_receipt_path(data)
    receipt = endpoint.load_json(receipt_path)
    pid = int(runtime.get("pid") or 0)
    identity = endpoint._quick_process_identity(pid)
    receipt_bytes = json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return (
        receipt_path.is_file()
        and endpoint._identity_matches_quick_receipt(identity, receipt),
        {
            "pid": pid,
            "receipt_present": receipt_path.is_file(),
            "receipt_fingerprint": _sha12(receipt_bytes),
            "identity_fingerprint": _sha12(repr(identity.fingerprint()) if identity else "missing"),
        },
    )


def _run(*, timeout_seconds: float) -> dict[str, Any]:
    started = time.monotonic()
    api_key = secrets.token_urlsafe(32)
    wrong_key = secrets.token_urlsafe(32)
    result: dict[str, Any] = {
        "probe": "world-engine-v5.1.1-cloudflare-quick-live",
        "status": "FAIL",
        "external_availability": "not_proven",
        "api_key_fingerprint": _sha12(api_key),
        "checks": {},
        "durations_ms": {},
    }
    process_pid: int | None = None
    cleanup: dict[str, Any] = {}
    data: Path | None = None
    server: ThreadingHTTPServer | None = None
    try:
        with ExitStack() as stack:
            temporary = stack.enter_context(
                tempfile.TemporaryDirectory(prefix="world-engine-live-tunnel-")
            )
            data = Path(temporary) / "data"
            data.mkdir()
            server, port = stack.enter_context(_loopback_server(api_key))
            if server is None:
                raise RuntimeError("loopback server context returned no server")
            else:

                # Registered after the server context, so ExitStack stops and
                # proves ownership of the Quick child before it tears down the
                # loopback listener and finally deletes the temporary receipt.
                def cleanup_owned_tunnel() -> None:
                    nonlocal cleanup
                    stop_started = time.monotonic()
                    try:
                        cleanup = endpoint.stop_owned_quick_tunnel(data)
                        stopped = cleanup.get("status") in {"STOPPED", "NOT_RUNNING"}
                        receipt_removed = not endpoint.quick_runtime_receipt_path(data).exists()
                        gone = process_pid is None or endpoint._quick_process_identity(process_pid) is None
                    except Exception:  # noqa: BLE001 - cleanup failure must be recorded as fail-closed
                        cleanup = {"status": "ERROR"}
                        stopped = receipt_removed = gone = False
                    result["durations_ms"]["quick_tunnel_stop"] = round((time.monotonic() - stop_started) * 1000)
                    result["checks"]["owned_tunnel_cleanup"] = {
                        "ok": stopped and receipt_removed and gone,
                        "status": str(cleanup.get("status") or "UNKNOWN"),
                        "receipt_removed": receipt_removed,
                        "process_gone": gone,
                    }
                    if result["status"] == "PASS" and not result["checks"]["owned_tunnel_cleanup"]["ok"]:
                        result["status"] = "FAIL"

                stack.callback(cleanup_owned_tunnel)
                result["loopback_port"] = port
                resolve_started = time.monotonic()
                cloudflared = endpoint.automatic_cloudflared(
                    allow_download=True,
                    data=data,
                )
                result["durations_ms"]["cloudflared_resolve"] = round((time.monotonic() - resolve_started) * 1000)
                executable = Path(cloudflared)
                if not executable.is_file():
                    raise RuntimeError("automatic_cloudflared did not return a regular file")
                # On Windows, automatic_cloudflared itself only accepts this pin;
                # re-hashing makes that release invariant visible in the report.
                digest = endpoint.sha256_file(executable)
                result["checks"]["pinned_cloudflared"] = {
                    "ok": (digest == endpoint.CLOUDFLARED_WINDOWS_AMD64_SHA256 if sys.platform.startswith("win") else bool(digest)),
                    "executable_fingerprint": _sha12(str(executable.resolve())),
                    "sha256_prefix": digest[:12],
                }
                if not result["checks"]["pinned_cloudflared"]["ok"]:
                    raise RuntimeError("automatic_cloudflared failed pinned helper verification")

                start_started = time.monotonic()
                runtime = endpoint.start_cloudflare_quick_endpoint(
                    cloudflared,
                    data=data,
                    port=port,
                    timeout_seconds=timeout_seconds,
                )
                result["durations_ms"]["quick_tunnel_start"] = round((time.monotonic() - start_started) * 1000)
                process_pid = int(runtime.get("pid") or 0)
                public_url = str(runtime["public_url"])
                hostname = urllib.parse.urlsplit(public_url).hostname or ""
                if not hostname.lower().endswith(".trycloudflare.com"):
                    raise RuntimeError("Quick Tunnel returned a non-Cloudflare hostname")
                result["public_hostname"] = hostname
                result["public_hostname_fingerprint"] = _sha12(hostname)
                receipt_ok, receipt_report = _runtime_receipt_ok(data, runtime)
                result["checks"]["runtime_receipt_exact_ownership"] = {"ok": receipt_ok, **receipt_report}
                if not receipt_ok:
                    raise RuntimeError("Quick Tunnel ownership receipt did not match the live process")

                external_started = time.monotonic()
                external_ok, health, correct, wrong, readiness = _await_external_contract(
                    public_url,
                    api_key=api_key,
                    wrong_key=wrong_key,
                    timeout_seconds=timeout_seconds,
                    max_attempts=45,
                    delay_seconds=1.0,
                )
                result["durations_ms"]["external_requests"] = round((time.monotonic() - external_started) * 1000)
                result["checks"]["external_readiness"] = readiness
                result["checks"]["external_health"] = {"ok": health["status"] == 200 and not health["redirected"], "status": health["status"]}
                result["checks"]["external_correct_bearer"] = {"ok": correct["status"] == 200 and not correct["redirected"], "status": correct["status"]}
                result["checks"]["external_wrong_bearer_exact_401"] = {"ok": is_api_key_rejection(wrong["status"], wrong["body"]) and not wrong["redirected"], "status": wrong["status"]}
                if not external_ok:
                    raise RuntimeError("external Quick Tunnel request contract failed")
                result["external_availability"] = "proven_from_this_host"
                result["status"] = "PASS"
                # Keep the local listener visibly alive until tunnel cleanup has
                # been verified.  The context manager stops it afterwards.
                result["checks"]["loopback_server_running"] = {"ok": bool(server.fileno() >= 0)}
    except Exception as error:  # noqa: BLE001 - top-level diagnostic returns a redacted structured failure
        result["error_type"] = type(error).__name__
        # Error text may include a URL from cloudflared's log.  Deliberately
        # retain only its type; the log remains inside TemporaryDirectory.
        result["error"] = "live tunnel probe failed; inspect local execution logs"
    finally:
        server_closed = server is None or server.fileno() == -1
        result["checks"]["loopback_server_cleanup"] = {"ok": server_closed}
        if result["status"] == "PASS" and not server_closed:
            result["status"] = "FAIL"
        result["durations_ms"]["total"] = round((time.monotonic() - started) * 1000)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=45.0, help="per-start/request timeout in seconds (default: 45)")
    args = parser.parse_args(argv)
    if args.timeout < 3 or args.timeout > 120:
        parser.error("--timeout must be between 3 and 120 seconds")
    report = _run(timeout_seconds=float(args.timeout))
    print(json.dumps(report, sort_keys=True, indent=2, ensure_ascii=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
