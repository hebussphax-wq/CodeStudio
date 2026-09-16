"""SoftKI: sanitize out-of-range module test indices after Quantor promote bind."""
from __future__ import annotations

import copy
import unittest

from autonomy import AutonomousRun
from director import sanitize_module_test_indices, validate_director


def _plan_with_oob_tests():
    return {
        "acceptance": {
            "checks": [
                {"name": "solutions", "argv": ["python", "solutions.py"]},
                {"name": "unittest", "argv": ["python", "-m", "unittest", "discover"]},
            ],
            "prose": ["solutions and unittest discover"],
        },
        "assumptions": [],
        "questions": [],
        "modules": [
            {
                "id": "impl",
                "contract": "solutions.py exports the required helpers used by the discover suite.",
                "outcomes": [
                    "Importing solutions succeeds and exposes the helpers the tests call."
                ],
                "files": ["solutions.py"],
                "references": [],
                "depends_on": [],
                # Planner hallucination after bind: only profiles 0..1 exist.
                "tests": [0, 2, 99],
            }
        ],
    }


class TestIndexSanitize(unittest.TestCase):
    def test_autonomy_helper_filters_oob(self):
        plan = _plan_with_oob_tests()
        AutonomousRun.sanitize_module_test_indices(plan, test_count=2)
        self.assertEqual(plan["modules"][0]["tests"], [0])

    def test_director_sanitize_then_validate_allow_pending(self):
        plan = _plan_with_oob_tests()
        # Empty after filter of all-oob still OK with allow_pending inside validate_director.
        plan2 = copy.deepcopy(plan)
        plan2["modules"][0]["tests"] = [2, 99]
        sanitize_module_test_indices(plan2, 2)
        self.assertEqual(plan2["modules"][0]["tests"], [])

        plan = _plan_with_oob_tests()
        flow = validate_director(
            plan,
            2,
            6,
            protected=[],
            existing=[],
            final_tests=False,
        )
        self.assertEqual(flow["modules"][0]["tests"], [0])

    def test_validate_passes_with_oob_indices_sanitized(self):
        plan = _plan_with_oob_tests()
        # Mutate through validate_director defense-in-depth path (no pre-call).
        raw = copy.deepcopy(plan)
        self.assertEqual(raw["modules"][0]["tests"], [0, 2, 99])
        flow = validate_director(raw, 2, 6, [], [], final_tests=True)
        # Non-final modules keep sanitized; final module forced to all indices when final_tests.
        self.assertEqual(flow["modules"][0]["tests"], [0, 1])


if __name__ == "__main__":
    unittest.main()
