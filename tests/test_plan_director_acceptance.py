"""SoftKI Planvertrag: director.validate_director accepts list OR {checks, prose}."""
from __future__ import annotations

import copy
import unittest

from director import DIRECTOR_SCHEMA, acceptance_rows_for_validation, validate_director


def _base_plan():
    return {
        "acceptance": ["Format doubled values returns readable string output"],
        "assumptions": [],
        "questions": [],
        "modules": [
            {
                "id": "values",
                "contract": "values.py exports double(n) returning n*2.",
                "outcomes": [
                    "Calling double(4) returns 8; negative inputs retain their sign."
                ],
                "files": ["values.py"],
                "references": ["test_app.py"],
                "depends_on": [],
                "tests": [],
            },
            {
                "id": "app",
                "contract": "app.py imports double from values and exports show(n) returning str(double(n)).",
                "outcomes": [
                    'Calling show(4) returns the string "8"; show(-2) returns "-4".'
                ],
                "files": ["app.py"],
                "references": ["test_app.py"],
                "depends_on": ["values"],
                "tests": [0],
            },
        ],
    }


class PlanDirectorAcceptanceTests(unittest.TestCase):
    def test_list_acceptance_ok(self):
        plan = _base_plan()
        flow = validate_director(
            plan, 1, 6, ["test_app.py"], ["test_app.py"]
        )
        self.assertEqual(flow["modules"][-1]["tests"], [0])
        self.assertIsInstance(plan["acceptance"], list)

    def test_object_with_prose_ok(self):
        plan = _base_plan()
        plan["acceptance"] = {
            "prose": ["Format doubled values returns readable string output"],
            "checks": [
                {
                    "name": "unit",
                    "argv": ["python", "-m", "unittest", "tests.test_app"],
                }
            ],
        }
        flow = validate_director(
            plan, 1, 6, ["test_app.py"], ["test_app.py"]
        )
        self.assertTrue(flow["modules"])
        # SoftKI object form must remain on the plan (not coerced to list).
        self.assertIsInstance(plan["acceptance"], dict)
        self.assertIn("checks", plan["acceptance"])
        self.assertIn("prose", plan["acceptance"])

    def test_object_checks_only_ok(self):
        plan = _base_plan()
        plan["acceptance"] = {
            "prose": [],
            "checks": [
                {"name": "unit", "argv": ["python", "-B", "test_app.py"]},
                {"name": "node", "path": "tests/test_app.cjs"},
            ],
        }
        flow = validate_director(
            plan, 1, 6, ["test_app.py"], ["test_app.py"]
        )
        self.assertTrue(flow["modules"])
        self.assertEqual(plan["acceptance"]["prose"], [])
        self.assertEqual(len(plan["acceptance"]["checks"]), 2)
        rows, kind = acceptance_rows_for_validation(plan["acceptance"])
        self.assertEqual(kind, "checks")
        self.assertEqual(rows, ["unit", "node"])

    def test_invalid_acceptance_rejected(self):
        plan = _base_plan()
        for bad in (
            None,
            {},
            {"prose": [], "checks": []},
            {"prose": "not-a-list", "checks": []},
            {"prose": [], "checks": [123]},
            "just-a-string",
            [],
        ):
            with self.subTest(bad=bad):
                p = copy.deepcopy(plan)
                p["acceptance"] = bad
                with self.assertRaises(ValueError) as ctx:
                    validate_director(
                        p, 1, 6, ["test_app.py"], ["test_app.py"]
                    )
                self.assertIn("acceptance", str(ctx.exception))

    def test_schema_allows_object_or_array(self):
        acc = DIRECTOR_SCHEMA["properties"]["acceptance"]
        self.assertIn("oneOf", acc)
        types = {branch.get("type") for branch in acc["oneOf"]}
        self.assertEqual(types, {"array", "object"})


if __name__ == "__main__":
    unittest.main()
