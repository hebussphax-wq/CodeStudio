import json,pathlib,tempfile,unittest
from unittest.mock import patch,MagicMock,call
from localtools import stop_tool,unload_tool
from safety import digest,canonical,atomic_bytes

class LocalToolsStopTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.tmp.name);self.exe=self.root/'tool.exe';self.exe.write_bytes(b'fixture');self.path=self.root/'tools.json'
  self.endpoint='http://127.0.0.1:11439'
  self.profile={'enabled':True,'endpoint':self.endpoint,'executable':str(self.exe),'cwd':str(self.root),'args':['serve'],'env':{'OLLAMA_NO_CLOUD':'true'}}
  raw=json.dumps({'schema':'codestudio.local-tools.v1','tools':{'ollama':self.profile}}).encode()
  self.path.write_bytes(raw);self.raw=raw;self.cfg_sha=digest(raw)

 def tearDown(self):self.tmp.cleanup()

 def _write_receipt(self,pid=4242,stamp='created-OWN',status='running',token='lock-token-1'):
  receipt={'tool':'ollama','pid':pid,'endpoint':self.endpoint,'config_sha256':self.cfg_sha,'status':status,'log':str(self.root/'ollama.log'),'lock_token':token,'process_stamp':stamp}
  atomic_bytes(self.root/'ollama-start.json',canonical(receipt))
  (self.root/'ollama.starting').write_text(token,encoding='utf-8')
  return receipt

 def test_stop_own_receipt_kills_tree_and_marks_stopped(self):
  self._write_receipt()
  with patch('localtools.probe',side_effect=[{'models':[]},None]),\
       patch('localtools.process_stamp',return_value='created-OWN'),\
       patch('localtools.subprocess.run') as run:
   result=stop_tool(self.path,'ollama',self.endpoint,timeout=5)
  self.assertEqual(result['status'],'stopped')
  self.assertEqual(result['pid'],4242)
  run.assert_called_once()
  argv=run.call_args.args[0]
  self.assertEqual(argv[0],r'C:\Windows\System32\taskkill.exe')
  self.assertEqual(argv[1:],['/PID','4242','/T','/F'])
  self.assertFalse(run.call_args.kwargs.get('shell'))
  stopped=json.loads((self.root/'ollama-start.json').read_bytes())
  self.assertEqual(stopped['status'],'stopped')
  self.assertFalse((self.root/'ollama.starting').exists())

 def test_refuse_foreign_healthy_service(self):
  # Healthy probe but receipt stamp does not match live process -> Fremder Dienst
  self._write_receipt(stamp='created-OWN')
  with patch('localtools.probe',return_value={'models':[]}),\
       patch('localtools.process_stamp',return_value='foreign-stamp'),\
       patch('localtools.subprocess.run') as run:
   with self.assertRaises(RuntimeError) as ctx:
    stop_tool(self.path,'ollama',self.endpoint)
  self.assertIn('Fremder Dienst',str(ctx.exception))
  self.assertIn('kein Stop',str(ctx.exception))
  run.assert_not_called()

 def test_already_stopped_without_own_receipt(self):
  with patch('localtools.probe',return_value=None),patch('localtools.subprocess.run') as run:
   result=stop_tool(self.path,'ollama',self.endpoint)
  self.assertEqual(result['status'],'already_stopped')
  run.assert_not_called()

 def test_unload_ollama_keep_alive_zero_path(self):
  # Explicit model: unload via generate keep_alive:0 without listing /api/ps
  with patch('localtools.probe') as probe, patch('localtools.post_json',return_value={'done':True}) as post:
   result=unload_tool(self.path,'ollama',self.endpoint,model='llama3.2:latest')
  self.assertEqual(result['status'],'unloaded')
  self.assertEqual(result['models'],['llama3.2:latest'])
  self.assertTrue(result['actions'][0]['ok'])
  probe.assert_not_called()
  post.assert_called_once_with(self.endpoint,'/api/generate',{'model':'llama3.2:latest','keep_alive':0,'prompt':''})

if __name__=='__main__':
 unittest.main()
