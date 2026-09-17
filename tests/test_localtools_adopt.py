import json,pathlib,tempfile,unittest
from unittest.mock import patch
from localtools import ensure_tool,stop_tool
from safety import digest,canonical,atomic_bytes

class LocalToolsAdoptTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.tmp.name);self.exe=self.root/'ollama.exe';self.exe.write_bytes(b'fixture');self.path=self.root/'tools.json'
  self.endpoint='http://127.0.0.1:11439'
  self.profile={'enabled':True,'endpoint':self.endpoint,'executable':str(self.exe),'cwd':str(self.root),'args':['serve'],'env':{'OLLAMA_NO_CLOUD':'true'}}
  raw=json.dumps({'schema':'codestudio.local-tools.v1','tools':{'ollama':self.profile}}).encode()
  self.path.write_bytes(raw);self.raw=raw;self.cfg_sha=digest(raw)

 def tearDown(self):self.tmp.cleanup()

 def test_adopt_writes_receipt_when_listener_matches_profile_exe(self):
  # Healthy already, no receipt -> adopt PID on port if image path == profile executable
  with patch('localtools.probe',return_value={'models':[]}),\
       patch('localtools.subprocess.Popen') as launch,\
       patch('localtools._pids_listening_on_port',return_value=[5151]),\
       patch('localtools._parent_pid',return_value=None),\
       patch('localtools._process_image_path',return_value=str(self.exe)),\
       patch('localtools.process_stamp',return_value='stamp-ADOPT'):
   result=ensure_tool(self.path,'ollama',self.endpoint)
  self.assertEqual(result['status'],'running')
  launch.assert_not_called()
  receipt=json.loads((self.root/'ollama-start.json').read_bytes())
  self.assertEqual(receipt['status'],'running')
  self.assertEqual(receipt['pid'],5151)
  self.assertEqual(receipt['tool'],'ollama')
  self.assertEqual(receipt['endpoint'],self.endpoint)
  self.assertEqual(receipt['config_sha256'],self.cfg_sha)
  self.assertEqual(receipt['process_stamp'],'stamp-ADOPT')
  self.assertTrue(receipt.get('adopted_running'))
  # stop_tool must now accept ownership (not Fremder Dienst)
  with patch('localtools.probe',side_effect=[{'models':[]},None]),\
       patch('localtools.process_stamp',return_value='stamp-ADOPT'),\
       patch('localtools.subprocess.run') as run:
   stopped=stop_tool(self.path,'ollama',self.endpoint,timeout=5)
  self.assertEqual(stopped['status'],'stopped')
  self.assertEqual(stopped['pid'],5151)
  run.assert_called_once()

 def test_no_adopt_when_listener_exe_mismatches(self):
  foreign=self.root/'other.exe';foreign.write_bytes(b'x')
  with patch('localtools.probe',return_value={'models':[]}),\
       patch('localtools.subprocess.Popen') as launch,\
       patch('localtools._pids_listening_on_port',return_value=[6161]),\
       patch('localtools._parent_pid',return_value=None),\
       patch('localtools._process_image_path',return_value=str(foreign)),\
       patch('localtools.process_stamp',return_value='stamp-X'):
   result=ensure_tool(self.path,'ollama',self.endpoint)
  self.assertEqual(result['status'],'running')
  launch.assert_not_called()
  self.assertFalse((self.root/'ollama-start.json').exists())
  # stop must refuse — Fremder Dienst
  with patch('localtools.probe',return_value={'models':[]}),\
       patch('localtools.subprocess.run') as run:
   with self.assertRaises(RuntimeError) as ctx:
    stop_tool(self.path,'ollama',self.endpoint)
  self.assertIn('Fremder Dienst',str(ctx.exception))
  run.assert_not_called()

 def test_keep_existing_own_receipt_skips_adopt(self):
  receipt={'tool':'ollama','pid':4242,'endpoint':self.endpoint,'config_sha256':self.cfg_sha,
           'status':'running','process_stamp':'created-OWN','lock_token':'tok'}
  atomic_bytes(self.root/'ollama-start.json',canonical(receipt))
  with patch('localtools.probe',return_value={'models':[]}),\
       patch('localtools.process_stamp',return_value='created-OWN'),\
       patch('localtools._adopt_running_if_ours') as adopt,\
       patch('localtools._pids_listening_on_port') as listen:
   result=ensure_tool(self.path,'ollama',self.endpoint)
  self.assertEqual(result['status'],'running')
  adopt.assert_not_called()
  listen.assert_not_called()
  kept=json.loads((self.root/'ollama-start.json').read_bytes())
  self.assertEqual(kept['pid'],4242)
  self.assertNotIn('adopted_running',kept)

if __name__=='__main__':
 unittest.main()
