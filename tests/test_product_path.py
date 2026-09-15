import copy, json, pathlib, sys, tempfile, unittest
from unittest.mock import patch
from core import CodeStudioCore, ModelOutputError
from service import StudioService
from director import description

class ProductPathTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.temp.name)
        self.cfg={'workspace':str(self.root),'state_dir':str(self.root/'state'),'options':{'num_ctx':16384,'num_predict':4096}}
    def tearDown(self):self.temp.cleanup()
    def test_profiles_and_output_configure_atomically(self):
        service=StudioService(self.root,self.cfg,lambda e:None)
        profiles=[{'name':'unit','argv':[sys.executable,'-B','unit.py'],'timeout_sec':30},
                  {'name':'integration','argv':[sys.executable,'-B','integration.py'],'timeout_sec':60}]
        result=service.handle({'command':'configure','context_tokens':32768,'output_tokens':8192,'test_profiles':profiles})
        self.assertEqual(result['test_profiles'],profiles);self.assertEqual(service.core.config['options']['num_predict'],8192)
        before=copy.deepcopy(service.core.config)
        for output in (True,0,32769,'8192'):
            with self.assertRaises(ValueError):service.handle({'command':'configure','output_tokens':output,'test_profiles':profiles})
            self.assertEqual(service.core.config,before)
    def test_schema_is_in_prompt_and_transport_format(self):
        core=CodeStudioCore(self.root,self.cfg);core.director_mode=True
        with patch.object(core,'ollama_request',return_value={'message':{'content':'{}'}}) as call:
            core.chat('planner','brief','local')
        body=call.call_args.args[1]
        self.assertEqual(json.loads(body['messages'][0]['content'].split('OUTPUT JSON SCHEMA (field meanings and required shape):\n')[1]),body['format'])
        self.assertIn('never put source code',body['messages'][0]['content'])
    def test_truncation_preserves_bounded_diagnostic_without_retrying_transport(self):
        core=CodeStudioCore(self.root,self.cfg)
        raw='{"edits":['+'x'*9000
        with patch.object(core,'ollama_request',return_value={'done_reason':'length','eval_count':4096,'message':{'content':raw}}) as call:
            with self.assertRaises(ModelOutputError) as error:core.chat('coder','brief','local')
        self.assertEqual(call.call_count,1);ev=error.exception.evidence
        self.assertEqual(ev['eval_count'],4096);self.assertEqual(ev['response_bytes'],len(raw));self.assertTrue(ev['preview_truncated'])
        self.assertLessEqual(len(ev['response_preview']),4000)
    def test_missing_edits_is_not_an_intentional_noop(self):
        core=CodeStudioCore(self.root,self.cfg);core.module_mode=True;core.allow_unchanged_module=True
        (self.root/'a.py').write_text('a=2\n')
        contract={'files':['a.py'],'references':[],'contract':'Preserve a=2.'}
        with patch.object(core,'chat',return_value={}):
            with self.assertRaisesRegex(ValueError,'edits'):core.analyze('Preserve a=2.','local',contract)
    def test_short_behavior_does_not_require_arbitrary_word_count(self):
        self.assertEqual(description('Doubles inputs.','contract'),'Doubles inputs.')
        with self.assertRaises(ValueError):description('read-only','contract')

if __name__=='__main__':unittest.main()
