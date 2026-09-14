import pathlib,tempfile,unittest,struct,zlib,json
from unittest.mock import patch
from comfyassets import ComfyAssets,png_size

def png():
 def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
 return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',1024,576,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b'\0'*(576*(1+1024*3))))+chunk(b'IEND',b'')
class AssetTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.tmp.name);self.api=ComfyAssets(self.root,self.root/'state')
 def tearDown(self):self.tmp.cleanup()
 def submit(self):
  with patch.object(self.api,'models',return_value=['local']),patch.object(self.api,'request',return_value={'prompt_id':'p'}):return self.api.submit('jungle','assets/a.png','local')
 def test_queue_is_not_success_then_verified_png_import_and_replay(self):
  job=self.submit();self.assertEqual(job['status'],'queued');self.assertFalse((self.root/'assets/a.png').exists())
  with patch.object(self.api,'request',return_value={}):self.assertEqual(self.api.collect(job['id'])['status'],'queued')
  h={'p':{'status':{'completed':True,'status_str':'success'},'outputs':{'7':{'images':[{'filename':'a.png','type':'output'}]}}}}
  with patch.object(self.api,'request',side_effect=[h,png()]):result=self.api.collect(job['id'])
  self.assertEqual(result['status'],'succeeded');self.assertEqual(png_size((self.root/'assets/a.png').read_bytes()),(1024,576))
  with patch.object(self.api,'request') as request:self.assertEqual(self.api.collect(job['id']),result);request.assert_not_called()
  (self.root/'assets/a.png').write_bytes(b'user change')
  with self.assertRaises(ValueError):self.api.collect(job['id'])
 def test_no_external_endpoint_or_existing_file_overwrite(self):
  for url in ['http://example.com','http://localhost@evil.com','http://127.0.0.1/x']:
   with self.assertRaises(ValueError):ComfyAssets(self.root,self.root,url)
  (self.root/'a.png').write_bytes(b'user')
  with self.assertRaises(ValueError):self.api.submit('x','a.png','local')
  with self.assertRaises(ValueError):self.api.submit('x','../a.png','local')
 def test_corrupt_image_is_rejected(self):
  with self.assertRaises(ValueError):png_size(png()[:-5])
  with self.assertRaises(ValueError):png_size(png()[:40]+b'x'+png()[41:])
 def test_failed_generation_is_terminal_without_asset(self):
  job=self.submit()
  with patch.object(self.api,'request',return_value={'p':{'status':{'status_str':'error'}}}):result=self.api.collect(job['id'])
  self.assertEqual(result['status'],'failed');self.assertFalse((self.root/'assets/a.png').exists())

 def test_complete_chunks_without_image_data_rejected(self):
  data=png()
  with self.assertRaises(ValueError):png_size(data[:33]+data[-12:])
 def test_redirect_rejected(self):
  from comfyassets import NoRedirect
  with self.assertRaises(ValueError):NoRedirect().redirect_request(None,None,302,'redirect',{},'https://example.com')
