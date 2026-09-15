"""Hermetic SoftKI helper tests (no full AutonomousRun)."""
from __future__ import annotations

import json
import unittest

from plan_gate import ensure_checks, lint_plan_acceptance, normalize_acceptance


class SoftKIHelpers(unittest.TestCase):
    def test_empty_config_plus_plan_checks(self):
        plan = {
            "acceptance": {
                "prose": ["health ok"],
                "checks": [{"name": "h", "argv": ["python", "-c", "print(1)"]}],
            }
        }
        lint_plan_acceptance(plan)
        import pathlib, tempfile
        with tempfile.TemporaryDirectory() as d:
            out = ensure_checks(pathlib.Path(d), plan, existing_tests=[])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["argv"][0], "python")

    def test_numeric_comparison_payload_shape(self):
        # Mirrors fields numeric_failure_feedback reads from analyze_failure
        analysis = {
            "comparison": {"actual": "816", "expected": "380", "operator": "=="},
            "locations": [{"path": "levels.js", "line": 42}],
            "known": "AssertionError: 816 != 380",
        }
        parts = []
        if analysis.get("comparison"):
            parts.append("NUMERISCH: " + json.dumps(analysis["comparison"], ensure_ascii=False))
        self.assertIn("816", parts[0])
        self.assertIn("380", parts[0])


if __name__ == "__main__":
    unittest.main()
