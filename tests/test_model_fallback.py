import json, unittest
from unittest.mock import patch
from tests import test_director as director_tests
from tests.test_director import OK
from core import CodeStudioCore
from autonomy import fallback_models, RunStopped
from service import StudioService

class ModelFallbackTests(unittest.TestCase):
    setUp=director_tests.DirectorTests.setUp
    tearDown=director_tests.DirectorTests.tearDown
    make_run=director_tests.DirectorTests.make_run

    def setup_fallback(self):
        self.config.update(ollama_url='http://127.0.0.1:11439',autonomous_fallback_models=['backup'])
        return {'schema':'codestudio.modules.v1','modules':[{'id':'app',
            'contract':'app.py exports show(n) returning str(n*2).','files':['app.py'],
            'references':['test_app.py'],'depends_on':[],'tests':[0]}]}

    def test_failed_tests_switch_model_without_new_scope_or_budget(self):
        workflow=self.setup_fallback();calls=[];coders=[]
        def chat(role,prompt,model):
            calls.append((role,model))
            if role=='coder':
                coders.append(model)
                value=[3,4,2][len(coders)-1]
                return {'edits':[{'path':'app.py','op':'create' if len(coders)==1 else 'write',
                    'content':f'def show(n): return str(n*{value})\n'}]}
            return OK
        run=self.make_run(workflow=workflow,limits={'repairs':2,'model_calls':10})
        with patch.object(CodeStudioCore,'installed_models',return_value=['local','backup']),patch.object(CodeStudioCore,'chat',side_effect=chat):
            r=run.execute()['receipt']
        self.assertEqual(r['status'],'succeeded');self.assertEqual(coders,['local','local','backup'])
        self.assertEqual(r['model'],'local');self.assertEqual(len(r['model_switches']),1)
        self.assertEqual(r['attempts'][-1]['models']['coder'],'backup')
        self.assertEqual(r['model_calls'],len(calls));self.assertLessEqual(r['model_calls'],10)
        self.assertTrue(all(m=='backup' for role,m in calls if role=='reviewer'))
        self.assertEqual(set(run.tx.files),{'app.py'})

    def test_invalid_proposal_counts_as_attempt_without_replaying_old_test(self):
        workflow=self.setup_fallback();models=[]
        def chat(role,prompt,model):
            if role!='coder':return OK
            models.append(model)
            if len(models)==2:raise ValueError('Malformed proposal, no test executed')
            value=2 if len(models)==4 else 3+len(models)
            return {'edits':[{'path':'app.py','op':'create' if len(models)==1 else 'write',
                'content':f'def show(n): return str(n*{value})\n'}]}
        with patch.object(CodeStudioCore,'installed_models',return_value=['backup']),patch.object(CodeStudioCore,'chat',side_effect=chat):
            r=self.make_run(workflow=workflow,limits={'repairs':3}).execute()['receipt']
        self.assertEqual(r['status'],'succeeded');self.assertEqual(models,['local','local','backup','backup'])
        self.assertEqual(len(r['attempts']),3)
        self.assertEqual([a['phase'] for a in r['failure_analysis']],['implementation','proposal','implementation'])

    def test_switch_does_not_add_attempts_or_reset_call_budget(self):
        for limits in ({'repairs':1},{'repairs':3,'model_calls':4}):
            with self.subTest(limits=limits):
                workflow=self.setup_fallback();models=[]
                def chat(role,prompt,model):
                    if role!='coder':return OK
                    models.append(model)
                    return {'edits':[{'path':'app.py','op':'create' if len(models)==1 else 'write',
                        'content':f'def show(n): return str(n*{len(models)+2})\n'}]}
                with patch.object(CodeStudioCore,'installed_models',return_value=['backup']),patch.object(CodeStudioCore,'chat',side_effect=chat):
                    r=self.make_run(workflow=workflow,limits=limits).execute()['receipt']
                self.assertEqual(r['status'],'stalled' if limits['repairs']==3 else 'budget_exhausted')
                if limits['repairs']==3:self.assertEqual(r['blocker_report']['attempts'],4)
                self.assertLessEqual(len(models),limits['repairs']+1)
                self.assertFalse((self.project/'app.py').exists())
                if limits['repairs']==1:self.assertNotIn('model_switches',r)

    def test_absent_models_block_before_writes_and_host_never_bypassed(self):
        workflow=self.setup_fallback()
        with patch.object(CodeStudioCore,'installed_models',return_value=[]),patch.object(CodeStudioCore,'chat') as chat:
            r=self.make_run(workflow=workflow).execute()['receipt']
        self.assertEqual(r['status'],'blocked');chat.assert_not_called()
        from autonomy import AutonomousRun
        with self.assertRaises(ValueError):
            AutonomousRun(self.root,self.config,{'run_id':'a'*32,'approved':True,'task':'build','model':'local'},transport=lambda *a:None)
        self.config['ollama_url']='https://external.invalid'
        with self.assertRaises(ValueError):self.make_run(workflow=workflow)

    def test_validated_service_settings_do_not_partially_mutate(self):
        self.config['ollama_url']='http://127.0.0.1:11439'
        service=StudioService(self.root,self.config,lambda e:None)
        result=service.handle({'command':'configure','fallback_models':['backup'],'context_tokens':8192})
        self.assertEqual(result['fallback_models'],['backup'])
        before=json.dumps(service.core.config,sort_keys=True)
        with self.assertRaises(ValueError):service.handle({'command':'configure','fallback_models':['same','same'],'context_tokens':4096})
        self.assertEqual(before,json.dumps(service.core.config,sort_keys=True))
        for value in ('backup',['has space'],['a','b','c','d']):
            with self.assertRaises(ValueError):fallback_models(value)

    def test_terminal_receipt_keeps_started_model_request_after_cancellation(self):
        self.config['ollama_url']='http://127.0.0.1:11439'
        run=self.make_run()
        with patch.object(CodeStudioCore,'ollama_request',side_effect=RunStopped('cancelled','cancel after request started')):
            r=run.execute()['receipt']
        self.assertEqual(r['status'],'cancelled');self.assertEqual(r['model_calls'],1)
        self.assertEqual(r['model_history'],[{'call':1,'role':'planner','model':'local','provider':'http://127.0.0.1:11439'}])
        self.assertEqual(json.loads(run.path.read_text())['model_history'],r['model_history'])

if __name__=='__main__':unittest.main()
