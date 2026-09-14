import json, pathlib, tempfile, unittest, uuid, sys
from unittest.mock import patch
from autonomy import AutonomousRun
from core import CodeStudioCore
from moduleflow import normalize_workflow
OK={'verdict':'ok','issues':[],'summary':'verified'}
def change(content):return {'edits':[{'path':'calc.py','op':'write','content':content}],'notes':''}
class ModuleTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(); self.root=pathlib.Path(self.temp.name)
  (self.root/'calc.py').write_text('a=0\nb=0\n')
  (self.root/'test_a.py').write_text('from calc import a\nassert a==2, "a must equal 2"\n')
  (self.root/'test_b.py').write_text('from calc import b\nassert b==3, "b must equal 3"\n')
  self.config={'workspace':str(self.root),'state_dir':str(self.root/'state'),'tests':[{'argv':[sys.executable,'-B','test_a.py']},{'argv':[sys.executable,'-B','test_b.py']}]}
  self.workflow={'schema':'codestudio.modules.v1','modules':[
   {'id':'a','contract':'a=2','files':['calc.py'],'references':['test_a.py'],'tests':[0],'models':{'coder':'small','reviewer':'review'}},
   {'id':'b','contract':'preserve a and b=3','files':['calc.py'],'references':['test_b.py'],'tests':[1],'depends_on':['a']}]}
 def tearDown(self):self.temp.cleanup()
 def run_new(self,repairs=1):
  self.events=[]
  return AutonomousRun(self.root,self.config,{'run_id':uuid.uuid4().hex,'approved':True,'task':'a=2 b=3','model':'default','workflow':self.workflow,'limits':{'repairs':repairs}},self.events.append)
 def test_module_repairs_before_next_and_routes_roles(self):
  calls=[]; replies=iter([change('a=1\nb=0\n'),change('a=2\nb=0\n'),OK,change('a=2\nb=3\n'),OK,OK])
  def chat(role,prompt,model):calls.append((role,prompt,model));return next(replies)
  run=self.run_new()
  with patch.object(CodeStudioCore,'chat',side_effect=chat): result=run.execute()['receipt']
  self.assertEqual(result['status'],'succeeded')
  self.assertEqual([x['module_id'] for x in result['attempts']],['a','a','b'])
  self.assertEqual([x['test']['returncode'] for x in result['attempts']],[1,0,0])
  self.assertEqual([x[2] for x in calls],['small','small','review','default','default','default'])
  self.assertIn('a must equal 2',calls[1][1]);self.assertNotIn('test_b.py',calls[0][1])
  self.assertEqual(self.events[-1]['status'],'succeeded')
  self.assertEqual(result['updated_at'],result['finished_at'])
 def test_failed_module_blocks_next_and_rolls_back(self):
  run=self.run_new(0)
  with patch.object(CodeStudioCore,'chat',side_effect=[change('a=1\nb=0\n'),OK]) as chat: result=run.execute()['receipt']
  self.assertEqual(chat.call_count,1);self.assertEqual(result['status'],'budget_exhausted')
  self.assertTrue(result['rollback_verified']);self.assertEqual(result['steps'],[])
  self.assertEqual((self.root/'calc.py').read_text(),'a=0\nb=0\n')
  self.assertEqual(self.events[-1]['status'],'budget_exhausted')
 def test_previous_gate_is_rechecked(self):
  run=self.run_new(0)
  with patch.object(CodeStudioCore,'chat',side_effect=[change('a=2\nb=0\n'),OK,change('a=0\nb=3\n'),OK]):result=run.execute()['receipt']
  self.assertEqual(result['status'],'budget_exhausted');self.assertEqual(len(result['steps']),1)
  self.assertTrue(result['rollback_verified'])
 def test_invalid_dependency_test_and_escape_rejected_before_run(self):
  import copy
  for key,value in [('depends_on',['future']),('tests',[5]),('files',['../escape.py'])]:
   flow=copy.deepcopy(self.workflow);flow['modules'][0][key]=value
   with self.assertRaises(ValueError):normalize_workflow(flow,2,6)
 def test_reference_files_cannot_become_write_scope(self):
  run=self.run_new(0);run.core.config['max_review_rounds']=0
  with patch.object(CodeStudioCore,'chat',return_value={'edits':[{'path':'test_a.py','op':'write','content':'pass'}]}):result=run.execute()['receipt']
  self.assertNotEqual(result['status'],'succeeded')
  self.assertIn('assert a==2',(self.root/'test_a.py').read_text())
 def test_truncated_response_is_explicit_failure(self):
  core=CodeStudioCore(self.root,self.config)
  with patch.object(core,'ollama_request',return_value={'done_reason':'length','message':{'content':'{}'}}):
   with self.assertRaisesRegex(RuntimeError,'abgeschnitten'):core.chat('coder','x','small')

 def test_overlapping_reference_rejected(self):
  self.workflow['modules'][0]['references']=['calc.py']
  with self.assertRaises(ValueError):self.run_new()
 def test_original_task_review_can_reject_green_modules(self):
  run=self.run_new()
  with patch.object(CodeStudioCore,'chat',side_effect=[change('a=2\nb=0\n'),OK,change('a=2\nb=3\n'),OK,{'verdict':'reject','issues':['missing original feature'],'summary':'incomplete'}]):result=run.execute()['receipt']
  self.assertEqual(result['status'],'failed');self.assertTrue(result['rollback_verified'])

 def test_invalid_repair_keeps_original_test_diagnosis(self):
  run=self.run_new(2);run.core.config['max_review_rounds']=0;prompts=[]
  replies=iter([change('a=1\nb=0\n'),change('a=1\nb=0\n'),change('a=2\nb=0\n'),OK,change('a=2\nb=3\n'),OK,OK])
  def chat(role,prompt,model):prompts.append(prompt);return next(replies)
  with patch.object(CodeStudioCore,'chat',side_effect=chat):result=run.execute()['receipt']
  self.assertEqual(result['status'],'succeeded');self.assertIn('a must equal 2',prompts[2]);self.assertIn('Keine effektive',prompts[2])
