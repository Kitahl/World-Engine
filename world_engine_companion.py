"""Standalone Windows desktop companion for World Engine 5.1.1.

The UI is a bundled local application hosted on an ephemeral 127.0.0.1 port
inside a pywebview/EdgeChromium window. The JavaScript bridge is closed: it
offers a safe snapshot, explicit authoring stages, one-time ngrok setup, and a
small external-link allowlist. It never exposes SQL, files, API keys, or a
generic engine dispatcher.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import webbrowser
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from world_engine import WorldEngine
from world_engine.desktop import DESKTOP_PROJECTION_VERSION, DesktopProjectionKernel
from world_engine_connection_guard import load_json, persistent_data_dir

ROOT = Path(__file__).resolve().parent
ASSET_ROOT = ROOT / "companion_ui"
DEFAULT_DB = persistent_data_dir() / "world_engine.sqlite3"
_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,100}$")
_AUTHOR_ACTIONS = frozenset({"stage", "validate", "dry_run", "promote"})
_PUBLIC_GENERATION_COUNT_KEYS = (
    "locations",
    "location_links",
    "factions",
    "npcs",
    "characters",
    "items",
    "resource_nodes",
    "quests",
)
_SPEC_KEYS = frozenset({"seed", "namespace", "mode", "config", "days"})
_ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/ambient_audio.js": ("ambient_audio.js", "text/javascript; charset=utf-8"),
}
_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data:; connect-src 'none'; object-src 'none'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


def _bounded_json(path: Path, limit: int = 1_000_000) -> dict[str, Any]:
    try:
        if not path.is_file() or path.stat().st_size > limit:
            return {}
        value = load_json(path)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _safe_endpoint_result(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """Secondary allowlist for every endpoint value crossing into JavaScript."""
    endpoint = value if isinstance(value, Mapping) else {}
    status = str(endpoint.get("status") or "NOT_CONFIGURED")[:100]
    return {
        "status": status,
        "provider": str(endpoint.get("provider") or "")[:100] or None,
        "public_url": str(endpoint.get("public_url") or "")[:2_048] or None,
        "error_code": str(endpoint.get("error_code") or "")[:100] or None,
        "message": str(endpoint.get("message") or "")[:500] or None,
        "retryable": bool(endpoint.get("retryable", status != "READY")),
        "permanent": bool(endpoint.get("permanent", False)),
        "stable_hostname": bool(endpoint.get("stable_hostname", False)),
        "requires_account": bool(endpoint.get("requires_account", False)),
        "action_reimport_required": bool(endpoint.get("action_reimport_required", False)),
    }


def _safe_fingerprint(value: Any) -> str | None:
    candidate = str(value or "").strip().casefold()
    return candidate if re.fullmatch(r"[0-9a-f]{12}", candidate) else None


def _endpoint_state() -> dict[str, Any]:
    data = persistent_data_dir()
    startup = _bounded_json(data / "last_startup_result.json")
    supervisor = _bounded_json(data / "supervisor_status.json")
    endpoint = supervisor.get("endpoint") if isinstance(supervisor.get("endpoint"), dict) else None
    if endpoint is None:
        endpoint = startup.get("endpoint") if isinstance(startup.get("endpoint"), dict) else {}
    else:
        endpoint = dict(endpoint)
    if not isinstance(endpoint, dict):
        endpoint = {}

    # Startup/supervisor receipts are historical observations and can retain a
    # now-acknowledged re-import warning.  The bounded current endpoint receipt
    # is authoritative for only the public connection fields below.  Overlaying
    # this strict allowlist keeps acknowledgement sticky across restarts without
    # allowing credentials or helper-process metadata into the bridge.
    current = _bounded_json(data / "permanent_endpoint.json")
    for key in (
        "provider",
        "public_url",
        "permanent",
        "stable_hostname",
        "requires_account",
        "action_reimport_required",
    ):
        if key in current:
            endpoint[key] = current[key]
    return _safe_endpoint_result(endpoint)


def _error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "code": code, "message": message[:500]}


class CompanionApi:
    """Exact local operator bridge exposed to the bundled webview."""

    def __init__(
        self,
        db_path: Path,
        campaign_id: str = "default",
        character_id: str | None = None,
    ) -> None:
        if not _ID_RE.fullmatch(campaign_id):
            raise ValueError("invalid campaign id")
        # PRIVATE by contract, not by convention. pywebview's exporter
        # (webview.util.inject_pywebview -> get_functions) recurses into any
        # PUBLIC attribute that is a non-callable object with a __module__, and
        # publishes its methods to JavaScript as "<attr>.<method>". With these
        # two attributes public, 595 unintended engine/projection methods were
        # reachable from the webview, including get_internal_state_block.
        # The leading underscore is what stops that walk.
        self._engine = WorldEngine(db_path)
        self._projection = DesktopProjectionKernel(self._engine, campaign_id, character_id)
        self._projection_lock = threading.RLock()
        self._endpoint_lock = threading.Lock()
        self._authoring_lock = threading.Lock()
        self._endpoint_override: dict[str, Any] | None = None

    def _campaign_id(self) -> str:
        with self._projection_lock:
            return self._projection.campaign_id

    def _snapshot_projection(self) -> dict[str, Any]:
        with self._projection_lock:
            return self._projection.snapshot()

    def _select_projected_character(self, character_id: str) -> dict[str, str]:
        with self._projection_lock:
            return self._projection.select_character(character_id)

    def _current_endpoint(self) -> dict[str, Any]:
        with self._endpoint_lock:
            return dict(self._endpoint_override or _endpoint_state())

    def bootstrap(self) -> dict[str, Any]:
        return {
            "ok": True,
            "desktop_version": DESKTOP_PROJECTION_VERSION,
            "campaign_id": self._campaign_id(),
            "operator_authoring": True,
            "generation_defaults": {
                "seed": "my-world",
                "namespace": "bootstrap",
                "mode": "bootstrap",
                "config": {
                    "location_count": 6,
                    "faction_count": 3,
                    "npcs_per_faction": 2,
                    "resource_count": 6,
                    "quest_count": 2,
                },
            },
        }

    def snapshot(self) -> dict[str, Any]:
        try:
            value = self._snapshot_projection()
            engine_state = "READY"
        except Exception:
            value = {
                "schema": DESKTOP_PROJECTION_VERSION,
                "campaign_id": self._campaign_id(),
                "campaign": None,
                "mode": "DISCONNECTED",
                "presentation": {"narration": "", "choices": []},
                "player": None,
                "location": None,
                "world_map": {"locations": [], "links": [], "current_location_id": None},
                "combat": None,
                "quests": [],
                "inventory": [],
                "known_npcs": [],
                "known_factions": [],
                "known_relationships": [],
                "journal": {"quests": [], "accepted_presentation_id": None},
                "investigation": {"leads": [], "note": "Local campaign is not ready."},
            }
            engine_state = "OFFLINE"
        endpoint = self._current_endpoint()
        value["states"] = {
            "engine": engine_state,
            "desktop": "READY",
            "gpt_link": endpoint.get("status", "NOT_CONFIGURED"),
        }
        value["connection"] = endpoint
        return value

    def select_character(self, character_id: str) -> dict[str, Any]:
        try:
            return {"ok": True, **self._select_projected_character(str(character_id))}
        except Exception:
            return _error("INVALID_CHARACTER", "That character is not available.")

    def copy_text(self, value: str) -> dict[str, Any]:
        if not isinstance(value, str) or len(value) > 12_000:
            return _error("INVALID_TEXT", "Text must contain at most 12,000 characters.")
        try:
            from world_engine_startup import clipboard_write

            copied = bool(clipboard_write(value))
            return {
                "ok": copied,
                "message": "Copied." if copied else "Copy was unavailable; select the text and press Ctrl+C.",
            }
        except Exception:
            return _error("COPY_UNAVAILABLE", "Copy was unavailable; select the text and press Ctrl+C.")

    def open_external(self, target: str) -> dict[str, Any]:
        if target != "ngrok_dashboard":
            return _error("TARGET_NOT_ALLOWED", "That external target is not allowed.")
        try:
            from world_engine_startup import AUTHTOKEN_URL

            opened = bool(webbrowser.open(AUTHTOKEN_URL, new=2))
            return {
                "ok": opened,
                "message": "Opened the official ngrok dashboard." if opened else "Could not open the browser.",
            }
        except Exception:
            return _error("OPEN_FAILED", "Could not open the official ngrok dashboard.")

    def configure_ngrok(self, token: str) -> dict[str, Any]:
        if not isinstance(token, str) or len(token) > 512:
            return _error("INVALID_TOKEN", "The ngrok token format is invalid.")
        if not self._endpoint_lock.acquire(blocking=False):
            return _error("ENDPOINT_BUSY", "Another endpoint operation is still running.")
        try:
            return self._configure_ngrok_unlocked(token)
        finally:
            self._endpoint_lock.release()

    def _configure_ngrok_unlocked(self, token: str) -> dict[str, Any]:
        if not isinstance(token, str) or len(token) > 512:
            return _error("INVALID_TOKEN", "The ngrok token format is invalid.")
        try:
            from world_engine_startup import (
                EndpointStatus,
                configure_ngrok_token_once,
                ensure_launcher_config,
                switch_to_ngrok_endpoint_outcome,
            )

            configured = configure_ngrok_token_once(token)
            if configured.get("status") != EndpointStatus.READY.value:
                current = _safe_endpoint_result(_endpoint_state())
                current["error_code"] = str(configured.get("error_code") or "NGROK_AUTH_INVALID")[:100]
                current["message"] = (
                    "ngrok authorization did not complete; the active endpoint provider was not changed."
                )
                self._endpoint_override = current
                return {"ok": False, **current}
            data = persistent_data_dir()
            api_key, _created = ensure_launcher_config(data)
            outcome = switch_to_ngrok_endpoint_outcome(
                ROOT,
                data,
                api_key,
                allow_download=True,
            )
            switched = outcome.get(
                "status"
            ) == EndpointStatus.READY.value and outcome.get("provider") == "ngrok_user"
            safe_outcome = _safe_endpoint_result(outcome)
            self._endpoint_override = safe_outcome
            return {
                "ok": switched,
                **safe_outcome,
                "token_fingerprint": _safe_fingerprint(configured.get("token_fingerprint")),
                "message": safe_outcome.get("message")
                or (
                    "ngrok is authorized and the GPT link is ready."
                    if switched
                    else "Authorization was saved. Use Retry after the endpoint becomes available."
                ),
            }
        except Exception:
            return _error(
                "NGROK_SETUP_FAILED",
                "ngrok setup did not complete; local World Engine remains available.",
            )

    def retry_endpoint(self) -> dict[str, Any]:
        if not self._endpoint_lock.acquire(blocking=False):
            return _error("ENDPOINT_BUSY", "Another endpoint operation is still running.")
        try:
            return self._retry_endpoint_unlocked()
        finally:
            self._endpoint_lock.release()

    def _retry_endpoint_unlocked(self) -> dict[str, Any]:
        try:
            from world_engine_startup import (
                EndpointStatus,
                ensure_endpoint_outcome,
                ensure_launcher_config,
            )

            data = persistent_data_dir()
            api_key, _created = ensure_launcher_config(data)
            outcome = ensure_endpoint_outcome(
                ROOT,
                data,
                api_key,
                interactive=False,
                allow_download=True,
                status=lambda _message: None,
            )
            safe_outcome = _safe_endpoint_result(outcome)
            self._endpoint_override = safe_outcome
            return {"ok": outcome.get("status") == EndpointStatus.READY.value, **safe_outcome}
        except Exception:
            return _error("ENDPOINT_RETRY_FAILED", "The GPT link is still unavailable; local play is unaffected.")

    def acknowledge_action_reimport(self) -> dict[str, Any]:
        if not self._endpoint_lock.acquire(blocking=False):
            return _error("ENDPOINT_BUSY", "Another endpoint operation is still running.")
        try:
            from world_engine_permanent_endpoint import acknowledge_action_reimport
            acknowledge_action_reimport(data=persistent_data_dir())
            current = _safe_endpoint_result(_endpoint_state())
            self._endpoint_override = current
            return {"ok": True, **current}
        except Exception:
            return _error("ACKNOWLEDGE_FAILED", "The schema reminder could not be acknowledged.")
        finally:
            self._endpoint_lock.release()

    @staticmethod
    def _closed_spec(spec: Mapping[str, Any]) -> dict[str, Any]:
        supplied = dict(spec)
        unknown = sorted(set(supplied) - _SPEC_KEYS)
        if unknown:
            raise ValueError(f"unknown generation fields: {', '.join(unknown)}")
        encoded = json.dumps(supplied, separators=(",", ":"), ensure_ascii=False)
        if len(encoded.encode("utf-8")) > 16_000:
            raise ValueError("generation request is too large")
        return supplied

    def authoring(self, action: str, batch_id: str, spec: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if action not in _AUTHOR_ACTIONS:
            return _error("ACTION_NOT_ALLOWED", "Unknown authoring action.")
        if not isinstance(batch_id, str) or not _ID_RE.fullmatch(batch_id):
            return _error("INVALID_BATCH", "Batch IDs use 1-100 letters, digits, dot, colon, dash, or underscore.")
        if not isinstance(spec, Mapping):
            return _error("INVALID_SPEC", "Generation settings must be an object.")
        if not self._authoring_lock.acquire(blocking=False):
            return _error("AUTHORING_BUSY", "Another authoring stage is still running.")
        try:
            closed = self._closed_spec(spec)
            campaign_id = self._campaign_id()
            if action == "stage":
                seed = closed.get("seed", "my-world")
                namespace = str(closed.get("namespace") or "bootstrap")
                mode = str(closed.get("mode") or "bootstrap")
                config = closed.get("config") or {}
                if not isinstance(config, dict):
                    raise ValueError("config must be an object")
                revision = self._engine.get_campaign(campaign_id)["revision"]
                result = self._engine.stage_generated_world(
                    campaign_id,
                    batch_id,
                    seed,
                    config,
                    namespace=namespace,
                    mode=mode,
                    expected_revision=int(revision),
                )
                manifest = result["generation"]["manifest"]
                batch = result["batch"]
                raw_counts = manifest.get("counts") if isinstance(manifest, Mapping) else {}
                safe_counts = {
                    key: max(0, int(raw_counts.get(key, 0)))
                    for key in _PUBLIC_GENERATION_COUNT_KEYS
                    if isinstance(raw_counts, Mapping)
                    and isinstance(raw_counts.get(key, 0), int)
                    and not isinstance(raw_counts.get(key, 0), bool)
                }
                return {
                    "ok": True,
                    "action": action,
                    "batch_id": batch_id,
                    "status": batch.get("status"),
                    "replayed": bool(batch.get("replayed", False)),
                    "manifest": {"counts": safe_counts},
                }
            if action == "validate":
                result = self._engine.author_validate(campaign_id, batch_id)
                return {
                    "ok": bool(result.get("valid")),
                    "action": action,
                    "batch_id": batch_id,
                    "status": result.get("status"),
                    "valid": bool(result.get("valid")),
                    "counts": result.get("counts", {}),
                    "errors": list(result.get("errors") or [])[:100],
                }
            if action == "dry_run":
                days = int(closed.get("days", 30))
                if not 1 <= days <= 365:
                    raise ValueError("dry-run days must be 1..365")
                result = self._engine.author_dry_run(campaign_id, batch_id, days=days)
                return {
                    "ok": bool(result.get("passed")),
                    "action": action,
                    "batch_id": batch_id,
                    "status": result.get("status"),
                    "passed": bool(result.get("passed")),
                    "days": days,
                    "metrics": result.get("metrics", {}),
                    "errors": list(result.get("errors") or [])[:100],
                }
            result = self._engine.author_promote(campaign_id, batch_id)
            return {
                "ok": result.get("status") == "promoted",
                "action": action,
                "batch_id": batch_id,
                "status": result.get("status"),
                "revision": result.get("revision"),
                "digest": result.get("digest"),
            }
        except (KeyError, TypeError, ValueError):
            return _error("AUTHORING_REJECTED", "Authoring input or staged state was rejected.")
        except Exception:
            return _error("AUTHORING_FAILED", "Authoring did not complete; no success is claimed.")
        finally:
            self._authoring_lock.release()


class AssetHandler(BaseHTTPRequestHandler):
    server_version = "WorldEngineCompanion/5.1.1"

    def do_GET(self) -> None:
        route = self.path.split("?", 1)[0]
        if route == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        asset = _ASSETS.get(route)
        if asset is None:
            self.send_error(404)
            return
        name, content_type = asset
        path = ASSET_ROOT / name
        try:
            body = path.read_bytes()
        except OSError:
            self.send_error(503)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", _CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *args: Any) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser(description="World Engine 5.1.1 standalone desktop companion")
    parser.add_argument("--campaign", default=os.environ.get("WORLD_ENGINE_CAMPAIGN", "default"))
    parser.add_argument("--character", default=os.environ.get("WORLD_ENGINE_CHARACTER"))
    args = parser.parse_args()
    db_path = Path(os.environ.get("WORLD_ENGINE_DB", str(DEFAULT_DB)))
    if not ASSET_ROOT.is_dir():
        raise SystemExit("Companion UI assets are missing.")

    data = persistent_data_dir()
    from world_engine_startup import (
        claim_companion_instance,
        release_companion_instance,
    )
    claim = claim_companion_instance(
        data,
        entrypoint=Path(__file__),
        executable=Path(sys.executable),
    )
    if claim is None:
        return 0
    server: ThreadingHTTPServer | None = None
    try:
        try:
            import webview
        except ImportError as exc:
            raise SystemExit("Install requirements-companion.txt (pywebview) first.") from exc
        api = CompanionApi(db_path, args.campaign, args.character)
        server = ThreadingHTTPServer(("127.0.0.1", 0), AssetHandler)
        host, port = server.server_address[:2]
        thread = threading.Thread(target=server.serve_forever, name="world-engine-ui-assets", daemon=True)
        thread.start()
        storage = data / "companion_webview"
        storage.mkdir(parents=True, exist_ok=True)
        webview.create_window(
            "World Engine Companion",
            f"http://{host}:{port}/",
            js_api=api,
            width=1320,
            height=840,
            min_size=(760, 560),
            background_color="#0a0d12",
        )
        webview.start(
            gui="edgechromium" if os.name == "nt" else None,
            debug=False,
            private_mode=False,
            storage_path=str(storage),
        )
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        release_companion_instance(claim)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
