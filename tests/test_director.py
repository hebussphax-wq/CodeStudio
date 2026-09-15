import copy, json, pathlib, sys, tempfile, unittest, uuid
from unittest.mock import patch
from autonomy import AutonomousRun
from core import CodeStudioCore
from director import validate_director, DIRECTOR_SCHEMA

OK={'verdict':'ok','issues':[],'summary':'matches contract'}
def workflow():
    return {'acceptance':['Format doubled values'], 'assumptions':[], 'questions':[], 'modules':[
        {'id':'values','contract':'values.py exports double(n) returning n*2.', 'files':['values.py'],
         'references':['test_app.py'],'depends_on':[],'tests':[]},
        {'id':'app','contract':'app.py imports double from values and exports show(n) returning str(double(n)).',
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
    def test_final_review_triggers_bounded_replanning_without_operator(self):
        repair=workflow();repair['modules']=[{'id':'repair','contract':'Add required description to app.py without changing show.',
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
            'files':['values.py'],'references':['test_app.py'],'depends_on':[],'tests':[0]}]
        (self.project/'spec.md').write_text('Doubled means multiplied by two.')
        initial=workflow();initial['modules'][0]['references'].append('spec.md')
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
        self.assertIn('spec.md',r['final_context']);self.assertEqual(len(r['plan_history']),2)
    def test_unrelated_earlier_test_does_not_verify_pending_module(self):
        (self.project/'test_values.py').write_text('from values import double\nassert double(4)==8\n')
        self.config['tests'].insert(0,{'argv':[sys.executable,'-B','test_values.py']})
        plan=workflow();plan['modules'][0]['tests']=[0]
        plan['modules'].insert(1,{'id':'extra','contract':'extra.py exports description="Doubler".',
            'files':['extra.py'],'references':[],'depends_on':['values'],'tests':[]})
        replies=iter([plan,create('values.py','def double(n): return n*2\n'),OK,
            create('extra.py','description="Doubler"\n'),OK,
            create('app.py','from values import double\ndef show(n): return str(double(n))\n'),OK,OK])
        with patch.object(CodeStudioCore,'chat',side_effect=lambda *args:next(replies)):r=self.make_run().execute()['receipt']
        self.assertEqual(r['status'],'succeeded');self.assertEqual(r['steps'][1]['status'],'reviewed_pending_tests')

if __name__=='__main__': unittest.main()
