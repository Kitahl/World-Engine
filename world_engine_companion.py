"""Standalone Windows desktop companion for World Engine 4.7.

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
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping

from world_engine import WorldEngine
from world_engine.desktop import DESKTOP_PROJECTION_VERSION, DesktopProjectionKernel
from world_engine_connection_guard import load_json, persistent_data_dir

ROOT = Path(__file__).resolve().parent
ASSET_ROOT = ROOT / "companion_ui"
DEFAULT_DB = persistent_data_dir() / "world_engine.sqlite3"
_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,100}$")
_AUTHOR_ACTIONS = frozenset({"stage", "validate", "dry_run", "promote"})
_SPEC_KEYS = frozenset({"seed", "namespace", "mode", "config", "days"})
_ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
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


def _endpoint_state() -> dict[str, Any]:
    data = persistent_data_dir()
    startup = _bounded_json(data / "last_startup_result.json")
    supervisor = _bounded_json(data / "supervisor_status.json")
    endpoint = supervisor.get("endpoint") if isinstance(supervisor.get("endpoint"), dict) else None
    if endpoint is None:
        endpoint = startup.get("endpoint") if isinstance(startup.get("endpoint"), dict) else {}
    status = str(endpoint.get("status") or "NOT_CONFIGURED")
    return {
        "status": status,
        "provider": str(endpoint.get("provider") or "") or None,
        "public_url": str(endpoint.get("public_url") or "")[:2_048] or None,
        "error_code": str(endpoint.get("error_code") or "")[:100] or None,
        "message": str(endpoint.get("message") or "")[:500] or None,
        "retryable": bool(endpoint.get("retryable", status != "READY")),
    }


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
        self._authoring_lock = threading.Lock()
        self._endpoint_override: dict[str, Any] | None = None

    def bootstrap(self) -> dict[str, Any]:
        return {
            "ok": True,
            "desktop_version": DESKTOP_PROJECTION_VERSION,
            "campaign_id": self._projection.campaign_id,
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
            value = self._projection.snapshot()
            engine_state = "READY"
        except (KeyError, OSError, ValueError):
            value = {
                "schema": DESKTOP_PROJECTION_VERSION,
                "campaign_id": self._projection.campaign_id,
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
        endpoint = dict(self._endpoint_override or _endpoint_state())
        value["states"] = {
            "engine": engine_state,
            "desktop": "READY",
            "gpt_link": endpoint.get("status", "NOT_CONFIGURED"),
        }
        value["connection"] = endpoint
        return value

    def select_character(self, character_id: str) -> dict[str, Any]:
        try:
            return {"ok": True, **self._projection.select_character(str(character_id))}
        except (KeyError, ValueError):
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
        try:
            from world_engine_startup import (
                EndpointStatus,
                configure_ngrok_token_once,
                ensure_endpoint_outcome,
                ensure_launcher_config,
            )

            configured = configure_ngrok_token_once(token)
            if configured.get("status") != EndpointStatus.READY.value:
                self._endpoint_override = dict(configured)
                return {"ok": False, **configured}
            data = persistent_data_dir()
            api_key, _created = ensure_launcher_config(data)
            outcome = ensure_endpoint_outcome(
                ROOT,
                data,
                api_key,
                interactive=False,
                allow_download=False,
                status=lambda _message: None,
            )
            self._endpoint_override = dict(outcome)
            return {
                "ok": outcome.get("status") == EndpointStatus.READY.value,
                **outcome,
                "token_fingerprint": configured.get("token_fingerprint"),
                "message": outcome.get("message")
                or (
                    "ngrok is authorized and the GPT link is ready."
                    if outcome.get("status") == EndpointStatus.READY.value
                    else "Authorization was saved. Use Retry after the endpoint becomes available."
                ),
            }
        except Exception:
            return _error(
                "NGROK_SETUP_FAILED",
                "ngrok setup did not complete; local World Engine remains available.",
            )

    def retry_endpoint(self) -> dict[str, Any]:
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
                allow_download=False,
                status=lambda _message: None,
            )
            self._endpoint_override = dict(outcome)
            return {"ok": outcome.get("status") == EndpointStatus.READY.value, **outcome}
        except Exception:
            return _error("ENDPOINT_RETRY_FAILED", "The GPT link is still unavailable; local play is unaffected.")

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
            campaign_id = self._projection.campaign_id
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
                return {
                    "ok": True,
                    "action": action,
                    "batch_id": batch_id,
                    "status": batch.get("status"),
                    "replayed": bool(batch.get("replayed", False)),
                    "manifest": manifest,
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
        except (KeyError, TypeError, ValueError) as exc:
            return _error("AUTHORING_REJECTED", str(exc))
        except Exception:
            return _error("AUTHORING_FAILED", "Authoring did not complete; no success is claimed.")
        finally:
            self._authoring_lock.release()


class AssetHandler(BaseHTTPRequestHandler):
    server_version = "WorldEngineCompanion/4.5"

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
    parser = argparse.ArgumentParser(description="World Engine 4.5 standalone desktop companion")
    parser.add_argument("--campaign", default=os.environ.get("WORLD_ENGINE_CAMPAIGN", "default"))
    parser.add_argument("--character", default=os.environ.get("WORLD_ENGINE_CHARACTER"))
    args = parser.parse_args()
    db_path = Path(os.environ.get("WORLD_ENGINE_DB", str(DEFAULT_DB)))
    if not ASSET_ROOT.is_dir():
        raise SystemExit("Companion UI assets are missing.")
    try:
        import webview
    except ImportError as exc:
        raise SystemExit("Install requirements-companion.txt (pywebview) first.") from exc

    api = CompanionApi(db_path, args.campaign, args.character)
    server = ThreadingHTTPServer(("127.0.0.1", 0), AssetHandler)
    host, port = server.server_address[:2]
    thread = threading.Thread(target=server.serve_forever, name="world-engine-ui-assets", daemon=True)
    thread.start()
    storage = persistent_data_dir() / "companion_webview"
    storage.mkdir(parents=True, exist_ok=True)
    try:
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
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
