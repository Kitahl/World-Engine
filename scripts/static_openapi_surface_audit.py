#!/usr/bin/env python3
"""Statically audit the GPT Action surface without importing the application.

This is deliberately weaker than exporting and validating FastAPI's generated
OpenAPI document. It exists so a source-only review can still detect duplicate
operation IDs and drift from the shared public Action allowlist.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app.py"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from world_engine.openapi_compat import PUBLIC_ACTION_OPERATION_IDS


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
    source_ids = {item["operation_id"] for item in source}
    public = [item for item in source if item["operation_id"] in PUBLIC_ACTION_OPERATION_IDS]
    ids = [item["operation_id"] for item in public]
    checked_in_ids = checked_in_openapi_operation_ids()
    duplicates = sorted({value for value in ids if ids.count(value) > 1})
    missing_allowlisted_source_ids = sorted(PUBLIC_ACTION_OPERATION_IDS - source_ids)
    missing_public_ids = sorted(PUBLIC_ACTION_OPERATION_IDS - set(ids))
    extra_public_ids = sorted(set(ids) - PUBLIC_ACTION_OPERATION_IDS)
    checked_in_missing_ids = sorted(PUBLIC_ACTION_OPERATION_IDS - checked_in_ids)
    checked_in_extra_ids = sorted(checked_in_ids - PUBLIC_ACTION_OPERATION_IDS)
    report = {
        "audit_kind": "static_ast_only",
        "runtime_openapi_verified": False,
        "checked_in_openapi_static_checked": (ROOT / "openapi_actions.json").is_file(),
        "source_operations": len(source),
        "curated_gpt_operations": len(public),
        "maximum_curated_operations": 30,
        "allowlisted_operation_ids": len(PUBLIC_ACTION_OPERATION_IDS),
        "missing_allowlisted_source_operation_ids": missing_allowlisted_source_ids,
        "missing_public_operation_ids": missing_public_ids,
        "extra_public_operation_ids": extra_public_ids,
        "checked_in_openapi_missing_operation_ids": checked_in_missing_ids,
        "checked_in_openapi_extra_operation_ids": checked_in_extra_ids,
        "duplicate_operation_ids": duplicates,
        "max_operation_id_length": max((len(value) for value in ids), default=0),
        "passed": (
            len(public) <= 30
            and not duplicates
            and not missing_allowlisted_source_ids
            and not missing_public_ids
            and not extra_public_ids
            and not checked_in_missing_ids
            and not checked_in_extra_ids
        ),
        "operations": public,
    }
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
