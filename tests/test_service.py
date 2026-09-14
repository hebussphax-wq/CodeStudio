import copy
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch
from service import StudioService

class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=pathlib.Path(self.temp.name)
        self.workspace=self.root/'project';self.workspace.mkdir()
        (self.workspace/'calc.py').write_bytes(b'value=1\n')
        self.service=StudioService(self.root,{'workspace':str(self.workspace),'tests':[]},lambda _:None)
    def tearDown(self):self.temp.cleanup()
    def propose(self):
        replies=[{'plan':['change value'],'files':['calc.py'],'questions':[],'acceptance':['value two']},
                 {'edits':[{'path':'calc.py','op':'replace','old_text':'value=1','new_text':'value=2'}],'notes':''},
                 {'verdict':'ok','issues':[],'summary':'fixture'}]
        with patch.object(self.service.core,'chat',side_effect=replies):
            return self.service.handle({'command':'analyze','task':'value two','model':'fixture'})
    def test_proposal_and_explicit_apply_share_one_process(self):
        proposed=self.propose()
        self.assertEqual(proposed['changes'],[{'path':'calc.py','before':'value=1\n','after':'value=2\n'}])
        self.assertEqual((self.workspace/'calc.py').read_text(),'value=1\n')
        with self.assertRaises(ValueError):self.service.handle({'command':'apply','proposal_id':proposed['proposal_id']})
        result=self.service.handle({'command':'apply','proposal_id':proposed['proposal_id'],'approved':True})
        self.assertEqual(result['receipt']['status'],'applied-untested')
        self.assertEqual((self.workspace/'calc.py').read_text(),'value=2\n')
        with self.assertRaises(ValueError):self.service.handle({'command':'apply','proposal_id':proposed['proposal_id'],'approved':True})
    def test_configuration_invalidates_old_proposal(self):
        proposed=self.propose()
        self.service.handle({'command':'configure','context_tokens':32768,'test_argv':[]})
        self.assertEqual(self.service.core.config['options']['num_ctx'],32768)
        with self.assertRaises(ValueError):self.service.handle({'command':'apply','proposal_id':proposed['proposal_id'],'approved':True})
    def test_invalid_commands_and_settings_do_not_write(self):
        for request in [{'command':'shell','argv':['x']},{'command':'configure','context_tokens':True},
                        {'command':'configure','test_argv':'python'},{'command':'analyze','task':'x','model':None}]:
            with self.assertRaises(ValueError):self.service.handle(request)
        self.assertEqual((self.workspace/'calc.py').read_text(),'value=1\n')
    def test_changed_file_is_rejected(self):
        proposed=self.propose();(self.workspace/'calc.py').write_text('user change')
        with self.assertRaises(ValueError):self.service.handle({'command':'apply','proposal_id':proposed['proposal_id'],'approved':True})
        self.assertEqual((self.workspace/'calc.py').read_text(),'user change')
