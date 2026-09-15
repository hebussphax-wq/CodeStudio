import json
import pathlib
import sys
import tempfile
import threading
import unittest
import uuid
from unittest.mock import patch
from autonomy import AutonomousRun
from core import CodeStudioCore
from processrunner import run_command

OK={'verdict':'ok','issues':[],'summary':'verified'}
def plan(step='implement',files=None):
    return {'plan':[step],'files':files or ['calc.py'],'questions':[],'acceptance':['a=2 and b=3']}
def edit(old,new):
    return {'edits':[{'path':'calc.py','op':'replace','old_text':old,'new_text':new}],'notes':''}
def cycle(old,new):return [plan(),edit(old,new),OK]
PASS={'status':'passed','returncode':0,'output':'test passed','process_tree_stopped':True,'output_truncated':False}
FAIL={**PASS,'status':'failed','returncode':1,'output':'AssertionError: a must be 2'}

class AutonomyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=pathlib.Path(self.temp.name);self.workspace=self.root/'project';self.workspace.mkdir()
        (self.workspace/'calc.py').write_bytes(b'a=0\nb=0\n')
        (self.workspace/'test_calc.py').write_bytes(b'import unittest\nfrom calc import a,b\nclass TestCalc(unittest.TestCase):\n def test_values(self):\n  self.assertEqual(a,2)\n  self.assertEqual(b,3)\n')
        self.config={'workspace':str(self.workspace),'state_dir':str(self.root/'state'),'tests':[{'argv':[sys.executable,'-B','-m','unittest','discover','-v'],'timeout_sec':20}]}
    def tearDown(self):self.temp.cleanup()
    def run_new(self,**kwargs):
        request={'run_id':uuid.uuid4().hex,'approved':True,'task':'Implement a=2 and b=3','model':'fixture','planning':'steps',**kwargs}
        return AutonomousRun(self.root,self.config,request)
    def test_multistep_real_tests_then_automatic_repair(self):
        run=self.run_new()
        initial={p.name:p.read_bytes() for p in self.workspace.iterdir()}
        master={**plan(),'plan':['Implement a','Implement b']}
        replies=[master,*cycle('a=0','a=1'),*cycle('b=0','b=3'),*cycle('a=1','a=2'),OK]
        prompts=[]
        def chat(role,user,model):prompts.append(user);return replies.pop(0)
        with patch.object(CodeStudioCore,'chat',side_effect=chat):
            result=run.execute()['receipt']
        self.assertEqual(result['status'],'succeeded')
        self.assertEqual(len(result['steps']),2)
        self.assertEqual([a['test']['returncode'] for a in result['attempts']],[1,1,0])
        self.assertTrue(all(a['test']['results'][0]['process_tree_stopped'] for a in result['attempts']))
        self.assertIn('AssertionError','\n'.join(prompts))
        self.assertEqual((self.workspace/'calc.py').read_bytes(),b'a=2\nb=3\n')
        self.assertEqual((self.workspace/'test_calc.py').read_bytes(),initial['test_calc.py'])
        self.assertEqual(json.loads(run.path.read_text())['after'],result['after'])
        self.assertFalse(run.lock.exists())
    def test_failed_final_tests_restore_whole_run_bytes(self):
        run=self.run_new(limits={'repairs':0})
        with patch.object(CodeStudioCore,'chat',side_effect=[plan(),*cycle('a=0','a=1')]),patch('autonomy.run_command',return_value=FAIL):
            result=run.execute()['receipt']
        self.assertEqual(result['status'],'budget_exhausted')
        self.assertTrue(result['rollback_verified'])
        self.assertEqual((self.workspace/'calc.py').read_bytes(),b'a=0\nb=0\n')
    def test_tests_required_before_any_model_or_write(self):
        self.config['tests']=[]
        with self.assertRaises(ValueError):self.run_new()
        self.assertEqual((self.workspace/'calc.py').read_bytes(),b'a=0\nb=0\n')
    def test_existing_tests_cannot_be_weakened(self):
        run=self.run_new()
        replies=[plan(),plan(files=['test_calc.py']),{'edits':[{'path':'test_calc.py','op':'write','content':'pass\n'}]},OK]
        with patch.object(CodeStudioCore,'chat',side_effect=replies):
            result=run.execute()['receipt']
        self.assertEqual(result['status'],'failed')
        self.assertIn('Vorhandene Testdateien',result['error'])
        self.assertIn(b'assertEqual',(self.workspace/'test_calc.py').read_bytes())
    def test_cancel_after_generation_before_write(self):
        run=self.run_new()
        replies=[plan(),plan(),edit('a=0','a=2'),OK]
        def chat(role,user,model):
            value=replies.pop(0)
            if role=='reviewer':run.cancel_event.set()
            return value
        with patch.object(CodeStudioCore,'chat',side_effect=chat):
            result=run.execute()['receipt']
        self.assertEqual(result['status'],'cancelled')
        self.assertEqual((self.workspace/'calc.py').read_bytes(),b'a=0\nb=0\n')
    def test_cancel_during_test_stops_tree_then_restores(self):
        run=self.run_new()
        self.config['tests']=[{'argv':[sys.executable,'-c','import time;time.sleep(30)'],'timeout_sec':20}]
        run=self.run_new()
        actual=run_command
        def cancel_test(*args,**kwargs):
            timer=threading.Timer(.2,run.cancel_event.set);timer.start()
            try:return actual(*args,**kwargs)
            finally:timer.cancel()
        with patch.object(CodeStudioCore,'chat',side_effect=[plan(),*cycle('a=0','a=2')]),patch('autonomy.run_command',side_effect=cancel_test):
            result=run.execute()['receipt']
        self.assertEqual(result['status'],'cancelled')
        self.assertTrue(result['test']['process_tree_stopped'])
        self.assertEqual((self.workspace/'calc.py').read_bytes(),b'a=0\nb=0\n')
    def test_user_change_during_test_is_not_overwritten(self):
        run=self.run_new()
        def changed(*args,**kwargs):(self.workspace/'calc.py').write_text('user edit');return FAIL
        with patch.object(CodeStudioCore,'chat',side_effect=[plan(),*cycle('a=0','a=2')]),patch('autonomy.run_command',side_effect=changed):
            result=run.execute()['receipt']
        self.assertEqual(result['status'],'rollback_conflict')
        self.assertEqual((self.workspace/'calc.py').read_text(),'user edit')
    def test_model_budget_exhaustion_cannot_claim_success(self):
        run=self.run_new(limits={'model_calls':4})
        with patch.object(CodeStudioCore,'chat',side_effect=[plan(),*cycle('a=0','a=2')]),patch('autonomy.run_command',return_value=PASS):
            result=run.execute()['receipt']
        self.assertEqual(result['status'],'budget_exhausted')
        self.assertEqual(result['model_calls'],4)
        self.assertEqual((self.workspace/'calc.py').read_bytes(),b'a=0\nb=0\n')
    def test_expired_host_before_write_fails_closed(self):
        run=self.run_new()
        run.core.transport=lambda *args:(_ for _ in ()).throw(ValueError('host expired'))
        with patch.object(CodeStudioCore,'chat',side_effect=[plan(),*cycle('a=0','a=2')]):
            result=run.execute()['receipt']
        self.assertEqual(result['status'],'failed')
        self.assertEqual((self.workspace/'calc.py').read_bytes(),b'a=0\nb=0\n')
    def test_run_id_replay_and_lock_rejected(self):
        run=self.run_new()
        with patch.object(CodeStudioCore,'chat',return_value={**plan(),'questions':['missing']}):
            self.assertEqual(run.execute()['receipt']['status'],'blocked')
        with self.assertRaises(ValueError):run.execute()
        other=self.run_new();other.lock.write_text('other owner')
        with self.assertRaises(FileExistsError):other.execute()
    def test_lock_file_is_not_a_model_target(self):
        from safety import relative
        with self.assertRaises(ValueError):relative('.codestudio-apply.lock')
    def test_deadline_before_work_is_terminal_and_no_writes(self):
        run=self.run_new();run.core.deadline=0
        result=run.execute()['receipt']
        self.assertEqual(result['status'],'timed_out')
        self.assertEqual((self.workspace/'calc.py').read_bytes(),b'a=0\nb=0\n')


    def test_each_file_checked_before_its_write(self):
        (self.workspace/'second.py').write_text('value=0\n')
        run=self.run_new()
        real=run.tx.write
        def write(rel,content):
            real(rel,content)
            if rel=='calc.py':(self.workspace/'second.py').write_text('user edit')
        replies=[plan(),plan(files=['calc.py','second.py']),{'edits':[
            {'path':'calc.py','op':'replace','old_text':'a=0','new_text':'a=2'},
            {'path':'second.py','op':'replace','old_text':'value=0','new_text':'value=2'}]},OK]
        with patch.object(CodeStudioCore,'chat',side_effect=replies),patch.object(run.tx,'write',side_effect=write):
            result=run.execute()['receipt']
        self.assertEqual(result['status'],'conflict')
        self.assertEqual((self.workspace/'second.py').read_text(),'user edit')
        self.assertEqual((self.workspace/'calc.py').read_bytes(),b'a=0\nb=0\n')
    def test_final_evidence_failure_rolls_back(self):
        run=self.run_new()
        save=run.save
        def fail_success():
            if run.receipt['status']=='succeeded':raise OSError('disk receipt failure')
            return save()
        with patch.object(CodeStudioCore,'chat',side_effect=[plan(),*cycle('a=0','a=2'),OK]),patch('autonomy.run_command',return_value=PASS),patch.object(run,'save',side_effect=fail_success):
            result=run.execute()['receipt']
        self.assertEqual(result['status'],'failed')
        self.assertTrue(result['rollback_verified'])
        self.assertEqual((self.workspace/'calc.py').read_bytes(),b'a=0\nb=0\n')
    def test_cancel_does_not_wait_for_model_response(self):
        import time
        run=self.run_new()
        def slow(*args):time.sleep(2);return plan()
        timer=threading.Timer(.15,run.cancel_event.set)
        start=time.monotonic();timer.start()
        with patch.object(CodeStudioCore,'chat',side_effect=slow):
            result=run.execute()['receipt']
        timer.cancel()
        self.assertLess(time.monotonic()-start,1)
        self.assertEqual(result['status'],'cancelled')
        self.assertEqual(result['model_calls'], 1)
        self.assertEqual(json.loads(run.path.read_text())['model_calls'], 1)
    def test_actual_json_retry_counts_towards_budget(self):
        run=self.run_new(limits={'model_calls':4})
        run.core.model_calls=3
        with patch.object(CodeStudioCore,'ollama_request',return_value={'message':{'content':'invalid json'}}) as request:
            with self.assertRaises(Exception):run.core.chat('planner','task','fixture')
        self.assertEqual(request.call_count,1)
        self.assertEqual(run.core.model_calls,4)
    def test_service_reservation_can_cancel_before_worker_starts(self):
        from service import StudioService
        service=StudioService(self.root,self.config,lambda _:None)
        request={'run_id':uuid.uuid4().hex,'approved':True,'task':'a=2','model':'fixture'}
        run=service.reserve(request)
        self.assertEqual(service.handle({'command':'cancel','run_id':run.id})['status'],'cancelling')
        self.assertEqual(run.execute()['receipt']['status'],'cancelled')


    def test_invalid_substep_is_deferred_then_repaired(self):
        run=self.run_new()
        replies=iter([plan(),RuntimeError('bad edit'),*cycle('a=0','a=2'),OK])
        def chat(*args):
            r=next(replies)
            if isinstance(r,Exception):raise r
            return r
        with patch.object(CodeStudioCore,'chat',side_effect=chat),patch('autonomy.run_command',side_effect=[FAIL,PASS]):
            result=run.execute()['receipt']
        self.assertEqual(result['status'],'succeeded')
        self.assertEqual(result['steps'][0]['status'],'deferred_to_qc')
    def test_all_products_receive_bound_workflow_result(self):
        from workflow import normalize_job,job_result
        from safety import digest,canonical
        for product in ('codestudio','tobyki','halomonsterai','bizdrive'):
            job={'schema':'codestudio.job.v1','product':product,'project_id':'p','task_id':'t','workflow_id':'w','employee_id':'coder','task':'implement','acceptance':['tests pass'],'execution_authorized':True}
            normalized=normalize_job(job)
            result=job_result(job,{'run_id':'r','status':'succeeded','after':{},'test':{'status':'passed'}},'receipt.json')
            self.assertEqual(result['product'],product)
            self.assertEqual(result['job_sha256'],digest(canonical(normalized)))
            self.assertFalse(result['host_accepted'])
            self.assertEqual(result['workflow_next'],'verify_and_review')
            job['execution_authorized']=False
            with self.assertRaises(ValueError):normalize_job(job)


    def test_empty_unittest_run_does_not_finish_green(self):
        run=self.run_new(limits={'repairs':0})
        with patch.object(CodeStudioCore,'chat',side_effect=[plan(),*cycle('a=0','a=2')]),patch('autonomy.run_command',return_value={**PASS,'output':'Ran 0 tests in 0.001s\nOK'}):
            result=run.execute()['receipt']
        self.assertEqual(result['status'],'budget_exhausted')
        self.assertEqual(result['test']['status'],'no_tests')
        self.assertEqual((self.workspace/'calc.py').read_bytes(),b'a=0\nb=0\n')
    def test_full_read_autonomous_schema_uses_whole_file_edits(self):
        from core import SCHEMA
        run=self.run_new()
        run.core.read_files(['calc.py'])
        run.core._request_count=0
        body={'format':SCHEMA['coder']}
        with patch.object(CodeStudioCore,'ollama_request',return_value={}) as send:
            run.core.ollama_request('/api/chat',body)
        self.assertEqual(send.call_args.args[1]['format']['properties']['edits']['items']['properties']['op']['enum'],['write','create','delete'])
        self.assertIn('replace',SCHEMA['coder']['properties']['edits']['items']['properties']['op']['enum'])

if __name__=='__main__':unittest.main()
