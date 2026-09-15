"""Unit tests for SoftKI plan_gate."""
from __future__ import annotations

import pathlib
import tempfile
import unittest

from plan_gate import (
    QUANTIFIER_RE,
    ensure_checks,
    lint_plan_acceptance,
    normalize_acceptance,
)


class PlanGateTests(unittest.TestCase):
    def test_normalize_list(self):
        acc = normalize_acceptance(["Datei existiert", "Test grün"])
        self.assertEqual(acc["checks"], [])
        self.assertEqual(len(acc["prose"]), 2)

    def test_normalize_object(self):
        acc = normalize_acceptance({
            "prose": ["ok"],
            "checks": [{"name": "unit", "argv": ["python", "-m", "unittest", "tests.test_plan_gate"]}],
        })
        self.assertEqual(len(acc["checks"]), 1)
        self.assertEqual(acc["checks"][0]["argv"][0], "python")

    def test_lint_rejects_quantifier_without_checks(self):
        plan = {"acceptance": ["Jede Leiter verbindet eine Plattform"]}
        with self.assertRaises(ValueError) as ctx:
            lint_plan_acceptance(plan)
        self.assertIn("SoftKI-Plan-Gate", str(ctx.exception))

    def test_lint_allows_quantifier_with_checks(self):
        plan = {
            "acceptance": {
                "prose": ["Jede Leiter verbindet eine Plattform"],
                "checks": [{"name": "inv", "argv": ["node", "--test", "t.cjs"]}],
            }
        }
        lint_plan_acceptance(plan)
        self.assertTrue(plan["acceptance"]["checks"])

    def test_lint_allows_plain_prose(self):
        plan = {"acceptance": ["health endpoint returns 200"]}
        lint_plan_acceptance(plan)

    def test_ensure_checks_from_plan_argv(self):
        with tempfile.TemporaryDirectory() as d:
            ws = pathlib.Path(d)
            plan = {
                "acceptance": {
                    "prose": [],
                    "checks": [{"name": "u", "argv": ["python", "-m", "unittest", "discover"]}],
                }
            }
            out = ensure_checks(ws, plan, existing_tests=[])
            self.assertEqual(len(out), 1)
            self.assertEqual(out[0]["name"], "u")

    def test_ensure_checks_merges_config(self):
        with tempfile.TemporaryDirectory() as d:
            ws = pathlib.Path(d)
            plan = {"acceptance": {"prose": [], "checks": []}}
            existing = [{"name": "cfg", "argv": ["python", "-m", "unittest", "tests.x"]}]
            out = ensure_checks(ws, plan, existing_tests=existing)
            self.assertEqual(out[0]["name"], "cfg")

    def test_quantifier_re_hits_german(self):
        self.assertTrue(QUANTIFIER_RE.search("erreichbar vom Start"))


if __name__ == "__main__":
    unittest.main()
