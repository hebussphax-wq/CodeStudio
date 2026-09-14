import json,pathlib,tempfile,unittest
from unittest.mock import patch,MagicMock
from localtools import ensure_tool
class LocalToolsTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.tmp.name);self.exe=self.root/'tool.exe';self.exe.write_bytes(b'fixture');self.path=self.root/'tools.json'
  self.profile={'enabled':True,'endpoint':'http://127.0.0.1:11439','executable':str(self.exe),'cwd':str(self.root),'args':['serve'],'env':{'OLLAMA_NO_CLOUD':'true'}}
  self.path.write_text(json.dumps({'schema':'codestudio.local-tools.v1','tools':{'ollama':self.profile}}))
 def tearDown(self):self.tmp.cleanup()
 def test_running_service_not_duplicated(self):
  with patch('localtools.probe',return_value={'models':[]}),patch('localtools.subprocess.Popen') as launch:
   self.assertEqual(ensure_tool(self.path,'ollama',self.profile['endpoint'])['status'],'running');launch.assert_not_called()
 def test_start_uses_absolute_argv_and_records_verified_health(self):
  child=MagicMock(pid=123);child.poll.return_value=None
  with patch('localtools.probe',side_effect=[None,{'models':[]}]),patch('localtools.subprocess.Popen',return_value=child) as launch:
   result=ensure_tool(self.path,'ollama',self.profile['endpoint'])
  self.assertEqual(result['status'],'running');self.assertEqual(launch.call_args.args[0],[str(self.exe),'serve']);self.assertFalse((self.root/'ollama.starting').exists())
 def test_timeout_retains_lock_preventing_duplicate(self):
  child=MagicMock(pid=123);child.poll.return_value=None
  with patch('localtools.probe',return_value=None),patch('localtools.subprocess.Popen',return_value=child) as launch:
   with self.assertRaises(RuntimeError):ensure_tool(self.path,'ollama',self.profile['endpoint'],timeout=0)
   with self.assertRaises(RuntimeError):ensure_tool(self.path,'ollama',self.profile['endpoint'],timeout=0)
   self.assertEqual(launch.call_count,1)
 def test_unregistered_endpoint_never_starts_program(self):
  with patch('localtools.subprocess.Popen') as launch:
   self.assertEqual(ensure_tool(self.path,'ollama','http://127.0.0.1:9999')['status'],'externally_managed');launch.assert_not_called()

 def test_late_healthy_owned_process_clears_only_its_timeout_lock(self):
  child=MagicMock(pid=123);child.poll.return_value=None
  with patch('localtools.process_stamp',return_value='created-A'),patch('localtools.probe',return_value=None),patch('localtools.subprocess.Popen',return_value=child):
   with self.assertRaises(RuntimeError):ensure_tool(self.path,'ollama',self.profile['endpoint'],timeout=0)
  with patch('localtools.process_stamp',return_value='created-A'),patch('localtools.probe',return_value={'models':[]}):
   self.assertEqual(ensure_tool(self.path,'ollama',self.profile['endpoint'])['status'],'running')
  self.assertFalse((self.root/'ollama.starting').exists())
  self.assertTrue(json.loads((self.root/'ollama-start.json').read_text())['late_start_reconciled'])
  with patch('localtools.process_stamp',return_value='created-B'),patch('localtools.probe',side_effect=[None,{'models':[]}]),patch('localtools.subprocess.Popen',return_value=child) as launch:
   ensure_tool(self.path,'ollama',self.profile['endpoint']);self.assertEqual(launch.call_count,1)
 def test_reused_pid_cannot_clear_timeout_lock(self):
  child=MagicMock(pid=123);child.poll.return_value=None
  with patch('localtools.process_stamp',return_value='created-A'),patch('localtools.probe',return_value=None),patch('localtools.subprocess.Popen',return_value=child):
   with self.assertRaises(RuntimeError):ensure_tool(self.path,'ollama',self.profile['endpoint'],timeout=0)
  with patch('localtools.process_stamp',return_value='different-process'),patch('localtools.probe',return_value={'models':[]}):
   ensure_tool(self.path,'ollama',self.profile['endpoint'])
  self.assertTrue((self.root/'ollama.starting').exists())
