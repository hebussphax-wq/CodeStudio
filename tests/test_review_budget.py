import pathlib,tempfile,unittest
from unittest.mock import patch
from core import CodeStudioCore,SCHEMA
class ReviewBudgetTests(unittest.TestCase):
 def test_reviewer_bounded_without_changing_coder_budget(self):
  with tempfile.TemporaryDirectory() as d:
   core=CodeStudioCore(pathlib.Path(d),{'workspace':d,'options':{'num_predict':6144,'num_ctx':8192},'think':False})
   with patch.object(core,'ollama_request',return_value={'message':{'content':'{}'}}) as send:
    core.chat('reviewer','contract','local');review=send.call_args.args[1]
    core.chat('coder','contract','local');coder=send.call_args.args[1]
   self.assertEqual(review['options']['num_predict'],1024);self.assertEqual(coder['options']['num_predict'],6144)
   self.assertEqual(core.config['options']['num_predict'],6144);self.assertFalse(review['think'])
   self.assertEqual(review['format']['properties']['issues']['maxItems'],4)

 def test_module_schema_has_one_source_copy_and_keeps_legacy_replace(self):
  with tempfile.TemporaryDirectory() as d:
   core=CodeStudioCore(pathlib.Path(d),{'workspace':d});core.module_mode=True
   item=core.output_schema('coder')['properties']['edits']['items']
   self.assertEqual(set(item['required']),{'path','op','content'})
   self.assertNotIn('old_text',item['properties']);self.assertFalse(item['additionalProperties'])
   core.module_mode=False;self.assertIn('old_text',core.output_schema('coder')['properties']['edits']['items']['properties'])
