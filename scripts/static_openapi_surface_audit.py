#!/usr/bin/env python3
"""Statically audit the GPT Action surface without importing the application.

This is deliberately weaker than exporting and validating FastAPI's generated
OpenAPI document. It exists so a source-only review can still detect duplicate
operation IDs and drift in the launcher's curated 30-action filter.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app.py"
HIDDEN_PATHS = {
    "/api/snapshot",
    "/api/visual/profile/{entity_kind}/{entity_id}",
    "/api/visual/state/{scope_type}/{scope_id}",
    "/api/visual/recent",
    "/api/world/event",
}
HIDDEN_METHODS = {
    ("/api/visual/preferences", "get"),
    ("/api/visual/preferences", "post"),
    ("/api/visual/state", "post"),
}

FORBIDDEN_OPERATION_IDS = {
    "getWorldContext",
    "getEntity",
    "saveNpc",
    "saveFaction",
    "updateNpcState",
    "adjustFaction",
    "setWorldState",
    "configureSimulation",
    "authorWorldContent",
}


def literal(node: ast.AST | None):
    if isinstance(node, ast.Constant):
        return node.value
    return None


def operations() -> list[dict[str, str]]:
    tree = ast.parse(APP.read_text(encoding="utf-8"), filename=str(APP))
    found: list[dict[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                continue
            if not isinstance(decorator.func.value, ast.Name) or decorator.func.value.id != "app":
                continue
            method = decorator.func.attr.lower()
            if method not in {"get", "post", "put", "patch", "delete"} or not decorator.args:
                continue
            path = literal(decorator.args[0])
            keywords = {item.arg: literal(item.value) for item in decorator.keywords if item.arg}
            operation_id = keywords.get("operation_id")
            if not isinstance(path, str) or not isinstance(operation_id, str):
                continue
            if keywords.get("include_in_schema") is False:
                continue
            found.append({"path": path, "method": method, "operation_id": operation_id})
    return sorted(found, key=lambda item: (item["path"], item["method"]))


def checked_in_openapi_operation_ids() -> set[str]:
    path = ROOT / "openapi_actions.json"
    if not path.is_file():
        return set()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {
        operation["operationId"]
        for path_item in document.get("paths", {}).values()
        if isinstance(path_item, dict)
        for operation in path_item.values()
        if isinstance(operation, dict) and isinstance(operation.get("operationId"), str)
    }


def main() -> int:
    source = operations()
    public = [
        item
        for item in source
        if item["path"] not in HIDDEN_PATHS
        and (item["path"], item["method"]) not in HIDDEN_METHODS
    ]
    ids = [item["operation_id"] for item in public]
    checked_in_ids = checked_in_openapi_operation_ids()
    checked_in_forbidden = sorted(FORBIDDEN_OPERATION_IDS & checked_in_ids)
    forbidden_present = sorted(FORBIDDEN_OPERATION_IDS & set(ids))
    duplicates = sorted({value for value in ids if ids.count(value) > 1})
    required = {"resolveTurn", "publishPresentation"}
    missing_required = sorted(required - set(ids))
    report = {
        "audit_kind": "static_ast_only",
        "runtime_openapi_verified": False,
        "checked_in_openapi_static_checked": (ROOT / "openapi_actions.json").is_file(),
        "checked_in_openapi_forbidden_operation_ids_present": checked_in_forbidden,
        "source_operations": len(source),
        "curated_gpt_operations": len(public),
        "maximum_curated_operations": 30,
        "missing_required_operation_ids": missing_required,
        "duplicate_operation_ids": duplicates,
        "forbidden_operation_ids_present": forbidden_present,
        "max_operation_id_length": max((len(value) for value in ids), default=0),
        "passed": len(public) <= 30 and not duplicates and not missing_required and not forbidden_present and not checked_in_forbidden,
        "operations": public,
    }
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
