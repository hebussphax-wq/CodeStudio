import pathlib, tempfile, unittest
from failureanalysis import analyze_failure
from tests import test_director as director_tests
from tests.test_director import workflow, create, OK
from core import CodeStudioCore
from unittest.mock import patch


class FailureAnalysisTests(unittest.TestCase):
    def test_changing_actual_value_does_not_hide_repeated_assertion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp);test=root/'test.js';test.write_text('assert.equal(exit.y+exit.h,top);\n')
            def output(actual):return f'AssertionError [ERR_ASSERTION]: exit on top\n    at Object.<anonymous> ({test}:1:1)\n  actual: {actual},\n  expected: 50,\n'
            a=analyze_failure(root,output(390),profile='levels')
            b=analyze_failure(root,output(90),profile='levels')
            self.assertEqual(a['fingerprint'],b['fingerprint']);self.assertEqual(a['kind'],'assertion')
            self.assertEqual(a['comparison']['expected'],'50');self.assertFalse(a['needs_model_diagnosis'])
            self.assertIn('exit.y+exit.h',a['source_excerpts'][0]['text'])
    def test_python_dynamic_assertion_and_moving_helper_keep_check_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp); test=root/'test_calc.py'; helper=root/'calc.py'
            def output(actual,line):
                return f'File "{test}", line 3\nFile "{helper}", line {line}\nAssertionError: expected 8 got {actual}'
            a=analyze_failure(root,output(12,5),protected=['test_calc.py'])
            b=analyze_failure(root,output(16,8),protected=['test_calc.py'])
            self.assertEqual(a['fingerprint'],b['fingerprint'])
    def test_long_traceback_keeps_protected_check_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp)
            def output(helper,value):
                return f'File "{root}/test_calc.py", line 3\nFile "{root}/first.py", line 7\nFile "{root}/{helper}.py", line 2\nFile "{root}/last.py", line 9\nAssertionError: got {value}'
            a=analyze_failure(root,output('one','alpha'),protected=['test_calc.py'])
            b=analyze_failure(root,output('two','beta'),protected=['test_calc.py'])
            self.assertEqual(a['fingerprint'],b['fingerprint'])
            self.assertEqual(a['locations'][0]['path'],'test_calc.py')
    def test_timeout_reports_evidence_without_model_guess(self):
        with tempfile.TemporaryDirectory() as tmp:
            a=analyze_failure(tmp,'Program exceeded 30 seconds',status='timed_out',profile='browser')
            self.assertEqual(a['kind'],'timeout');self.assertFalse(a['needs_model_diagnosis'])
            self.assertIn('Zeitlimit',a['next_action'])
    def test_external_stack_paths_never_become_read_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            outside=pathlib.Path(tmp)/'private.py';outside.write_text('must not be read')
            root=pathlib.Path(tmp)/'project';root.mkdir()
            a=analyze_failure(root,f'File "{outside}", line 1\nValueError: wrong value')
            self.assertEqual(a['locations'],[]);self.assertEqual(a['source_excerpts'],[])
    def test_distinct_profiles_and_conditions_keep_distinct_identities(self):
        with tempfile.TemporaryDirectory() as tmp:
            a=analyze_failure(tmp,'AssertionError: positive',profile='python',profile_id='one')
            b=analyze_failure(tmp,'AssertionError: positive',profile='python',profile_id='two')
            c=analyze_failure(tmp,'AssertionError: negative',profile='python',profile_id='one')
            self.assertNotEqual(a['fingerprint'],b['fingerprint']);self.assertNotEqual(a['fingerprint'],c['fingerprint'])


class ScriptFirstTests(unittest.TestCase):
    setUp=director_tests.DirectorTests.setUp
    tearDown=director_tests.DirectorTests.tearDown
    make_run=director_tests.DirectorTests.make_run
    def test_assertion_repairs_use_programmatic_context_without_planner_diagnosis(self):
        self.config['repair_diagnosis']=True
        plan=workflow();plan['modules']=[{'id':'app','contract':'app.py exports show(n) returning str(n*2).',
            'outcomes':['Calling show(4) returns the string "8".'],'files':['app.py'],
            'references':['test_app.py'],'depends_on':[],'tests':[0]}]
        replies=iter([plan,create('app.py','def show(n): return str(n*3)\n'),
            {'edits':[{'path':'app.py','op':'write','content':'def show(n): return str(n*2)\n'}]},OK,OK])
        calls=[]
        def chat(role,prompt,model):calls.append((role,prompt));return next(replies)
        with patch.object(CodeStudioCore,'chat',side_effect=chat):r=self.make_run().execute()['receipt']
        self.assertEqual(r['status'],'succeeded');self.assertEqual(sum(role=='planner' for role,_ in calls),1)
        self.assertNotIn('repair_diagnoses',r)
        self.assertIn('PROGRAMMATISCHE FEHLERANALYSE',calls[2][1])
    def test_success_of_other_profile_does_not_reset_failure_counter(self):
        run=self.make_run()
        failure={'status':'failed','name':'python','profile_id':'failing','output':'AssertionError: failed contract'}
        run.record_failure(failure)
        run.record_test_progress({'results':[{'returncode':0,'name':'python','profile_id':'different'}]})
        self.assertEqual(run.record_failure(failure)['kind'],'assertion')
        self.assertEqual(run.receipt['failure_analysis'][-1]['attempts'],2)
        run.record_test_progress({'results':[{'returncode':0,'name':'python','profile_id':'failing'}]})
        run.record_failure(failure);self.assertEqual(run.receipt['failure_analysis'][-1]['attempts'],1)

    def test_current_provider_error_is_not_replaced_by_old_test_failure(self):
        plan=workflow();plan['modules']=plan['modules'][1:]
        plan['modules'][0]['depends_on']=[]
        replies=iter([plan,create('app.py','def show(n): return str(n*3)\n'),OSError('Provider disconnected')])
        def chat(*args):
            value=next(replies)
            if isinstance(value,Exception):raise value
            return value
        with patch.object(CodeStudioCore,'chat',side_effect=chat):r=self.make_run().execute()['receipt']
        self.assertEqual(r['status'],'failed')
        self.assertEqual(r['blocker_report']['stop_reason'],'Provider disconnected')
        self.assertIn('Provider disconnected',r['blocker_report']['known'])
        self.assertEqual(r['blocker_report']['last_test_failure']['kind'],'assertion')
        self.assertTrue(r['rollback_verified'])

    def test_final_review_repetition_stops_even_when_tests_pass(self):
        plan=workflow();plan['modules']=plan['modules'][1:];plan['modules'][0]['depends_on']=[]
        rejected={'verdict':'reject','issues':['Required feature still missing'],'summary':'incomplete'}
        calls=[]
        def chat(role,prompt,model):
            calls.append(role)
            if role=='planner':return plan
            if role=='coder':
                if not (self.project/'app.py').exists():return create('app.py','def show(n): return str(n*2)\n')
                return {'edits':[],'notes':'unchanged'}
            if 'Review the entire result' in prompt:return rejected
            return OK
        with patch.object(CodeStudioCore,'chat',side_effect=chat):r=self.make_run(limits={'repairs':5}).execute()['receipt']
        self.assertEqual(r['status'],'stalled',r.get('error'))
        self.assertEqual(r['blocker_report']['phase'],'final_review')
        self.assertEqual(r['blocker_report']['attempts'],4)
        self.assertEqual(r['test']['returncode'],0);self.assertTrue(r['rollback_verified'])

    def test_legacy_steps_cannot_repeat_same_failed_check_past_four(self):
        plan={'plan':['Implement formatter'],'files':['app.py'],'acceptance':['Return doubled values'],'questions':[]}
        count=0
        def chat(role,prompt,model):
            nonlocal count
            if role=='planner':return plan
            if role=='reviewer':return OK
            count+=1
            return {'edits':[{'path':'app.py','op':'create' if count==1 else 'write',
                              'content':f'def show(n): return str(n*{count+2})\n'}]}
        with patch.object(CodeStudioCore,'chat',side_effect=chat):
            r=self.make_run(planning='steps',limits={'repairs':5}).execute()['receipt']
        self.assertEqual(r['status'],'stalled',r.get('error'))
        self.assertEqual(count,4);self.assertEqual(r['blocker_report']['attempts'],4)
        self.assertTrue(r['rollback_verified']);self.assertFalse((self.project/'app.py').exists())

    def test_invalid_proposals_do_not_count_old_test_as_new_execution(self):
        plan=workflow();plan['modules']=plan['modules'][1:];plan['modules'][0]['depends_on']=[]
        replies=iter([plan,create('app.py','def show(n): return str(n*3)\n'),
                      ValueError('Invalid edit shape'),ValueError('Invalid edit shape'),ValueError('Invalid edit shape')])
        def chat(*args):
            value=next(replies)
            if isinstance(value,Exception):raise value
            return value
        run=self.make_run(workflow={'schema':'codestudio.modules.v1','modules':plan['modules']},limits={'repairs':3})
        next(replies)
        with patch.object(CodeStudioCore,'chat',side_effect=chat):r=run.execute()['receipt']
        assertions=[a for a in r['failure_analysis'] if a['kind']=='assertion']
        proposals=[a for a in r['failure_analysis'] if a['phase']=='proposal']
        self.assertEqual(len(assertions),1);self.assertEqual(assertions[0]['attempts'],1)
        self.assertEqual([a['attempts'] for a in proposals],[1,2,3]);self.assertTrue(r['rollback_verified'])

    def test_invalid_edits_have_one_coder_output_per_visible_attempt(self):
        (self.project/'app.py').write_text('def show(n): return str(n*3)\n')
        self.config['max_review_rounds']=2
        plan=workflow();plan['modules']=plan['modules'][1:];plan['modules'][0]['depends_on']=[]
        run=self.make_run(workflow={'schema':'codestudio.modules.v1','modules':plan['modules']},limits={'repairs':3})
        with patch.object(CodeStudioCore,'chat',return_value=create('app.py','def show(n): return str(n*2)\n')) as calls:
            r=run.execute()['receipt']
        self.assertEqual(calls.call_count,4)
        self.assertEqual(r['status'],'stalled');self.assertEqual(r['blocker_report']['attempts'],4)
        self.assertEqual((self.project/'app.py').read_text(),'def show(n): return str(n*3)\n')

    def test_malformed_json_does_not_hide_an_extra_autonomous_model_call(self):
        run=self.make_run()
        with patch.object(CodeStudioCore,'ollama_request',return_value={'message':{'content':'not JSON'}}) as request:
            with self.assertRaises(RuntimeError):run.core.chat('coder','small task','local')
        self.assertEqual(request.call_count,1)
        self.assertEqual(run.core.model_calls,1)

    def test_repeated_invalid_proposals_can_switch_to_configured_model(self):
        (self.project/'app.py').write_text('def show(n): return str(n*3)\n')
        self.config['ollama_url']='http://127.0.0.1:11439'
        self.config['autonomous_fallback_models']=['backup']
        plan=workflow();plan['modules']=plan['modules'][1:];plan['modules'][0]['depends_on']=[]
        models=[]
        def chat(role,prompt,model):
            if role=='reviewer':return OK
            models.append(model)
            return {'edits':[{'path':'app.py','op':'write' if model=='backup' else 'create',
                              'content':'def show(n): return str(n*2)\n'}]}
        with patch.object(CodeStudioCore,'installed_models',return_value=['local','backup']), patch.object(CodeStudioCore,'chat',side_effect=chat):
            r=self.make_run(workflow={'schema':'codestudio.modules.v1','modules':plan['modules']},limits={'repairs':3}).execute()['receipt']
        self.assertEqual(r['status'],'succeeded',r.get('error'))
        self.assertEqual(models,['local','local','backup'])
        self.assertEqual(r['model_switches'][0]['reason'],'two_unsuccessful_module_attempts')

    def test_repeated_qc_rejections_also_switch_configured_model(self):
        self.config['ollama_url']='http://127.0.0.1:11439';self.config['autonomous_fallback_models']=['backup']
        plan=workflow();plan['modules']=plan['modules'][1:];plan['modules'][0]['depends_on']=[]
        models=[]
        def chat(role,prompt,model):
            if role=='reviewer':return OK if model=='backup' else {'verdict':'reject','issues':['missing required behavior'],'summary':'reject'}
            models.append(model)
            return {'edits':[{'path':'app.py','op':'create' if len(models)==1 else 'write',
                              'content':f'def show(n): return str(n*2)\n# attempt {len(models)}\n'}]}
        with patch.object(CodeStudioCore,'installed_models',return_value=['local','backup']), patch.object(CodeStudioCore,'chat',side_effect=chat):
            r=self.make_run(workflow={'schema':'codestudio.modules.v1','modules':plan['modules']},limits={'repairs':3}).execute()['receipt']
        self.assertEqual(r['status'],'succeeded',r.get('error'))
        self.assertEqual(models,['local','local','backup'])
