import copy, json, unittest
from unittest.mock import patch
from tests import test_director as dt
from tests.test_director import workflow, create, OK
from core import CodeStudioCore
from runmemory import load_candidate, snapshot_path
from safety import canonical, digest

class RunMemoryTests(unittest.TestCase):
    setUp=dt.DirectorTests.setUp
    tearDown=dt.DirectorTests.tearDown
    make_run=dt.DirectorTests.make_run

    def fail_run(self):
        plan=workflow()
        replies=iter([plan,create('values.py','def double(n): return n*3\n'),OK,
            create('app.py','from values import double\ndef show(n): return str(double(n))\n')])
        run=self.make_run(limits={'repairs':0})
        with patch.object(CodeStudioCore,'chat',side_effect=lambda *a:next(replies)):
            r=run.execute()['receipt']
        self.assertEqual(r['status'],'budget_exhausted',r.get('error'))
        self.assertTrue(r['rollback_verified'])
        self.assertFalse((self.project/'values.py').exists())
        return run,r

    def test_failed_candidate_survives_rollback_and_resume_retests_all_modules(self):
        first,receipt=self.fail_run()
        p=load_candidate(first.core.runs,first.id,first.core.workspace,self.config['tests'])
        self.assertIn('n*3',p['files']['values.py']['content'])
        self.assertIsNone(p['basis']['values.py'])
        self.assertEqual(len(p['workflow']['modules']),2)
        calls=[]
        replies=iter([{'edits':[{'path':'values.py','op':'write','content':'def double(n): return n*2\n'}]},OK,
            {'edits':[],'notes':'app uses the corrected values'},OK,OK])
        second=self.make_run(resume_from=first.id,changed_approach='Fix multiply factor from actual failed test')
        def chat(role,prompt,model):calls.append((role,prompt));return next(replies)
        with patch.object(CodeStudioCore,'chat',side_effect=chat):r=second.execute()['receipt']
        self.assertEqual(r['status'],'succeeded',r.get('error'))
        self.assertEqual([s['id'] for s in r['steps']],['values','app'])
        self.assertEqual(r['test']['returncode'],0)
        self.assertFalse(r['resume']['historical_steps_accepted'])
        self.assertIn('Fix multiply factor',calls[0][1])
        experience=json.loads((second.core.runs/('experience-'+second.id+'.json')).read_text())
        self.assertEqual(experience['status'],'verified_run_outcome')
        self.assertEqual(experience['source_run'],first.id)
        self.assertEqual(experience['tests_sha256'],receipt['tests_sha256'])

    def test_corrupted_snapshot_blocks_before_model_or_write(self):
        first,r=self.fail_run()
        path=snapshot_path(first.core.runs,first.id)
        e=json.loads(path.read_text());e['payload']['files']['values.py']['content']='tampered'
        path.write_text(json.dumps(e))
        second=self.make_run(resume_from=first.id,changed_approach='Fix multiply factor from test')
        with patch.object(CodeStudioCore,'chat') as chat:r=second.execute()['receipt']
        chat.assert_not_called();self.assertEqual(r['status'],'failed')
        self.assertFalse((self.project/'values.py').exists())
        self.assertNotIn('experience',r)

    def test_changed_reference_or_test_profile_blocks_without_overwriting(self):
        first,r=self.fail_run()
        (self.project/'test_app.py').write_text('print("user change")')
        with self.assertRaisesRegex(ValueError,'Ausgangsdatei'):
            load_candidate(first.core.runs,first.id,first.core.workspace,self.config['tests'])
        self.assertEqual((self.project/'test_app.py').read_text(),'print("user change")')
        profiles=copy.deepcopy(self.config['tests']);profiles[0]['timeout_sec']=90
        with self.assertRaisesRegex(ValueError,'Testprofile'):
            load_candidate(first.core.runs,first.id,first.core.workspace,profiles)

    def test_second_failure_rolls_back_to_original_basis_and_does_not_promote(self):
        first,r=self.fail_run()
        replies=iter([{'edits':[{'path':'values.py','op':'write','content':'def double(n): return n*4\n'}]},OK,
            {'edits':[],'notes':'unchanged app'}])
        second=self.make_run(resume_from=first.id,changed_approach='Try corrected multiplier four',limits={'repairs':0})
        with patch.object(CodeStudioCore,'chat',side_effect=lambda *a:next(replies)):r=second.execute()['receipt']
        self.assertEqual(r['status'],'budget_exhausted',r.get('error'))
        self.assertTrue(r['rollback_verified'])
        self.assertFalse((self.project/'values.py').exists());self.assertFalse((self.project/'app.py').exists())
        self.assertNotIn('experience',r)
        self.assertTrue(snapshot_path(second.core.runs,second.id).exists())

    def test_same_failed_candidate_not_retested(self):
        first,r=self.fail_run()
        second=self.make_run(resume_from=first.id,changed_approach='Claim new approach without actual changes',limits={'repairs':3})
        def reply(role,*a):return OK if role=='reviewer' else {'edits':[],'notes':'no change'}
        with patch.object(CodeStudioCore,'chat',side_effect=reply):r=second.execute()['receipt']
        self.assertEqual(r['status'],'stalled',r.get('error'))
        self.assertEqual(r['blocker_report']['attempts'],4)
        self.assertIn('Identischer',r['error']);self.assertTrue(r['rollback_verified'])
        self.assertEqual(len(r['attempts']),1)  # earlier pending module re-reviewed
        self.assertEqual(r['attempts'][0]['test']['status'],'deferred')

    def test_newly_created_basis_file_is_not_overwritten(self):
        first,r=self.fail_run()
        (self.project/'values.py').write_text('user content')
        second=self.make_run(resume_from=first.id,changed_approach='Fix from prior failure evidence')
        with patch.object(CodeStudioCore,'chat') as chat:r=second.execute()['receipt']
        chat.assert_not_called();self.assertEqual(r['status'],'failed')
        self.assertEqual((self.project/'values.py').read_text(),'user content')

    def test_snapshot_retains_deletion_and_failed_resume_restores_original(self):
        (self.project/'values.py').write_text('def double(n): return n*2\n')
        plan=workflow();plan['modules'][0]['contract']='Remove values.py; app is temporarily independent.'
        replies=iter([plan,{'edits':[{'path':'values.py','op':'delete'}]},OK,create('app.py','def show(n): return str(n*3)\n')])
        first=self.make_run(limits={'repairs':0})
        with patch.object(CodeStudioCore,'chat',side_effect=lambda *a:next(replies)):r=first.execute()['receipt']
        self.assertTrue(r['rollback_verified'])
        p=load_candidate(first.core.runs,first.id,first.core.workspace,self.config['tests'])
        self.assertIsNone(p['files']['values.py']['content'])
        self.assertIsNotNone(p['files']['values.py']['before'])
        second=self.make_run(resume_from=first.id,changed_approach='Repair app after checked deletion',limits={'repairs':0})
        with patch.object(CodeStudioCore,'chat',side_effect=ValueError('no valid output')):r=second.execute()['receipt']
        self.assertTrue(r['rollback_verified'])
        self.assertEqual((self.project/'values.py').read_text(),'def double(n): return n*2\n')

    def test_legacy_in_project_state_keeps_execution_but_reports_no_safe_snapshot(self):
        self.config['state_dir']=str(self.project/'state')
        first,r=self.fail_run()
        self.assertNotIn('candidate',r)
        self.assertIn('außerhalb',r['candidate_unavailable'])

    def test_resume_requires_explicit_changed_approach(self):
        with self.assertRaisesRegex(ValueError,'Ansatz'):self.make_run(resume_from='a'*32)
        with self.assertRaises(ValueError):self.make_run(resume_from='../outside',changed_approach='Changed approach')

    def test_resumed_failure_can_replan_and_return_to_original_workflow(self):
        first,r=self.fail_run()
        repair={'acceptance':['show(4) is 8'],'assumptions':[],'questions':[], 'modules':[{
            'id':'repair','contract':'Correct values.double to n*2 and app.show to stringify double(n).',
            'outcomes':['show(4) returns 8 and show(-2) returns -4 as strings'],
            'files':['values.py','app.py'],'references':['test_app.py'],'depends_on':[],'tests':[0]}]}
        def app(n):return {'edits':[{'path':'app.py','op':'write','content':f'def show(n): return str(n*{n})\n'}]}
        replies=iter([{'edits':[]},OK,app(4),app(5),repair,{'edits':[
            {'path':'values.py','op':'write','content':'def double(n): return n*2\n'},
            {'path':'app.py','op':'write','content':'from values import double\ndef show(n): return str(double(n))\n'}]},OK,{'edits':[]},OK,OK])
        second=self.make_run(resume_from=first.id,changed_approach='Repair both integration components',limits={'repairs':1})
        with patch.object(CodeStudioCore,'chat',side_effect=lambda *a:next(replies)):r=second.execute()['receipt']
        self.assertEqual(r['status'],'succeeded',r.get('error'))
        self.assertEqual([s['id'] for s in r['steps']],['values','repair','app'])
        self.assertEqual(r['test']['returncode'],0)
        self.assertEqual(r['resumed_workflows'][0]['module'],'app')

    def test_legacy_incomplete_plan_is_replanned_on_resume(self):
        (self.project/'README.md').write_text('## Required files\n- index.html, style.css: frontend\n')
        with patch('director.required_artifacts',return_value={}):first,r=self.fail_run()
        complete=workflow();complete['modules'][-1]['files']+=['index.html','style.css']
        replies=iter([complete,{'edits':[{'path':'values.py','op':'write','content':'def double(n): return n*2\n'}]},OK,
            {'edits':[{'path':'index.html','op':'create','content':'<!doctype html><p>Done</p>'},
                      {'path':'style.css','op':'create','content':'p { color: black; }'}]},OK,OK])
        second=self.make_run(resume_from=first.id,changed_approach='Replan to cover the original explicit file contract')
        with patch.object(CodeStudioCore,'chat',side_effect=lambda *a:next(replies)):r=second.execute()['receipt']
        self.assertEqual(r['status'],'succeeded',r.get('error'))
        self.assertEqual(r['resume']['replanned_missing_artifacts'],['index.html','style.css'])
        self.assertTrue((self.project/'index.html').exists());self.assertTrue((self.project/'style.css').exists())
        self.assertEqual(r['test']['returncode'],0)

    def test_complete_failed_plan_proposal_revalidated_without_new_planner_call(self):
        (self.project/'README.md').write_text('## Required files\n- index.html, style.css: frontend\n')
        with patch('director.required_artifacts',return_value={}):first,r=self.fail_run()
        complete=workflow();complete['modules'][-1]['files']+=['index.html','style.css']
        r['planning_failures']=[{'response_preview':canonical(complete).decode('utf8'),
            'response_sha256':digest(canonical(complete)),'preview_truncated':False}]
        first.path.write_bytes(canonical(r))
        replies=iter([{'edits':[{'path':'values.py','op':'write','content':'def double(n): return n*2\n'}]},OK,
            {'edits':[{'path':'index.html','op':'create','content':'<!doctype html><p>Done</p>'},
                      {'path':'style.css','op':'create','content':'p { color: black; }'}]},OK,OK])
        roles=[]
        def answer(role,*a):roles.append(role);return next(replies)
        second=self.make_run(resume_from=first.id,changed_approach='Revalidate retained complete plan after runtime correction')
        with patch.object(CodeStudioCore,'chat',side_effect=answer):r=second.execute()['receipt']
        self.assertEqual(r['status'],'succeeded',r.get('error'))
        self.assertTrue(r['resume']['reused_plan_candidate']);self.assertNotIn('planner',roles)

    def test_test_failure_survives_a_later_proposal_failure(self):
        first,r=self.fail_run()
        second=self.make_run(resume_from=first.id,changed_approach='Repair original observed error',limits={'repairs':0})
        with patch.object(CodeStudioCore,'chat',side_effect=ValueError('bad proposal schema')):r=second.execute()['receipt']
        p=load_candidate(second.core.runs,second.id,second.core.workspace,self.config['tests'])
        kinds={x['kind'] for x in p['failure_analysis']}
        self.assertIn('assertion',kinds)
        observed=next(x for x in p['failure_analysis'] if x['kind']=='assertion')
        self.assertEqual(observed['source_run'],first.id)
        self.assertTrue(r['rollback_verified'])

    def test_reviewer_opinion_does_not_block_unchanged_recheck(self):
        flow={'schema':'codestudio.modules.v1','modules':[{'id':'app','contract':'show doubles as string',
            'files':['app.py'],'references':['test_app.py'],'depends_on':[],'tests':[0]}]}
        bad={'verdict':'reject','issues':['unsupported opinion'],'summary':'reject'}
        replies=iter([create('app.py','def show(n): return str(n*2)\n'),bad,bad])
        first=self.make_run(workflow=flow,limits={'repairs':0})
        with patch.object(CodeStudioCore,'chat',side_effect=lambda *a:next(replies)):r=first.execute()['receipt']
        p=load_candidate(first.core.runs,first.id,first.core.workspace,self.config['tests'])
        self.assertEqual(p['failed_candidates'],[])
        second=self.make_run(resume_from=first.id,changed_approach='Recheck unsupported reviewer claim',model='different')
        replies=iter([{'edits':[]},OK,OK])
        with patch.object(CodeStudioCore,'chat',side_effect=lambda *a:next(replies)):r=second.execute()['receipt']
        self.assertEqual(r['status'],'succeeded',r.get('error'))
        self.assertEqual(r['test']['returncode'],0)

    def test_failed_delivery_invalidates_success_experience(self):
        from safety import atomic_bytes as write
        first,r=self.fail_run()
        second=self.make_run(resume_from=first.id,changed_approach='Fix multiplier from failed check')
        replies=iter([{'edits':[{'path':'values.py','op':'write','content':'def double(n): return n*2\n'}]},OK,{'edits':[]},OK,OK])
        failed=[]
        def fail_once(path,data):
            if path==second.path and b'"experience"' in data and not failed:
                failed.append(True);raise OSError('simulated receipt persistence error')
            return write(path,data)
        with patch.object(CodeStudioCore,'chat',side_effect=lambda *a:next(replies)),patch('autonomy.atomic_bytes',side_effect=fail_once):
            r=second.execute()['receipt']
        self.assertEqual(r['status'],'failed');self.assertTrue(r['rollback_verified'])
        experience=json.loads((second.core.runs/('experience-'+second.id+'.json')).read_text())
        self.assertEqual(experience['status'],'invalidated_by_failed_delivery')
        self.assertFalse((self.project/'values.py').exists())

if __name__=='__main__':unittest.main()

