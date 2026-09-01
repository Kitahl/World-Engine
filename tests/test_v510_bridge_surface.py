"""World Engine 5.1.0 — pywebview bridge export-surface and confidentiality gates.

Why this file exists
--------------------
``CompanionApi`` is handed to pywebview as ``js_api``. pywebview does not export
only the methods declared on that class: ``webview.util.inject_pywebview`` walks
the object with a nested ``get_functions`` helper that **recurses into any public
attribute that is a non-callable object with a ``__module__``**. A public
``self.engine`` therefore publishes every public ``WorldEngine`` method to
JavaScript as ``engine.<name>``.

Testing ``dir(CompanionApi)`` cannot see that, because the leak comes from
instance attributes, not the class. So these tests execute **pywebview's own
walker source**, extracted from the installed package at run time, rather than a
paraphrase of it. If upstream changes the algorithm, extraction fails loudly
instead of silently certifying a stale assumption.
"""

from __future__ import annotations

import ast
import inspect
import json
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import world_engine_companion as companion  # noqa: E402

# The complete set of bridge functions JavaScript may reach. Anything else is a
# finding, not a preference.
ALLOWED_EXPORTS = frozenset(
    {
        "authoring",
        "bootstrap",
        "configure_ngrok",
        "copy_text",
        "open_external",
        "retry_endpoint",
        "select_character",
        "snapshot",
    }
)

# Substrings that must never appear as a key anywhere in a bridge result.
FORBIDDEN_RESULT_KEYS = (
    "internal_state",
    "npc_cognition",
    "beliefs",
    "goals",
    "memory",
    "settings_json",
    "bible_json",
    "notes_json",
    "policies",
    "weights",
    "api_key",
    "admin_key",
    "ngrok_token",
    "authtoken",
    "password",
    "secret",
    "db_path",
    "sql",
)


def _pywebview_export_walker():
    """Return pywebview's real ``get_functions``, extracted from the installed lib.

    ``get_functions`` is nested inside ``inject_pywebview``, which spawns a
    thread and needs a live Window, so it cannot simply be imported or called.
    Instead the two nested helpers are lifted out of the upstream source and
    executed verbatim - the algorithm under test is pywebview's, not ours.
    """
    import webview.util as util

    source = textwrap.dedent(inspect.getsource(util.inject_pywebview))
    tree = ast.parse(source)
    func_def = tree.body[0]
    wanted = {"get_args", "get_functions"}
    picked = [n for n in func_def.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    if {n.name for n in picked} != wanted:
        raise AssertionError(
            "pywebview's export helpers were not found where expected; "
            "the installed pywebview changed and this gate must be re-derived"
        )
    module = ast.Module(body=picked, type_ignores=[])
    ast.fix_missing_locations(module)
    namespace: dict[str, object] = {"inspect": inspect, "logger": util.logger, "exposed_objects": []}
    exec(compile(module, "<pywebview-extract>", "exec"), namespace)  # noqa: S102
    return namespace["get_functions"], namespace


def exported_function_names(js_api: object) -> set[str]:
    """Exactly what pywebview would publish at ``window.pywebview.api``."""
    get_functions, namespace = _pywebview_export_walker()
    namespace["exposed_objects"].clear()
    return set(get_functions(js_api).keys())


def walk_keys(node, path="$"):
    if isinstance(node, dict):
        for key, value in node.items():
            yield path, str(key), value
            yield from walk_keys(value, f"{path}.{key}")
    elif isinstance(node, (list, tuple)):
        for index, value in enumerate(node):
            yield from walk_keys(value, f"{path}[{index}]")


class BridgeExportSurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.api = companion.CompanionApi(Path(self.tmp.name) / "surface.sqlite3", "default")

    def tearDown(self) -> None:
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def test_walker_extraction_matches_installed_pywebview(self) -> None:
        """The gate is only meaningful if it really ran upstream's algorithm."""
        get_functions, _ = _pywebview_export_walker()
        self.assertTrue(callable(get_functions))

    def test_exported_surface_is_exactly_the_allowlist(self) -> None:
        exported = exported_function_names(self.api)
        self.assertEqual(
            ALLOWED_EXPORTS,
            exported,
            f"pywebview export surface drifted; unexpected={sorted(exported - ALLOWED_EXPORTS)} "
            f"missing={sorted(ALLOWED_EXPORTS - exported)}",
        )

    def test_no_nested_engine_or_projection_namespace_is_reachable(self) -> None:
        exported = exported_function_names(self.api)
        leaked = sorted(n for n in exported if "." in n)
        self.assertEqual([], leaked, f"nested objects exported to JavaScript: {leaked}")

    def test_named_dangerous_methods_are_not_reachable(self) -> None:
        exported = exported_function_names(self.api)
        for banned in (
            "engine.get_internal_state_block",
            "projection.snapshot",
            "engine.snapshot",
            "engine.resolve_turn",
            "engine.rules_dispatch",
            "engine.world_systems_dispatch",
            "engine.advance_world",
            "engine.upsert_character",
            "engine.author_promote",
        ):
            self.assertNotIn(banned, exported)

    def test_engine_and_projection_are_private_attributes(self) -> None:
        public = {n for n in vars(self.api) if not n.startswith("_")}
        self.assertEqual(set(), public, f"public instance attributes leak to pywebview: {sorted(public)}")
        self.assertTrue(hasattr(self.api, "_engine"))
        self.assertTrue(hasattr(self.api, "_projection"))

    def test_a_public_attribute_would_be_caught(self) -> None:
        """Positive control.

        A gate that cannot fail proves nothing, so re-attach the engine publicly
        and confirm the walker reports the leak. Without this, the passing gate
        above could simply mean the walker returned nothing.
        """
        self.api.engine = self.api._engine  # type: ignore[attr-defined]
        try:
            exported = exported_function_names(self.api)
            leaked = [n for n in exported if n.startswith("engine.")]
            self.assertGreater(
                len(leaked), 20, "control failed: a public engine attribute did not leak, so the gate is blind"
            )
            self.assertIn("engine.get_internal_state_block", exported)
        finally:
            del self.api.engine


class BridgeResultConfidentialityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.api = companion.CompanionApi(Path(self.tmp.name) / "conf.sqlite3", "default")
        engine = self.api._engine  # type: ignore[attr-defined]
        engine.ensure_campaign("default", "Confidentiality")
        engine.upsert_location(
            "default", "keep", "Ash Keep", region="Marches", x=3.0, y=4.0,
            description="A keep.", state={"population": 900},
        )
        engine.upsert_character("default", "pc", "Wren", hp=12, max_hp=20, location="keep")
        engine.upsert_npc(
            "default", "spy", "Silent Ivo", location="keep", importance="major",
            beliefs=["CANARY_BELIEF_the steward is bought"],
            goals=["CANARY_GOAL_open the postern"],
            memory=[{"secret": "CANARY_MEMORY_tunnel under the chapel"}],
        )

    def tearDown(self) -> None:
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def _results(self) -> dict[str, object]:
        return {
            "bootstrap": self.api.bootstrap(),
            "snapshot": self.api.snapshot(),
            "select_character": self.api.select_character("pc"),
            "retry_endpoint": self.api.retry_endpoint(),
        }

    def test_no_forbidden_key_in_any_bridge_result(self) -> None:
        for name, payload in self._results().items():
            for path, key, _value in walk_keys(payload):
                lowered = key.lower()
                for banned in FORBIDDEN_RESULT_KEYS:
                    self.assertNotIn(
                        banned, lowered, f"{name}: forbidden key {key!r} at {path}"
                    )

    def test_private_cognition_canaries_never_serialize(self) -> None:
        canaries = ("CANARY_BELIEF", "CANARY_GOAL", "CANARY_MEMORY")
        for name, payload in self._results().items():
            blob = json.dumps(payload, default=str)
            for canary in canaries:
                self.assertNotIn(canary, blob, f"{name} leaked private NPC cognition ({canary})")

    def test_results_are_json_serializable(self) -> None:
        """pywebview serializes results to JSON; a non-serializable object would
        surface as an error string carrying a traceback."""
        for name, payload in self._results().items():
            try:
                json.dumps(payload)
            except TypeError as exc:  # pragma: no cover - failure path
                self.fail(f"{name} is not JSON-serializable: {exc}")

    def test_cross_campaign_state_is_not_reachable(self) -> None:
        engine = self.api._engine  # type: ignore[attr-defined]
        engine.ensure_campaign("other", "Other Realm")
        engine.upsert_location("other", "faraway", "CANARY_OTHER_CAMPAIGN_TOWN", region="Elsewhere", x=1.0, y=1.0)
        for name, payload in self._results().items():
            blob = json.dumps(payload, default=str)
            self.assertNotIn("CANARY_OTHER_CAMPAIGN_TOWN", blob, f"{name} leaked another campaign")

    def test_invalid_campaign_id_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            companion.CompanionApi(Path(self.tmp.name) / "bad.sqlite3", "../escape")

    def test_authoring_stage_returns_only_allowlisted_counts(self) -> None:
        canary = "AUTHORING_MANIFEST_SECRET"
        staged = {
            "generation": {
                "manifest": {
                    "counts": {"locations": 4, "npcs": 2, "secret_rows": 99},
                    "ids": {"npcs": [canary]},
                    "content_digest": canary,
                    "config": {"private": canary},
                }
            },
            "batch": {"status": "staged", "replayed": False},
        }
        with (
            mock.patch.object(self.api._engine, "get_campaign", return_value={"revision": 0}),
            mock.patch.object(self.api._engine, "stage_generated_world", return_value=staged),
        ):
            result = self.api.authoring(
                "stage",
                "safe-batch",
                {"seed": "s", "namespace": "bootstrap", "mode": "bootstrap", "config": {}},
            )
        self.assertTrue(result["ok"])
        self.assertEqual({"counts"}, set(result["manifest"]))
        self.assertEqual(4, result["manifest"]["counts"]["locations"])
        self.assertEqual(2, result["manifest"]["counts"]["npcs"])
        self.assertNotIn("secret_rows", result["manifest"]["counts"])
        self.assertNotIn(canary, json.dumps(result, sort_keys=True))

    def test_authoring_validation_error_is_generic(self) -> None:
        canary = "C:/private/AUTHOR_CANARY.sqlite3"
        with mock.patch.object(
            self.api._engine,
            "get_campaign",
            side_effect=ValueError(canary),
        ):
            result = self.api.authoring(
                "stage",
                "safe-batch",
                {"seed": "s", "namespace": "bootstrap", "mode": "bootstrap", "config": {}},
            )
        self.assertFalse(result["ok"])
        self.assertEqual("AUTHORING_REJECTED", result["code"])
        self.assertNotIn(canary, json.dumps(result, sort_keys=True))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
