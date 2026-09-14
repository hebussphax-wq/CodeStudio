#!/usr/bin/env python3
"""v0.3.1 safety tests – no Ollama required."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import studio  # noqa: E402


class SafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name) / "ws"
        self.ws.mkdir()
        self.backups = Path(self.tmp.name) / "backups"
        self.backups.mkdir()
        self._ws = studio.WS
        self._bk = studio.BACKUPS
        studio.WS = self.ws.resolve()
        studio.BACKUPS = self.backups.resolve()
        studio.CFG = dict(studio.CFG)
        studio.CFG["context_max_bytes_per_file"] = 80
        studio.CFG["enforce_plan_scope"] = True
        studio._LAST_READ_META = {}

    def tearDown(self) -> None:
        studio.WS = self._ws
        studio.BACKUPS = self._bk
        self.tmp.cleanup()

    def test_casefold_blocks_git_dir(self) -> None:
        with self.assertRaises(ValueError):
            studio.safe_path(".GIT/config", for_write=True)
        with self.assertRaises(ValueError):
            studio.safe_path("src/../secret.py", for_write=True)

    def test_truncated_write_blocked(self) -> None:
        target = self.ws / "big.py"
        target.write_text("x" * 400, encoding="utf-8")
        studio.read_files(["big.py"])
        self.assertTrue(studio._LAST_READ_META["big.py"]["truncated"])
        with self.assertRaises(ValueError) as ctx:
            studio.normalize_edits([{"path": "big.py", "op": "write", "content": "tiny"}])
        self.assertIn("gekürzt", str(ctx.exception).lower() + str(ctx.exception))

    def test_plan_scope_denied(self) -> None:
        plan = {"files": ["a.py"]}
        edits = [{"path": "b.py", "op": "write", "content": "nope"}]
        with self.assertRaises(ValueError):
            studio.enforce_plan_scope(edits, plan)

    def test_transaction_rollback_new_and_old(self) -> None:
        old = self.ws / "keep.py"
        old.write_text("ORIGINAL", encoding="utf-8")
        tx = studio.WorkspaceTransaction("t1")
        tx.begin()
        tx.apply([
            {"path": "keep.py", "op": "write", "content": "CHANGED"},
            {"path": "new.py", "op": "write", "content": "NEW"},
        ])
        self.assertEqual(old.read_text(encoding="utf-8"), "CHANGED")
        self.assertTrue((self.ws / "new.py").is_file())
        restored = tx.rollback()
        self.assertEqual(set(restored), {"keep.py", "new.py"})
        self.assertEqual(old.read_text(encoding="utf-8"), "ORIGINAL")
        self.assertFalse((self.ws / "new.py").exists())

    def test_atomic_write_replaces(self) -> None:
        p = self.ws / "f.txt"
        studio.atomic_write_text(p, "hello")
        self.assertEqual(p.read_text(encoding="utf-8"), "hello")

    def test_tests_use_argv_not_shell(self) -> None:
        studio.CFG["test_command"] = ""
        studio.CFG["tests"] = [{
            "name": "unit",
            "argv": [sys.executable, "-c", "print('ok')"],
            "timeout_sec": 30,
        }]
        result = studio.run_tests()
        assert result is not None
        self.assertEqual(result["returncode"], 0)
        self.assertIn("ok", result["output"])


if __name__ == "__main__":
    unittest.main()
