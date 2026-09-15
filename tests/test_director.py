import copy, json, pathlib, sys, tempfile, unittest, uuid
from unittest.mock import patch
from autonomy import AutonomousRun
from core import CodeStudioCore
from director import validate_director, DIRECTOR_SCHEMA

OK={'verdict':'ok','issues':[],'summary':'matches contract'}
def workflow():
    return {'acceptance':['Format doubled values'], 'assumptions':[], 'questions':[], 'modules':[
        {'id':'values','contract':'values.py exports double(n) returning n*2.', 'outcomes':['Calling double(4) returns 8; negative inputs retain their sign.'], 'files':['values.py'],
         'references':['test_app.py'],'depends_on':[],'tests':[]},
        {'id':'app','contract':'app.py imports double from values and exports show(n) returning str(double(n)).',
         'outcomes':['Calling show(4) returns the string "8"; show(-2) returns "-4".'],
         'files':['app.py'],'references':['test_app.py'],'depends_on':['values'],'tests':[0]}]}
def create(path, content): return {'edits':[{'path':path,'op':'create','content':content}],'notes':''}

class DirectorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.temp.name)
        self.project=self.root/'project';self.project.mkdir()
        (self.project/'test_app.py').write_text('from app import show\nassert show(4)=="8"\nassert show(-2)=="-4"\nprint("2 contract checks passed")\n')
        self.config={'workspace':str(self.project),'state_dir':str(self.root/'state'),
                     'tests':[{'argv':[sys.executable,'-B','test_app.py']}], 'repair_diagnosis':False}
    def tearDown(self):self.temp.cleanup()
    def make_run(self,**args):
        return AutonomousRun(self.root,self.config,{'run_id':uuid.uuid4().hex,'approved':True,
              'task':'Implement a formatter for doubled values.', 'model':'local', **args})
    def test_default_brief_generates_modules_with_real_final_tests(self):
        calls=[]; replies=iter([workflow(),create('values.py','def double(n): return n*2\n'),OK,
                             create('app.py','from values import double\ndef show(n): return str(double(n))\n'),OK,OK])
        def chat(role,prompt,model): calls.append((role,prompt)); return next(replies)
        run=self.make_run()
        with patch.object(CodeStudioCore,'chat',side_effect=chat):result=run.execute()['receipt']
        self.assertEqual(result['status'],'succeeded')
        self.assertEqual(result['planning_origin'],'model_from_original_task')
        self.assertEqual(result['steps'][0]['status'],'reviewed_pending_tests')
        self.assertEqual(result['attempts'][0]['test']['status'],'deferred')
        self.assertIsNone(result['attempts'][0]['test']['returncode'])
        self.assertIn('def double',calls[3][1])
        self.assertEqual(result['test']['returncode'],0)
        self.assertIn('test_app.py',result['planning_context'])
        self.assertFalse(run.core.director_mode)
    def test_invalid_plan_repaired_before_any_project_write(self):
        bad=workflow();bad['modules'][0]['files']=['../escape.py']
        replies=iter([bad,workflow(),create('values.py','def double(n): return n*2\n'),OK,
                      create('app.py','from values import double\ndef show(n): return str(double(n))\n'),OK,OK])
        prompts=[]
        def chat(role,prompt,model):
            prompts.append(prompt)
            if len(prompts)<=2:self.assertFalse((self.project/'values.py').exists())
            return next(replies)
        run=self.make_run()
        with patch.object(CodeStudioCore,'chat',side_effect=chat):r=run.execute()['receipt']
        self.assertEqual(r['status'],'succeeded');self.assertIn('VALIDATION ERROR',prompts[1])
    def test_truncated_director_answer_is_repaired_with_original_budget(self):
        from core import ModelOutputError
        replies=iter([ModelOutputError('output length',{'eval_count':4096}),workflow(),
            create('values.py','def double(n): return n*2\n'),OK,
            create('app.py','from values import double\ndef show(n): return str(double(n))\n'),OK,OK])
        def chat(*args):
            value=next(replies)
            if isinstance(value,Exception):raise value
            return value
        with patch.object(CodeStudioCore,'chat',side_effect=chat):r=self.make_run(limits={'repairs':1}).execute()['receipt']
        self.assertEqual(r['status'],'succeeded');self.assertEqual(r['model_output_failures'][0]['eval_count'],4096)
        self.assertEqual(len(r['planning_failures']),1)
    def test_correct_existing_module_is_tested_without_artificial_write(self):
        (self.project/'values.py').write_text('def double(n): return n*2\n')
        replies=iter([workflow(),{'edits':[],'notes':'already correct'},OK,
            create('app.py','from values import double\ndef show(n): return str(double(n))\n'),OK,OK])
        with patch.object(CodeStudioCore,'chat',side_effect=lambda *args:next(replies)):r=self.make_run().execute()['receipt']
        self.assertEqual(r['status'],'succeeded');self.assertEqual(r['attempts'][0]['files'],[])
        self.assertEqual(r['test']['returncode'],0)
    def test_transport_validation_error_is_not_retried_as_a_plan(self):
        with patch.object(CodeStudioCore,'chat',side_effect=ValueError('host binding expired')) as chat:
            r=self.make_run().execute()['receipt']
        self.assertEqual(chat.call_count,1);self.assertNotIn('planning_failures',r)
        self.assertNotEqual(r['status'],'succeeded')
    def test_many_protected_tests_do_not_force_all_into_planning_context(self):
        for i in range(30):(self.project/('test_unrelated_'+str(i)+'.py')).write_text('assert True\n')
        replies=iter([workflow(),create('values.py','def double(n): return n*2\n'),OK,
            create('app.py','from values import double\ndef show(n): return str(double(n))\n'),OK,OK])
        with patch.object(CodeStudioCore,'chat',side_effect=lambda *args:next(replies)):r=self.make_run().execute()['receipt']
        self.assertEqual(r['status'],'succeeded');self.assertTrue(r['planning_context_omitted'])
        self.assertLessEqual(len(r['planning_context']),24)
        self.assertGreater(len(r['protected_tests']),24)
    def test_reversible_planning_questions_get_one_bounded_refinement(self):
        first=workflow();first['questions']=['Should I implement the missing formatter?']
        replies=iter([first,workflow(),create('values.py','def double(n): return n*2\n'),OK,
            create('app.py','from values import double\ndef show(n): return str(double(n))\n'),OK,OK])
        prompts=[]
        def chat(role,prompt,model):prompts.append(prompt);return next(replies)
        with patch.object(CodeStudioCore,'chat',side_effect=chat):r=self.make_run().execute()['receipt']
        self.assertEqual(r['status'],'succeeded');self.assertIn('RECONSIDER PROPOSED QUESTIONS',prompts[1])
    def test_essential_question_is_not_silently_discarded(self):
        first=workflow();first['questions']=['Which external interface contract applies?']
        with patch.object(CodeStudioCore,'chat',return_value=first) as chat:r=self.make_run().execute()['receipt']
        self.assertEqual(r['status'],'blocked');self.assertEqual(chat.call_count,2)
        self.assertFalse((self.project/'values.py').exists());self.assertEqual(len(r['planning_questions']),2)
    def test_protected_future_and_invented_profiles_rejected(self):
        for key,value in [('files',['test_app.py']),('references',['app.py']),('tests',[99]),('models',{'coder':'invented'})]:
            plan=workflow();plan['modules'][0][key]=value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_director(plan,1,6,['test_app.py'],['test_app.py'])
    def test_all_profiles_mandatory_even_when_planner_omits(self):
        plan=workflow();plan['modules'][-1]['tests']=[]
        self.assertEqual(validate_director(plan,2,6,['test_app.py'],['test_app.py'])['modules'][-1]['tests'],[0,1])
    def test_omitted_spec_and_test_references_are_added_by_runtime(self):
        (self.project/'README.md').write_text('API: double(n) and show(n).')
        plan=workflow()
        for m in plan['modules']:m['references']=[]
        replies=iter([plan,create('values.py','def double(n): return n*2\n'),OK,
                     create('app.py','from values import double\ndef show(n): return str(double(n))\n'),OK,OK])
        prompts=[]
        def chat(role,prompt,model):
            if role=='coder':prompts.append(prompt)
            return next(replies)
        with patch.object(CodeStudioCore,'chat',side_effect=chat):r=self.make_run().execute()['receipt']
        self.assertEqual(r['status'],'succeeded')
        self.assertIn('API: double(n)',prompts[0]);self.assertIn('assert show(-2)',prompts[1])
    def test_contract_change_during_planning_stops_before_writes(self):
        run=self.make_run()
        def chat(*args): (self.project/'test_app.py').write_text('user change'); return workflow()
        with patch.object(CodeStudioCore,'chat',side_effect=chat):r=run.execute()['receipt']
        self.assertEqual(r['status'],'conflict');self.assertFalse((self.project/'values.py').exists())
        self.assertEqual((self.project/'test_app.py').read_text(),'user change')
    def test_director_uses_host_planner_role_and_resets_schema(self):
        calls=[]
        def transport(kind, payload):calls.append((kind,payload));return workflow()
        run=self.make_run();run.core.transport=transport;run.core.director_mode=True
        run.core.chat('planner','brief','local')
        self.assertEqual(calls[0][1]['role'],'planner')
        self.assertEqual(calls[0][1]['schema'],DIRECTOR_SCHEMA)
        run.core.director_mode=False;run.core.module_mode=True
        self.assertIn('plan',run.core.output_schema('planner')['properties'])
        self.assertNotIn('modules',run.core.output_schema('planner')['properties'])
    def test_empty_node_test_suite_never_passes(self):
        run=self.make_run()
        with patch('autonomy.run_command',return_value={'returncode':0,'status':'passed','output':'# tests 0\n# pass 0\n'}):
            result=run.core.run_tests()
        self.assertEqual(result['status'],'no_tests');self.assertNotEqual(result['returncode'],0)
    def test_invalid_advisory_diagnosis_does_not_prevent_test_driven_repair(self):
        self.config['repair_diagnosis']=True
        plan=workflow();plan['modules']=[{'id':'app','contract':'app.py exports show(n) returning str(n*2).',
            'outcomes':['Calling show(4) returns the string "8".'],
            'files':['app.py'],'references':['test_app.py'],'depends_on':[],'tests':[0]}]
        replies=iter([plan,create('app.py','def show(n): return str(n*3)\n'),{'plan':['too long '*300]},
            {'edits':[{'path':'app.py','op':'write','content':'def show(n): return str(n*2)\n'}]},OK,OK])
        prompts=[]
        def chat(role,prompt,model):
            if role=='coder':prompts.append(prompt)
            return next(replies)
        with patch.object(CodeStudioCore,'chat',side_effect=chat):r=self.make_run().execute()['receipt']
        self.assertEqual(r['status'],'succeeded');self.assertEqual(len(r['diagnosis_errors']),1)
        self.assertIn('AssertionError',prompts[-1]);self.assertNotIn('too long',prompts[-1])
    def test_repair_output_schema_matches_runtime_bounds(self):
        run=self.make_run();run.core.module_mode=True
        schema=run.core.output_schema('planner')
        self.assertEqual(schema['properties']['plan']['maxItems'],3)
        self.assertEqual(schema['properties']['plan']['items']['maxLength'],600)
    def test_placeholder_plan_is_repaired_before_coding(self):
        bad=workflow()
        for m in bad['modules']: m['contract']='read-only'
        replies=iter([bad,workflow(),create('values.py','def double(n): return n*2\n'),OK,
                     create('app.py','from values import double\ndef show(n): return str(double(n))\n'),OK,OK])
        prompts=[]
        def chat(role,prompt,model):
            prompts.append((role,prompt))
            if len(prompts)<=2:self.assertFalse((self.project/'values.py').exists())
            return next(replies)
        with patch.object(CodeStudioCore,'chat',side_effect=chat):r=self.make_run().execute()['receipt']
        self.assertEqual(r['status'],'succeeded')
        self.assertEqual([x[0] for x in prompts[:3]],['planner','planner','coder'])
        self.assertEqual(len(r['planning_errors']),1)
        self.assertIn('Calling double(4) returns 8',prompts[2][1])
    def test_missing_outcomes_and_category_acceptance_rejected(self):
        for kind in ('missing','empty','category','contract'):
            value=workflow()
            if kind=='missing':del value['modules'][0]['outcomes']
            elif kind=='empty':value['modules'][0]['outcomes']=[]
            elif kind=='category':value['acceptance']=['values']
            else:value['modules'][0]['contract']='values.py'
            with self.subTest(kind=kind),self.assertRaises(ValueError):
                validate_director(value,1,6,['test_app.py'],['test_app.py'])
    def test_repeated_placeholder_plans_stop_without_project_writes(self):
        bad=workflow()
        for m in bad['modules']:m['contract']='read-only'
        run=self.make_run(limits={'repairs':1})
        with patch.object(CodeStudioCore,'chat',return_value=bad) as chat:
            r=run.execute()['receipt']
        self.assertNotEqual(r['status'],'succeeded');self.assertEqual(chat.call_count,2)
        self.assertFalse((self.project/'values.py').exists())
        failure=r['planning_failures'][-1]
        self.assertEqual(failure['path'],'modules[0].contract')
        self.assertEqual(failure['attempt'],2)
        self.assertIn('read-only',failure['response_preview'])
        self.assertEqual(len(failure['response_sha256']),64)
        self.assertEqual(json.loads(run.path.read_text(encoding='utf8'))['planning_failures'][-1],failure)
    def test_invalid_director_fallback_keeps_original_attempt_and_call_budgets(self):
        self.config.update(ollama_url='http://127.0.0.1:11439',autonomous_fallback_models=['backup'])
        bad=workflow();bad['modules'][1]['outcomes']=['app']
        seen=[]
        replies=iter([bad,bad,workflow(),create('values.py','def double(n): return n*2\n'),OK,
                     create('app.py','from values import double\ndef show(n): return str(double(n))\n'),OK,OK])
        def chat(role,prompt,model):
            seen.append((role,model,prompt))
            if role=='planner':self.assertFalse((self.project/'values.py').exists())
            return next(replies)
        run=self.make_run(limits={'repairs':2,'model_calls':8})
        with patch.object(CodeStudioCore,'installed_models',return_value=['local','backup']),patch.object(CodeStudioCore,'chat',side_effect=chat):
            r=run.execute()['receipt']
        self.assertEqual(r['status'],'succeeded')
        self.assertEqual([m for role,m,prompt in seen[:3]],['local','local','backup'])
        self.assertIn('modules[1].outcomes[0]',seen[1][2])
        self.assertEqual(r['model_calls'],8)
        self.assertEqual(r['model_switches'][0]['phase'],'planning')
        self.assertEqual(run.fallback_index,1)
    def test_invalid_director_never_adds_attempts_or_masks_transport_cancellation(self):
        self.config.update(ollama_url='http://127.0.0.1:11439',autonomous_fallback_models=['backup'])
        bad=workflow();bad['acceptance']=['app']
        with patch.object(CodeStudioCore,'installed_models',return_value=['backup']),patch.object(CodeStudioCore,'chat',return_value=bad) as chat:
            r=self.make_run(limits={'repairs':1}).execute()['receipt']
        self.assertEqual(chat.call_count,2);self.assertNotIn('model_switches',r)
        from autonomy import RunStopped
        with patch.object(CodeStudioCore,'installed_models',return_value=['backup']),patch.object(CodeStudioCore,'chat',side_effect=RunStopped('cancelled','stop')):
            r=self.make_run().execute()['receipt']
        self.assertEqual(r['status'],'cancelled');self.assertNotIn('planning_failures',r)
    def test_diagnosis_cannot_target_tests_and_is_never_promoted_to_fact(self):
        self.config['repair_diagnosis']=True
        for affected in (['test_app.py'],['app.py']):
            plan=workflow();plan['modules']=[{'id':'app','contract':'app.py exports show(n) returning str(n*2).',
                'outcomes':['Calling show(4) returns the string "8".'],
                'files':['app.py'],'references':['test_app.py'],'depends_on':[],'tests':[0]}]
            advice='Replace the expected value with the wrong value.'
            replies=iter([plan,create('app.py','def show(n): return str(n*3)\n'),
                {'plan':[advice],'files':affected},
                {'edits':[{'path':'app.py','op':'write','content':'def show(n): return str(n*2)\n'}]},OK,OK])
            prompts=[]
            def chat(role,prompt,model):
                if role=='coder':prompts.append(prompt)
                return next(replies)
            with self.subTest(affected=affected),patch.object(CodeStudioCore,'chat',side_effect=chat):
                r=self.make_run().execute()['receipt']
            self.assertEqual(r['status'],'succeeded')
            self.assertIn('AssertionError',prompts[-1])
            self.assertNotIn('ROOT CAUSE AND REPAIR PLAN',prompts[-1])
            if affected==['test_app.py']:
                self.assertNotIn(advice,prompts[-1]);self.assertEqual(len(r['diagnosis_errors']),1)
            else:
                self.assertIn('UNVERIFIED DIAGNOSTIC HYPOTHESES',prompts[-1]);self.assertIn(advice,prompts[-1])
            (self.project/'app.py').unlink()
    def test_final_review_triggers_bounded_replanning_without_operator(self):
        repair=workflow();repair['modules']=[{'id':'repair','contract':'Add required description to app.py without changing show.',
            'outcomes':['The module has a description and show(4) still returns "8".'],
            'files':['app.py'],'references':['test_app.py','values.py'],'depends_on':[],'tests':[0]}]
        replies=iter([workflow(),create('values.py','def double(n): return n*2\n'),OK,
            create('app.py','from values import double\ndef show(n): return str(double(n))\n'),OK,
            {'verdict':'reject','issues':['Missing description requested in original brief'],'summary':'missing'},repair,
            {'edits':[{'path':'app.py','op':'write','content':'"""Doubled value formatter."""\nfrom values import double\ndef show(n): return str(double(n))\n'}]},OK,OK])
        run=self.make_run()
        with patch.object(CodeStudioCore,'chat',side_effect=lambda *args:next(replies)):r=run.execute()['receipt']
        self.assertEqual(r['status'],'succeeded');self.assertEqual(len(r['integration_repairs']),1)
        self.assertIn('description',r['integration_repairs'][0]['feedback'])
    def test_integration_failure_in_earlier_module_is_replanned(self):
        repair=workflow();repair['modules']=[{'id':'repair-values','contract':'double(n) must return n*2.',
            'outcomes':['Calling double(4) returns 8, and double(-2) returns -4.'],
            'files':['values.py'],'references':['test_app.py'],'depends_on':[],'tests':[0]}]
        (self.project/'spec.md').write_text('Doubled means multiplied by two.')
        asset=b'\x89PNG\r\n\x1a\n\0'+b'x'*30000
        (self.project/'background.png').write_bytes(asset)
        initial=workflow();initial['modules'][0]['references'].append('spec.md')
        initial['modules'][0]['references'].append('background.png')
        noop={'edits':[{'path':'app.py','op':'write','content':'from values import double\ndef show(n): return str(double(n))\n'}]}
        replies=iter([initial,create('values.py','def double(n): return n*3\n'),OK,
            create('app.py','from values import double\ndef show(n): return str(double(n))\n'),noop,repair,
            {'edits':[{'path':'values.py','op':'write','content':'def double(n): return n*2\n'}]},OK,OK])
        calls=[];run=self.make_run(limits={'repairs':1});run.core.config['max_review_rounds']=0
        def chat(role,prompt,model):calls.append((role,prompt));return next(replies)
        with patch.object(CodeStudioCore,'chat',side_effect=chat):r=run.execute()['receipt']
        self.assertEqual(r['status'],'succeeded');self.assertEqual(len(r['integration_repairs']),1)
        repair_prompt=[p for role,p in calls if role=='planner'][-1]
        self.assertIn('def double(n): return n*3',repair_prompt)
        self.assertIn('binary_reference',repair_prompt)
        self.assertIn('background.png',r['final_context'])
        self.assertEqual((self.project/'background.png').read_bytes(),asset)
        for role in ('coder','reviewer'):
            self.assertTrue(any('binary_reference' in p for kind,p in calls if kind==role))
        self.assertIn('spec.md',r['final_context']);self.assertEqual(len(r['plan_history']),2)
    def test_binary_reference_never_becomes_editable_source_or_secret_bypass(self):
        (self.project/'asset.bin').write_bytes(b'\0not source')
        core=self.make_run().core
        context=core.read_files(['asset.bin'],readonly_assets=True)
        self.assertIn('binary_reference',context['asset.bin'])
        self.assertNotIn('asset.bin',core.fully_read)
        with self.assertRaises(ValueError):core.current_text('asset.bin')
        with self.assertRaises(ValueError):core.read_files(['asset.bin'])
        (self.project/'notes.md').write_text('api_key = sk-abcdefghijklmnopqrstuv')
        with self.assertRaises(ValueError):core.read_files(['notes.md'],readonly_assets=True)
        with self.assertRaises(ValueError):core.read_reference('../escape.png')
    def test_binary_reference_change_during_review_is_a_conflict(self):
        (self.project/'background.png').write_bytes(b'\x89PNG\0original')
        plan=workflow();plan['modules'][0]['references'].append('background.png')
        replies=iter([plan,create('values.py','def double(n): return n*2\n'),OK])
        def chat(role,prompt,model):
            if role=='reviewer':(self.project/'background.png').write_bytes(b'\x89PNG\0user changed')
            return next(replies)
        with patch.object(CodeStudioCore,'chat',side_effect=chat):r=self.make_run().execute()['receipt']
        self.assertEqual(r['status'],'conflict')
        self.assertEqual((self.project/'background.png').read_bytes(),b'\x89PNG\0user changed')
    def test_unrelated_earlier_test_does_not_verify_pending_module(self):
        (self.project/'test_values.py').write_text('from values import double\nassert double(4)==8\n')
        self.config['tests'].insert(0,{'argv':[sys.executable,'-B','test_values.py']})
        plan=workflow();plan['modules'][0]['tests']=[0]
        plan['modules'].insert(1,{'id':'extra','contract':'extra.py exports description="Doubler".',
            'outcomes':['Importing description returns the string "Doubler".'],
            'files':['extra.py'],'references':[],'depends_on':['values'],'tests':[]})
        replies=iter([plan,create('values.py','def double(n): return n*2\n'),OK,
            create('extra.py','description="Doubler"\n'),OK,
            create('app.py','from values import double\ndef show(n): return str(double(n))\n'),OK,OK])
        with patch.object(CodeStudioCore,'chat',side_effect=lambda *args:next(replies)):r=self.make_run().execute()['receipt']
        self.assertEqual(r['status'],'succeeded');self.assertEqual(r['steps'][1]['status'],'reviewed_pending_tests')

if __name__=='__main__': unittest.main()
