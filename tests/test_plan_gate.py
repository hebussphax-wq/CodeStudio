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
    promote_executable_prose,
    rewrite_check_for_windows,
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



    def test_rewrite_test_f_on_nt(self):
        import os
        from unittest.mock import patch
        with patch.object(os, "name", "nt"):
            out = rewrite_check_for_windows(
                {"name": "exists", "argv": ["test", "-f", "hello_planvertrag.txt"]}
            )
        self.assertEqual(out.get("path"), "hello_planvertrag.txt")
        self.assertNotIn("argv", out)

    def test_rewrite_test_e_on_nt(self):
        import os
        from unittest.mock import patch
        with patch.object(os, "name", "nt"):
            out = rewrite_check_for_windows(
                {"name": "exists", "argv": ["test", "-e", "out/file.txt"]}
            )
        self.assertEqual(out["path"], "out/file.txt")

    def test_rewrite_grep_on_nt(self):
        import os
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            ws = pathlib.Path(d)
            (ws / "hello.txt").write_text("Planvertrag SoftKI", encoding="utf-8")
            with patch.object(os, "name", "nt"):
                out = rewrite_check_for_windows(
                    {"name": "grep", "argv": ["grep", "-F", "Planvertrag", "hello.txt"]},
                    workspace=ws,
                )
            self.assertIn("argv", out)
            self.assertTrue(out["argv"][0])  # sys.executable
            self.assertEqual(out["argv"][1], "-c")
            self.assertIn("Planvertrag", out["argv"][2])

    def test_ensure_checks_rewrites_test_f_on_nt(self):
        import os
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            ws = pathlib.Path(d)
            plan = {
                "acceptance": {
                    "prose": [],
                    "checks": [{"name": "f", "argv": ["test", "-f", "hello_planvertrag.txt"]}],
                }
            }
            with patch.object(os, "name", "nt"):
                out = ensure_checks(ws, plan, existing_tests=[])
            self.assertEqual(len(out), 1)
            self.assertEqual(out[0]["argv"][1], "-c")
            self.assertIn("is_file", out[0]["argv"][2])
            # acceptance stored as path form after normalize
            self.assertEqual(plan["acceptance"]["checks"][0].get("path"), "hello_planvertrag.txt")



    def test_missing_solutions_py_binds_deferred_exists(self):
        """Run-output path like solutions.py must bind without raise (coder creates later)."""
        with tempfile.TemporaryDirectory() as d:
            ws = pathlib.Path(d)
            plan = {
                "acceptance": {
                    "prose": [],
                    "checks": [{"name": "solutions", "path": "solutions.py"}],
                }
            }
            out = ensure_checks(ws, plan, existing_tests=[])
            self.assertEqual(len(out), 1)
            self.assertEqual(out[0]["argv"][1], "-c")
            self.assertIn("is_file", out[0]["argv"][2])
            # path appears inside the -c snippet (absolute); suffix is enough
            self.assertTrue(
                "solutions.py" in out[0]["argv"][2]
                or out[0]["argv"][2].endswith("solutions.py')")
            )

    def test_existing_tests_py_binds_unittest(self):
        with tempfile.TemporaryDirectory() as d:
            ws = pathlib.Path(d)
            tests = ws / "tests"
            tests.mkdir()
            (tests / "__init__.py").write_text("", encoding="utf-8")
            (tests / "foo.py").write_text(
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_ok(self):\n"
                "        self.assertTrue(True)\n",
                encoding="utf-8",
            )
            plan = {
                "acceptance": {
                    "prose": [],
                    "checks": [{"name": "foo", "path": "tests/foo.py"}],
                }
            }
            out = ensure_checks(ws, plan, existing_tests=[])
            self.assertEqual(len(out), 1)
            self.assertEqual(out[0]["argv"][:4], ["python", "-m", "unittest", "tests.foo"])

    def test_existing_non_test_py_binds_python_run(self):
        with tempfile.TemporaryDirectory() as d:
            ws = pathlib.Path(d)
            (ws / "script.py").write_text("print(1)\n", encoding="utf-8")
            plan = {
                "acceptance": {
                    "prose": [],
                    "checks": [{"name": "script", "path": "script.py"}],
                }
            }
            out = ensure_checks(ws, plan, existing_tests=[])
            self.assertEqual(len(out), 1)
            self.assertEqual(out[0]["argv"][0], "python")
            self.assertTrue(str(out[0]["argv"][1]).endswith("script.py"))


    def test_promote_unittest_prose_with_all_quantifier(self):
        """Empty checks + prose with unittest and word 'all' → promote, lint OK."""
        plan = {
            "acceptance": {
                "checks": [],
                "prose": ["python -m unittest discover -v passes all tests"],
            }
        }
        lint_plan_acceptance(plan)
        checks = plan["acceptance"]["checks"]
        self.assertTrue(checks, "expected promoted check")
        self.assertEqual(
            checks[0]["argv"],
            ["python", "-m", "unittest", "discover", "-v"],
        )
        # Quantifier-only residue must not remain without checks (lint already passed).
        self.assertTrue(plan["acceptance"]["checks"])

    def test_promote_executable_prose_direct(self):
        acc = {
            "checks": [],
            "prose": ["python -m unittest discover -v passes all tests"],
        }
        promote_executable_prose(acc)
        self.assertEqual(len(acc["checks"]), 1)
        self.assertEqual(acc["checks"][0]["argv"][0], "python")
        self.assertIn("unittest", acc["checks"][0]["argv"])

    def test_promote_path_exists_prose(self):
        acc = {"checks": [], "prose": ["solutions.py exists"]}
        promote_executable_prose(acc)
        self.assertEqual(len(acc["checks"]), 1)
        self.assertEqual(acc["checks"][0].get("path"), "solutions.py")

    def test_lint_still_rejects_pure_quantifier_prose(self):
        """Pure quantifier prose without runnable command still rejects."""
        plan = {"acceptance": {"checks": [], "prose": ["all tests must pass"]}}
        with self.assertRaises(ValueError) as ctx:
            lint_plan_acceptance(plan)
        self.assertIn("SoftKI-Plan-Gate", str(ctx.exception))

    def test_lint_rejects_german_quantifier_without_command(self):
        plan = {"acceptance": ["Jede Leiter verbindet eine Plattform"]}
        with self.assertRaises(ValueError):
            lint_plan_acceptance(plan)


if __name__ == "__main__":
    unittest.main()
