import json
import pathlib
import tempfile
import time
import unittest
from unittest.mock import patch
from core import CodeStudioCore
from hosttransport import HostTransport
from service import StudioService

class HostTests(unittest.TestCase):
    def test_bound_core_never_uses_direct_ollama(self):
        with tempfile.TemporaryDirectory() as folder:
            calls=[]
            def transport(command,payload):
                calls.append((command,payload))
                return {'models':['bound']} if command=='models' else {'plan':[]}
            core=CodeStudioCore(pathlib.Path(folder),{'workspace':folder,'ollama_url':'http://127.0.0.1:11434'},transport=transport)
            with patch('urllib.request.urlopen',side_effect=AssertionError('Direct Ollama forbidden')):
                self.assertEqual(core.installed_models(),['bound'])
                self.assertEqual(core.chat('planner','task','bound'),{'plan':[]})
            self.assertEqual([c[0] for c in calls],['models','chat'])

    def test_failed_host_has_no_direct_fallback_and_config_uses_host_context(self):
        with tempfile.TemporaryDirectory() as folder:
            def broken(*_):raise ValueError('Host expired')
            service=StudioService(pathlib.Path(folder),{'workspace':folder},lambda _:None,transport=broken)
            with patch('urllib.request.urlopen',side_effect=AssertionError('Direct Ollama forbidden')):
                with self.assertRaisesRegex(ValueError,'Host expired'):service.handle({'command':'models'})
            service.transport=lambda *_:{'options':{'num_ctx':32768}}
            result=service.handle({'command':'configure','context_tokens':8192,'test_argv':[]})
            self.assertEqual(result['context_tokens'],32768)

    def test_descriptor_rejects_wrong_project_redirect_target_expiry_and_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            root=pathlib.Path(folder);file=root/'binding.json'
            base={'schema':'codestudio.host-session.v1','session_id':'test','workspace':folder,'token':'a'*64,'endpoint':'http://127.0.0.1:5555/model','expires_at':time.time()*1000+60000}
            for changes in [{'workspace':str(root/'missing')},{'endpoint':'https://remote.example/model'},{'expires_at':0}]:
                file.write_text(json.dumps({**base,**changes}))
                with self.assertRaises((ValueError,FileNotFoundError)):HostTransport(str(file),folder)
            file.write_text(json.dumps(base));transport=HostTransport(str(file),folder)
            file.write_text(json.dumps({**base,'token':'b'*64}))
            with self.assertRaisesRegex(ValueError,'verändert'):transport('models',{})
