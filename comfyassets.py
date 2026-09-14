"""Offline stock-node graphics adapter. Queue acceptance never means asset success."""
import json, pathlib, re, struct, time, urllib.request, urllib.parse, urllib.error, uuid, zlib

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("ComfyUI-Weiterleitung abgelehnt.")
from safety import safe_path, atomic_bytes, canonical, digest

def png_size(data):
    if len(data)>16000000 or data[:8]!=b'\x89PNG\r\n\x1a\n': raise ValueError('Ungültiges PNG.')
    offset=8; size=None; ended=False; compressed=bytearray(); channels=None
    while offset+12<=len(data):
        n=struct.unpack('>I',data[offset:offset+4])[0]; kind=data[offset+4:offset+8]
        end=offset+8+n
        if end+4>len(data) or zlib.crc32(data[offset+4:end])&0xffffffff!=struct.unpack('>I',data[end:end+4])[0]: raise ValueError('PNG-Prüfsumme ungültig.')
        if kind==b'IHDR':
            if size is not None or offset!=8 or n!=13: raise ValueError('Ungültiger PNG-Kopf.')
            size=struct.unpack('>II',data[offset+8:offset+16])
            depth,color,compression,filtering,interlace=struct.unpack('>BBBBB',data[offset+16:offset+21])
            if depth!=8 or color not in (2,6) or (compression,filtering,interlace)!=(0,0,0): raise ValueError('Nur nicht verschachtelte 8-Bit RGB/RGBA-PNGs.')
            channels=3 if color==2 else 4
        if kind==b'IDAT': compressed.extend(data[offset+8:end])
        if kind==b'IEND': ended=True;break
        offset=end+4
    if not ended or not size or not all(64<=x<=2048 for x in size): raise ValueError('PNG unvollständig oder Größe ungültig.')
    stride=1+size[0]*channels; expected=stride*size[1]
    decoder=zlib.decompressobj()
    try: pixels=decoder.decompress(bytes(compressed),expected+1)
    except zlib.error as exc: raise ValueError("PNG-Bilddaten ungültig.") from exc
    if not decoder.eof or decoder.unused_data or len(pixels)!=expected or any(pixels[i]>4 for i in range(0,expected,stride)): raise ValueError("PNG-Bilddaten unvollständig.")
    return size

class ComfyAssets:
    def __init__(self, workspace, state, endpoint='http://127.0.0.1:8189'):
        u=urllib.parse.urlsplit(endpoint)
        if u.scheme!='http' or u.hostname not in ('127.0.0.1','localhost','::1') or u.username or u.password or u.path not in ('','/') or u.query or u.fragment or any(x.isspace() for x in endpoint): raise ValueError('Lokale ComfyUI-Adresse erforderlich.')
        if u.port is not None and not 1<=u.port<=65535: raise ValueError('Ungültiger Port.')
        self.endpoint=endpoint.rstrip('/');self.workspace=pathlib.Path(workspace).resolve();self.state=pathlib.Path(state)/'assets'
    def request(self,path,body=None,raw=False):
        data=canonical(body) if body is not None else None
        req=urllib.request.Request(self.endpoint+path,data=data,headers={'Content-Type':'application/json'})
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
        with opener.open(req,timeout=20) as response:
            value=response.read(16000001)
        if len(value)>16000000: raise ValueError('ComfyUI-Antwort zu groß.')
        return value if raw else json.loads(value)
    def models(self):
        info=self.request('/object_info/CheckpointLoaderSimple')
        return info['CheckpointLoaderSimple']['input']['required']['ckpt_name'][0]
    def submit(self,prompt,target,checkpoint,seed=42,width=1024,height=576):
        if not isinstance(prompt,str) or not prompt.strip() or len(prompt)>3000: raise ValueError('Begrenzte Grafikbeschreibung erforderlich.')
        if type(seed) is not int or not 0<=seed<2**53: raise ValueError('Ungültiger Seed.')
        if any(type(x) is not int or not 256<=x<=1024 or x%64 for x in (width,height)): raise ValueError('Bildgröße 256–1024 in 64er Schritten.')
        path=safe_path(self.workspace,target)
        if path.suffix.lower()!='.png' or path.exists(): raise ValueError('Neuen PNG-Projektpfad verwenden; vorhandene Dateien bleiben erhalten.')
        if checkpoint not in self.models(): raise ValueError('Checkpoint nicht lokal verfügbar.')
        tag=uuid.uuid4().hex
        graph={
         '1':{'class_type':'CheckpointLoaderSimple','inputs':{'ckpt_name':checkpoint}},
         '2':{'class_type':'CLIPTextEncode','inputs':{'clip':['1',1],'text':prompt}},
         '3':{'class_type':'CLIPTextEncode','inputs':{'clip':['1',1],'text':'text, watermark, logo, blurry, distorted'}},
         '4':{'class_type':'EmptyLatentImage','inputs':{'width':width,'height':height,'batch_size':1}},
         '5':{'class_type':'KSampler','inputs':{'model':['1',0],'positive':['2',0],'negative':['3',0],'latent_image':['4',0],'seed':seed,'steps':20,'cfg':7,'sampler_name':'euler','scheduler':'normal','denoise':1}},
         '6':{'class_type':'VAEDecode','inputs':{'samples':['5',0],'vae':['1',2]}},
         '7':{'class_type':'SaveImage','inputs':{'images':['6',0],'filename_prefix':'CodeStudio/'+tag}}}
        receipt={'schema':'codestudio.asset.v1','id':tag,'workspace':str(self.workspace),'endpoint':self.endpoint,'target':target,'checkpoint':checkpoint,'prompt':prompt,'seed':seed,'dimensions':[width,height],'graph_sha256':digest(canonical(graph)),'status':'submitting'}
        atomic_bytes(self.state/(tag+'.json'),canonical(receipt))
        # Never retry this POST automatically after an uncertain response.
        result=self.request('/prompt',{'prompt':graph,'client_id':tag})
        if result.get('node_errors') or not isinstance(result.get('prompt_id'),str): raise RuntimeError('ComfyUI hat den Workflow nicht angenommen.')
        receipt.update(prompt_id=result['prompt_id'],status='queued')
        atomic_bytes(self.state/(tag+'.json'),canonical(receipt));return receipt
    def collect(self,tag):
        if not isinstance(tag,str) or not re.fullmatch('[a-f0-9]{32}',tag): raise ValueError('Ungültige Grafik-ID.')
        receipt=json.loads((self.state/(tag+'.json')).read_text(encoding='utf8'))
        if receipt['workspace']!=str(self.workspace) or receipt['endpoint']!=self.endpoint: raise ValueError('Grafikauftrag gehört zu anderem Projekt/Dienst.')
        path=safe_path(self.workspace,receipt['target'])
        if receipt['status']=='succeeded':
            if not path.is_file() or digest(path.read_bytes())!=receipt['sha256']: raise ValueError('Grafik seit Import verändert.')
            return receipt
        if not receipt.get('prompt_id'): raise RuntimeError('Übermittlung unklar; keinen neuen Auftrag automatisch starten.')
        history=self.request('/history/'+urllib.parse.quote(receipt['prompt_id'],safe='')).get(receipt['prompt_id'])
        if not history:return receipt
        if history.get('status',{}).get('status_str')=='error':
            receipt['status']='failed';atomic_bytes(self.state/(tag+'.json'),canonical(receipt));return receipt
        images=history.get('outputs',{}).get('7',{}).get('images',[])
        if not history.get('status',{}).get('completed') or len(images)!=1:return receipt
        img=images[0]
        data=self.request('/view?'+urllib.parse.urlencode({k:img.get(k,'') for k in ('filename','subfolder','type')}),raw=True)
        size=png_size(data)
        if list(size)!=receipt['dimensions']: raise ValueError('Bildgröße weicht vom Auftrag ab.')
        if path.exists(): raise ValueError('Ziel inzwischen vorhanden; kein Überschreiben.')
        path.parent.mkdir(parents=True,exist_ok=True)
        path=safe_path(self.workspace,receipt['target'])
        with path.open('xb') as f:f.write(data)
        if digest(path.read_bytes())!=digest(data): raise RuntimeError('Grafikimport nicht verifiziert.')
        receipt.update(status='succeeded',sha256=digest(data),bytes=len(data),finished_at=time.time())
        atomic_bytes(self.state/(tag+'.json'),canonical(receipt));return receipt
