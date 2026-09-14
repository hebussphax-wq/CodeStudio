import pathlib
import shutil
import tempfile
import unittest

from core import CodeStudioCore, WorkspaceTransaction, safe_path


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.ws = self.tmp / "workspace"; self.ws.mkdir()
        self.cfg = {
            "ollama_url": "http://127.0.0.1:1",
            "workspace": str(self.ws),
            "context_max_files": 40,
            "context_max_bytes_per_file": 100,
            "max_changed_files": 12,
            "enforce_plan_scope": True,
            "tests": [],
        }
        self.core = CodeStudioCore(self.tmp, self.cfg)

    def tearDown(self): shutil.rmtree(self.tmp, ignore_errors=True)
    def write(self, rel, text):
        p=self.ws/rel; p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(text.encode("utf-8"))

    def test_path_traversal_blocked(self):
        with self.assertRaises(ValueError): safe_path(self.ws,"../evil.py",write=True)

    def test_casefold_git_blocked(self):
        with self.assertRaises(ValueError): safe_path(self.ws,".GIT/hooks/x",write=True)

    def test_replace_keeps_rest(self):
        self.write("a.py","x=1\ny=2\nz=3\n")
        final,issues=self.core.validate_edits([{"path":"a.py","op":"replace","old_text":"y=2","new_text":"y=20","content":""}],{"a.py"})
        self.assertFalse(issues); self.assertEqual(final["a.py"],"x=1\ny=20\nz=3\n")

    def test_write_requires_full_read(self):
        self.write("a.py","hello\n")
        final,issues=self.core.validate_edits([{"path":"a.py","op":"write","old_text":"","new_text":"","content":"bye\n"}],{"a.py"})
        self.assertTrue(any("vollständigem Lesen" in x for x in issues)); self.assertEqual(final,{})

    def test_write_after_full_read(self):
        self.write("a.py","hello\n"); self.core.read_files(["a.py"])
        final,issues=self.core.validate_edits([{"path":"a.py","op":"write","old_text":"","new_text":"","content":"bye\n"}],{"a.py"})
        self.assertFalse(issues); self.assertEqual(final["a.py"],"bye\n")

    def test_scope_blocks_other_file(self):
        self.write("a.py","a\n"); self.write("b.py","b\n")
        final,issues=self.core.validate_edits([{"path":"b.py","op":"replace","old_text":"b","new_text":"x","content":""}],{"a.py"})
        self.assertTrue(any("Plan-Scope" in x for x in issues)); self.assertEqual(final,{})

    def test_transaction_rollback(self):
        self.write("a.py","old\n")
        tx=WorkspaceTransaction(self.ws,self.tmp/"backups","t"); tx.write("a.py","new\n"); tx.write("new.py","x\n"); tx.rollback()
        self.assertEqual((self.ws/"a.py").read_text(),"old\n"); self.assertFalse((self.ws/"new.py").exists())

if __name__=="__main__": unittest.main(verbosity=2)
