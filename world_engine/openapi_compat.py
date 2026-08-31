from __future__ import annotations

from typing import Any

PUBLIC_ACTION_OPERATION_IDS = frozenset(
    {
        "buildImageCue",
        "publishPresentation",
        "recordImageGeneration",
        "resolveTurn",
        "saveVisualProfile",
    }
)


def ensure_object_properties(node: Any) -> Any:
    """Normalize JSON/OpenAPI schemas for OpenAI tool-schema compatibility.

    OpenAI's tool/action schema validation requires every schema node whose
    ``type`` is ``object`` to contain an explicit ``properties`` member, even
    though ordinary JSON Schema permits it to be omitted.  FastAPI/Pydantic
    commonly emits ``{"type": "object", "additionalProperties": true}`` for
    ``dict[str, Any]`` responses, so normalize those nodes recursively.

    The function mutates and returns ``node``. It deliberately uses an empty
    properties mapping, preserving the original permissive additionalProperties
    semantics and therefore does not invent response fields that the API does
    not actually return.
    """
    if isinstance(node, dict):
        if node.get("type") == "object" and "properties" not in node:
            node["properties"] = {}
        for value in list(node.values()):
            ensure_object_properties(value)
    elif isinstance(node, list):
        for value in node:
            ensure_object_properties(value)
    return node


def object_schema_paths_missing_properties(node: Any, path: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    """Return paths to object schemas that are incompatible with OpenAI's validator."""
    missing: list[tuple[Any, ...]] = []
    if isinstance(node, dict):
        if node.get("type") == "object" and "properties" not in node:
            missing.append(path)
        for key, value in node.items():
            missing.extend(object_schema_paths_missing_properties(value, path + (key,)))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            missing.extend(object_schema_paths_missing_properties(value, path + (index,)))
    return missing


def mark_actions_non_consequential(schema: Any) -> Any:
    """Mark every exposed OpenAPI operation as non-consequential for GPT Actions.

    World Engine's exposed operations only mutate the user's game simulation. There
    are no purchase, messaging, account-deletion, or other external-world effects in
    the public action surface. Explicitly marking them false lets ChatGPT offer the
    user an "Always allow" path instead of treating ordinary POST gameplay calls as
    consequential by default.
    """
    if not isinstance(schema, dict):
        return schema
    for methods in (schema.get("paths") or {}).values():
        if not isinstance(methods, dict):
            continue
        for method, operation in methods.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete", "options", "head"}:
                continue
            if isinstance(operation, dict) and operation.get("operationId"):
                operation["x-openai-isConsequential"] = False
    return schema
