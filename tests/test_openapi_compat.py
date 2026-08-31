import json
import subprocess
import sys
import unittest
from pathlib import Path

from app import app
from world_engine.openapi_compat import (
    PUBLIC_ACTION_OPERATION_IDS,
    ensure_object_properties,
    object_schema_paths_missing_properties,
)


ROOT = Path(__file__).resolve().parents[1]


class OpenAIActionSchemaCompatibilityTests(unittest.TestCase):
    def test_recursive_normalizer_fixes_nested_objects(self):
        schema = {
            "type": "array",
            "items": {
                "anyOf": [
                    {"type": "object", "additionalProperties": True},
                    {"type": "array", "items": {"type": "object"}},
                ]
            },
        }
        self.assertTrue(object_schema_paths_missing_properties(schema))
        ensure_object_properties(schema)
        self.assertEqual([], object_schema_paths_missing_properties(schema))
        self.assertEqual({}, schema["items"]["anyOf"][0]["properties"])
        self.assertEqual({}, schema["items"]["anyOf"][1]["items"]["properties"])

    def test_exported_actions_schema_has_no_object_without_properties(self):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "export_openapi.py")],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, proc.returncode, proc.stderr)
        schema = json.loads((ROOT / "openapi_actions.json").read_text(encoding="utf-8"))
        self.assertEqual([], object_schema_paths_missing_properties(schema))
        operation_count = sum(
            1
            for methods in schema["paths"].values()
            for operation in methods.values()
            if isinstance(operation, dict) and operation.get("operationId")
        )
        self.assertLessEqual(operation_count, 30)
        exposed_operations = [
            operation
            for methods in schema["paths"].values()
            for operation in methods.values()
            if isinstance(operation, dict) and operation.get("operationId")
        ]
        self.assertTrue(exposed_operations)
        operation_ids = {operation["operationId"] for operation in exposed_operations}
        self.assertEqual(PUBLIC_ACTION_OPERATION_IDS, operation_ids)
        self.assertTrue({"resolveTurn", "publishPresentation"}.issubset(operation_ids))
        self.assertTrue(
            operation_ids.isdisjoint(
                {
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
            )
        )
        self.assertTrue(all(operation.get("x-openai-isConsequential") is False for operation in exposed_operations))

        turn = schema["paths"]["/api/turn"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]
        self.assertIn("properties", turn)


if __name__ == "__main__":
    unittest.main()
