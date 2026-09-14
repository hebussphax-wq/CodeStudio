import copy
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch
from core import CodeStudioCore
from safety import WorkspaceTransaction, safe_path

class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.ws = self.root / 'workspace'; self.ws.mkdir()
        self.file = self.ws / 'calc.py'; self.file.write_bytes(b'def add(a,b):\r\n    return a-b\r\n')
        self.core = CodeStudioCore(self.root, {'workspace':str(self.ws),'tests':[],'context_max_bytes_per_file':20000})

    def tearDown(self): self.tmp.cleanup()

    def proposal(self, edits=None):
        edits = edits or [{'path':'calc.py','op':'replace','old_text':'return a-b','new_text':'return a+b','content':''}]
        replies = [{'plan':['Fix addition'],'files':list(dict.fromkeys(e['path'] for e in edits)),'questions':[],'acceptance':['2+3=5']},
                   {'edits':edits,'notes':''},{'verdict':'ok','issues':[],'summary':'Reviewed'}]
        with patch.object(self.core,'chat',side_effect=replies): return self.core.analyze('Fix addition','fixture')

    def test_stale_file_rejected_before_any_write(self):
        result = self.proposal()
        self.file.write_bytes(b'user change')
        with self.assertRaisesRegex(ValueError,'Vorschau verändert'): self.core.apply(result)
        self.assertEqual(self.file.read_bytes(),b'user change')

    def test_workspace_switch_invalidates_proposal(self):
        result = self.proposal(); other = self.root / 'other'; other.mkdir()
        self.core.set_workspace(str(other))
        with self.assertRaises(ValueError): self.core.apply(result)
        self.assertEqual(list(other.iterdir()),[])

    def test_edited_proposal_rejected(self):
        result = self.proposal(); result.final_state['calc.py']='malicious'
        with self.assertRaisesRegex(ValueError,'Vorschlag wurde'): self.core.apply(result)

    def test_replay_rejected(self):
        result = self.proposal(); receipt = self.core.apply(result)
        self.assertEqual(receipt['status'],'applied-untested')
        with self.assertRaises(ValueError): self.core.apply(result)

    def test_actual_tests_pass_and_bytes_preserved(self):
        script = self.ws / 'check.py'; script.write_text('from calc import add\nassert add(2,3)==5\nprint("addition verified")\n')
        self.core.config['tests']=[{'argv':[sys.executable,str(script)]}]
        receipt = self.core.apply(self.proposal())
        self.assertEqual(receipt['status'],'applied')
        self.assertIn('addition verified',receipt['test']['output'])
        self.assertEqual(self.file.read_bytes(),b'def add(a,b):\r\n    return a+b\r\n')

    def test_real_failing_test_rolls_back_new_files_and_directories(self):
        checker = self.root / 'fail.py'; checker.write_text('raise SystemExit(7)')
        self.core.config['tests']=[{'argv':[sys.executable,str(checker)]}]
        original = self.file.read_bytes()
        result = self.proposal([{'path':'calc.py','op':'replace','old_text':'return a-b','new_text':'return a+b'},
                               {'path':'new/sub/a.py','op':'create','content':'x=1\n'}])
        receipt = self.core.apply(result)
        self.assertEqual(receipt['status'],'rolled-back')
        self.assertEqual(receipt['test']['returncode'],7)
        self.assertEqual(self.file.read_bytes(),original)
        self.assertFalse((self.ws / 'new').exists())
        self.assertEqual(json.loads((self.core.runs / (receipt['tag']+'.json')).read_text())['status'],'rolled-back')

    def test_timeout_rolls_back(self):
        checker = self.root / 'slow.py'; checker.write_text('import time\ntime.sleep(5)')
        self.core.config['tests']=[{'argv':[sys.executable,str(checker)],'timeout_sec':1}]
        original = self.file.read_bytes(); receipt=self.core.apply(self.proposal())
        self.assertEqual(receipt['status'],'rolled-back'); self.assertEqual(receipt['test']['returncode'],124)
        self.assertEqual(self.file.read_bytes(),original)

    def test_timeout_stops_descendant_before_rollback(self):
        child = self.root / 'child.py'; marker = self.root / 'late-write'
        child.write_text('import time,pathlib\ntime.sleep(2)\npathlib.Path('+repr(str(marker))+').write_text("late")')
        parent = self.root / 'parent.py'
        parent.write_text('import subprocess,sys,time\nsubprocess.Popen([sys.executable,'+repr(str(child))+'])\ntime.sleep(10)')
        self.core.config['tests']=[{'argv':[sys.executable,str(parent)],'timeout_sec':1}]
        receipt=self.core.apply(self.proposal())
        self.assertEqual(receipt['status'],'rolled-back')
        self.assertTrue(receipt['test']['results'][0]['process_tree_stopped'])
        import time
        time.sleep(1.2)
        self.assertFalse(marker.exists())

    def test_output_flood_is_bounded_and_rolled_back(self):
        checker=self.root/'flood.py'; checker.write_text('import sys\nwhile True: sys.stdout.write("x"*10000)')
        self.core.config['tests']=[{'argv':[sys.executable,str(checker)],'timeout_sec':10}]
        receipt=self.core.apply(self.proposal())
        self.assertEqual(receipt['status'],'rolled-back')
        self.assertEqual(receipt['test']['returncode'],125)

    def test_rollback_can_resume_after_partial_restore(self):
        (self.ws/'second.py').write_bytes(b'original second')
        tx=WorkspaceTransaction(self.ws,self.root/'backup','partial')
        tx.write('calc.py','new'); tx.write('second.py','new second')
        import safety
        real_write=safety.atomic_bytes
        def fail_second(file,data):
            if file.name=='second.py': raise OSError('synthetic interruption')
            return real_write(file,data)
        with patch('safety.atomic_bytes',side_effect=fail_second), self.assertRaises(OSError): tx.rollback()
        tx.rollback()
        self.assertEqual((self.ws/'second.py').read_bytes(),b'original second')

    def test_final_receipt_failure_rolls_back(self):
        result=self.proposal(); original=self.file.read_bytes()
        import core
        real_write=core.atomic_bytes
        def fail_receipt(file,data):
            if file.parent==self.core.runs: raise OSError('synthetic receipt failure')
            return real_write(file,data)
        with patch('core.atomic_bytes',side_effect=fail_receipt), self.assertRaises(OSError): self.core.apply(result)
        self.assertEqual(self.file.read_bytes(),original)
        emergency=json.loads((self.core.backups/result.receipt['tag']/'effect-receipt.json').read_text())
        self.assertEqual(emergency['status'],'error-rolled-back')

    def test_invalid_test_configuration_does_not_turn_green(self):
        self.core.config['tests']=[{'argv':[]}]
        receipt = self.core.apply(self.proposal())
        self.assertEqual(receipt['status'],'rolled-back')

    def test_missing_executable_rolls_back(self):
        self.core.config['tests']=[{'argv':[str(self.root / 'missing.exe')]}]
        self.assertEqual(self.core.apply(self.proposal())['status'],'rolled-back')

    def test_test_config_change_rejected(self):
        result=self.proposal(); self.core.config['tests']=[{'argv':['python']}]
        with self.assertRaisesRegex(ValueError,'Testkonfiguration'): self.core.apply(result)

    def test_protected_paths_read_and_write(self):
        for name in ['../a','.GIT/config','.env','a:stream','C:/x','con.txt','x./a','a/../b','.ssh/id_rsa','/abs']:
            for write in [False,True]:
                with self.subTest(name=name,write=write), self.assertRaises(ValueError): safe_path(self.ws,name,write)

    def test_secret_not_sent_to_model(self):
        (self.ws/'config.py').write_text('api_key="synthetic-not-a-real-credential"')
        self.assertNotIn('config.py',[x['path'] for x in self.core.scout('config api',self.core.file_tree())])
        with self.assertRaisesRegex(ValueError,'Zugangsdaten'): self.core.read_files(['config.py'])

    def test_hardlinks_blocked(self):
        (self.ws/'alias.py').hardlink_to(self.file)
        with self.assertRaisesRegex(ValueError,'Hardlink'): safe_path(self.ws,'alias.py')

    def test_json_credentials_and_key_blocks_not_read(self):
        for text in ['{"password": "synthetic-example-only"}', '{"api_key":"synthetic-example-only"}',
                     '-----BEGIN PRIVATE KEY-----\nSYNTHETIC TEST\n-----END PRIVATE KEY-----']:
            (self.ws/'config.json').write_text(text)
            with self.assertRaisesRegex(ValueError,'Zugangsdaten'): self.core.read_files(['config.json'])

    def test_stale_full_read_rejected(self):
        self.core.read_files(['calc.py']); self.file.write_bytes(b'changed')
        final, issues = self.core.validate_edits([{'path':'calc.py','op':'write','content':'replacement'}],{'calc.py'})
        self.assertTrue(issues); self.assertFalse(final)

    def test_context_dependency_change_invalidates_proposal(self):
        (self.ws/'interface.py').write_bytes(b'API = 1\n')
        replies=[{'plan':['fix'],'files':['calc.py','interface.py'],'questions':[],'acceptance':[]},
                 {'edits':[{'path':'calc.py','op':'replace','old_text':'return a-b','new_text':'return a+b'}]},
                 {'verdict':'ok','issues':[]}]
        with patch.object(self.core,'chat',side_effect=replies): result=self.core.analyze('fix','fixture')
        (self.ws/'interface.py').write_bytes(b'API = 2\n')
        with self.assertRaisesRegex(ValueError,'Vorschau verändert'): self.core.apply(result)

    def test_partial_read_cannot_write(self):
        self.core.config['context_max_bytes_per_file']=8; self.core.read_files(['calc.py'])
        final, issues = self.core.validate_edits([{'path':'calc.py','op':'write','content':'replacement'}],{'calc.py'})
        self.assertTrue(issues); self.assertFalse(final)

    def test_rollback_preserves_concurrent_edit(self):
        tx=WorkspaceTransaction(self.ws,self.root/'backup','test'); tx.write('calc.py','generated')
        self.file.write_bytes(b'user-after-write')
        with self.assertRaisesRegex(RuntimeError,'Rollback-Konflikt'): tx.rollback()
        self.assertEqual(self.file.read_bytes(),b'user-after-write')

    def test_delete_and_new_file_diff_has_correct_headers(self):
        result=self.proposal([{'path':'calc.py','op':'delete'},{'path':'new.py','op':'create','content':'x=1'}])
        self.assertIn('+++ /dev/null',result.diff); self.assertIn('--- /dev/null',result.diff)
        self.assertIn('\\ No newline at end of file',result.diff)

    def test_no_model_changes_during_analysis(self):
        with patch.object(self.core,'chat',side_effect=RuntimeError('provider unavailable')):
            before=self.file.read_bytes()
            with self.assertRaises(RuntimeError): self.core.analyze('fix','model')
            self.assertEqual(self.file.read_bytes(),before)
            self.assertFalse(self.core._busy)

if __name__ == '__main__': unittest.main()
