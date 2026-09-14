'use strict';
const vscode=require('vscode'),fs=require('fs'),path=require('path'),crypto=require('crypto'),{spawn}=require('child_process');

class Engine {
 constructor(extension,folder,state,output){
  this.pending=new Map();this.sequence=0;this.buffer='';
  const packed=path.join(extension.extensionPath,'engine','CodeStudio.exe');
  const cfg=vscode.workspace.getConfiguration('codestudio',folder.uri);
  const python=cfg.get('pythonPath')||process.env.CODESTUDIO_PYTHON;
  if(!fs.existsSync(packed)&&(!python||!path.isAbsolute(python)||!fs.existsSync(python)))throw Error('Python-Pfad in CodeStudio einstellen oder das vollständige Windows-Paket verwenden.');
  const args=fs.existsSync(packed)?['--serve']:['-u',path.join(extension.extensionPath,'..','service.py')];
  args.push('--workspace',folder.uri.fsPath,'--state-dir',state);
  const binding=cfg.get('hostBinding');
  if(binding){if(!path.isAbsolute(binding))throw Error('Absoluter TobyKi-Hostbindungspfad erforderlich.');args.push('--host-binding',binding);}
  this.child=spawn(fs.existsSync(packed)?packed:python,args,{cwd:folder.uri.fsPath,shell:false,windowsHide:true,env:{...process.env,PYTHONIOENCODING:'utf-8',PYTHONDONTWRITEBYTECODE:'1'}});
  this.child.stdout.setEncoding('utf8');
  this.child.stdout.on('data',chunk=>{
   this.buffer+=chunk;
   if(Buffer.byteLength(this.buffer)>8*1024*1024){this.fail(Error('CodeStudio-Antwort zu groß.'));this.child.kill();return;}
   let end;while((end=this.buffer.indexOf('\n'))>=0){const line=this.buffer.slice(0,end);this.buffer=this.buffer.slice(end+1);try{const r=JSON.parse(line);if(r.event==='log'){output.appendLine(r.text);continue;}const p=this.pending.get(r.id);if(p){this.pending.delete(r.id);r.ok?p.resolve(r.result):p.reject(Error(r.error));}}catch(e){this.fail(e);}}
  });
  this.child.stderr.resume();
  this.child.on('error',e=>this.fail(e));this.child.on('close',()=>this.fail(Error('CodeStudio-Kern beendet. Eventuelle Änderungen anhand der Laufbelege prüfen.')));
  this.child.stdin.on('error',e=>this.fail(e));
 }
 request(command,data={}){if(this.closed)return Promise.reject(Error('CodeStudio-Kern ist nicht mehr verbunden.'));return new Promise((resolve,reject)=>{const id=++this.sequence;this.pending.set(id,{resolve,reject,command});this.child.stdin.write(JSON.stringify({id,command,...data})+'\n');});}
 fail(error){this.closed=true;for(const p of this.pending.values())p.reject(error);this.pending.clear();}
 dispose(){const applying=[...this.pending.values()].some(p=>p.command==='apply');this.child.stdin.end();if(!applying)this.child.kill();}
}

function activate(context){
 const output=vscode.window.createOutputChannel('CodeStudio');
 const changed=new vscode.EventEmitter(),documents=new Map();
 let engine=null,folder=null,proposal=null,busy=false,status='Projekt öffnen und Aufgabe beschreiben';
 const boundWorkspaces=new Map();
 const config=()=>vscode.workspace.getConfiguration('codestudio',folder?.uri);
 const settingsIdentity=()=>JSON.stringify({model:config().get('model'),context:config().get('contextTokens'),tests:config().get('testCommand',[]),hostBinding:config().get('hostBinding','')});
 const item=(label,command,arg,icon)=>{const i=new vscode.TreeItem(label);if(command)i.command={command,title:label,arguments:arg===undefined?[]:[arg]};if(icon)i.iconPath=new vscode.ThemeIcon(icon);return i;};
 const provider={onDidChangeTreeData:changed.event,getTreeItem:x=>x,getChildren:()=>[
  item(status,null,null,busy?'sync~spin':'info'),
  item(config().get('hostBinding')?'Modellsteuerung: TobyKi-Hostsitzung':'Modellsteuerung: eigenständiges Ollama',null,null,'plug'),
  item('Aufgabe planen und Diff erzeugen','codestudio.generate',undefined,'edit'),
  item('Modell: '+config().get('model'),'codestudio.model',undefined,'server'),
  item('Kontext: '+config().get('contextTokens')+' Tokens','codestudio.context',undefined,'settings'),
  item(config().get('testCommand',[]).length?'Projekttests eingestellt':'Projekttests einstellen','codestudio.tests',undefined,'beaker'),
  ...(proposal?[...proposal.changes.map(c=>item(c.path,'codestudio.openDiff',c.path,'diff')),item('Geprüften Diff anwenden','codestudio.apply',undefined,'check'),item('Vorschlag verwerfen','codestudio.reject',undefined,'close')]:[]),
  item('Ablauf und Belege','codestudio.output',undefined,'output')
 ]};
 context.subscriptions.push(output,changed,vscode.window.registerTreeDataProvider('codestudio.tasks',provider),
  vscode.workspace.registerTextDocumentContentProvider('codestudio-diff',{provideTextDocumentContent:uri=>documents.get(uri.toString())||''}));
 async function ensure(){
  if(!vscode.workspace.isTrusted)throw Error('CodeStudio benötigt einen vertrauenswürdigen Projektordner.');
  const folders=vscode.workspace.workspaceFolders||[];
  if(!folders.length)throw Error('Zuerst einen lokalen Projektordner in VS Code öffnen.');
  const selected=folders.length===1?folders[0]:await vscode.window.showWorkspaceFolderPick({placeHolder:'CodeStudio-Projekt wählen'});
  if(!selected)return null;if(selected.uri.scheme!=='file')throw Error('CodeStudio unterstützt hier lokale Projektordner.');
  const binding=vscode.workspace.getConfiguration('codestudio',selected.uri).get('hostBinding',''),key=selected.uri.toString();
  if(boundWorkspaces.has(key)&&boundWorkspaces.get(key)!==binding)throw Error('TobyKi-Hostbindung geändert. Projekt erneut aus TobyKi öffnen; kein automatischer Standalone-Wechsel.');
  if(binding)boundWorkspaces.set(key,binding);
  if(!engine||engine.closed||folder?.uri.toString()!==selected.uri.toString()){
   if(engine)engine.dispose();proposal=null;folder=selected;
   const state=path.join(context.globalStorageUri.fsPath,crypto.createHash('sha256').update(folder.uri.toString()).digest('hex').slice(0,20));
   engine=new Engine(context,folder,state,output);
  }
  return engine;
 }
 function dirty(){return vscode.workspace.textDocuments.some(d=>d.isDirty&&vscode.workspace.getWorkspaceFolder(d.uri)?.uri.toString()===folder?.uri.toString());}
 async function guard(fn){if(busy){vscode.window.showInformationMessage('CodeStudio arbeitet noch.');return;}try{return await fn();}catch(e){status='Vorgang fehlgeschlagen';output.appendLine(e.message);output.show(true);vscode.window.showErrorMessage(e.message);changed.fire();throw e;}}
 async function update(key,value){if(value===undefined)return;await config().update(key,value,vscode.ConfigurationTarget.WorkspaceFolder);proposal=null;if(engine&&!engine.closed)await engine.request('reject');changed.fire();}
 async function showDiff(rel){if(!proposal)return;const change=proposal.changes.find(c=>c.path===rel);if(!change)return;const base=vscode.Uri.parse('codestudio-diff:/'+proposal.proposal_id+'/'+encodeURIComponent(rel));const before=base.with({query:'before'}),after=base.with({query:'after'});documents.set(before.toString(),change.before||'');documents.set(after.toString(),change.after||'');await vscode.commands.executeCommand('vscode.diff',before,after,rel+' · CodeStudio-Vorschlag',{preview:true});}
 const commands={
  'codestudio.output':()=>output.show(),
  'codestudio.openDiff':showDiff,
  'codestudio.model':()=>guard(async()=>{const client=await ensure();if(!client)return;const r=await client.request('models');const model=await vscode.window.showQuickPick(r.models,{placeHolder:'Installiertes Ollama-Modell'});await update('model',model);}),
  'codestudio.context':()=>guard(async()=>{if(!await ensure())return;if(config().get('hostBinding')){vscode.window.showInformationMessage('Kontext und Ressourcen werden durch die gebundene TobyKi-Hostkonfiguration bestimmt. Nach Änderungen erneut aus TobyKi öffnen.');return;}const value=await vscode.window.showQuickPick(['8192','16384','32768','65536','131072'],{placeHolder:'Kontextfenster – größere Werte benötigen mehr Speicher'});if(value)await update('contextTokens',Number(value));}),
  'codestudio.tests':()=>guard(async()=>{if(!await ensure())return;const value=await vscode.window.showInputBox({prompt:'Testprogramm und Argumente als JSON-Liste; [] bedeutet ungeprüft',value:JSON.stringify(config().get('testCommand',[])),ignoreFocusOut:true});if(value===undefined)return;const args=JSON.parse(value);if(!Array.isArray(args)||!args.every(x=>typeof x==='string'&&x))throw Error('Argumentliste erforderlich.');await update('testCommand',args);}),
  'codestudio.generate':options=>guard(async()=>{
   const client=await ensure();if(!client)return;
   if(dirty())throw Error('Offene Änderungen zuerst speichern, damit CodeStudio dieselben Dateien prüft wie der Editor.');
   const task=options?.task||await vscode.window.showInputBox({prompt:'Was soll CodeStudio ändern? Aufgabe und Akzeptanzkriterien.',ignoreFocusOut:true});if(!task)return;
   busy=true;proposal=null;status='Scout → Planer → Coder → Reviewer';changed.fire();output.show(true);
   try{
    const settings=settingsIdentity();
    await client.request('configure',{context_tokens:config().get('contextTokens'),test_argv:config().get('testCommand',[])});
    proposal=await client.request('analyze',{task,model:options?.model||config().get('model')});proposal.settings=settings;
    if(settings!==settingsIdentity()){proposal=null;await client.request('reject');throw Error('Einstellungen während der Planung geändert. Bitte neu analysieren.');}
    status='Vorschlag prüfen · '+proposal.changes.length+' Datei(en)';
    output.appendLine((proposal.plan.plan||[]).join('\n'));output.appendLine(proposal.diff);
    if(proposal.changes.length)await showDiff(proposal.changes[0].path);
    return {proposal_id:proposal.proposal_id,diff:proposal.diff,workspace:proposal.workspace};
   }finally{busy=false;changed.fire();}
  }),
  'codestudio.reject':()=>guard(async()=>{if(engine&&!engine.closed)await engine.request('reject');proposal=null;status='Vorschlag verworfen';changed.fire();}),
  'codestudio.apply':()=>guard(async()=>{
   if(!proposal||!engine)throw Error('Zuerst einen aktuellen Diff erzeugen.');
   const assertCurrent=()=>{if(!vscode.workspace.isTrusted||!vscode.workspace.workspaceFolders?.some(f=>f.uri.toString()===folder.uri.toString())||proposal.settings!==settingsIdentity())throw Error('Projekt oder Einstellungen geändert. Bitte neu analysieren.');if(dirty())throw Error('Ungespeicherte Editoränderungen: zuerst speichern und neu analysieren.');};
   assertCurrent();
   if(dirty())throw Error('Ungespeicherte Editoränderungen: zuerst speichern und neu analysieren.');
   const noTests=!config().get('testCommand',[]).length;
   const approval=await vscode.window.showWarningMessage(proposal.changes.length+' Datei(en) in '+proposal.workspace+' ändern?'+(noTests?' Keine Projekttests eingestellt.':' Anschließend laufen die eingestellten Projekttests.'),{modal:true},'Geprüften Diff anwenden');
   if(approval!=='Geprüften Diff anwenden')return;
   assertCurrent();
   busy=true;status='Anwenden und prüfen';changed.fire();
   const id=proposal.proposal_id;proposal=null;
   try{const result=await engine.request('apply',{proposal_id:id,approved:true});const labels={'applied':'Angewendet · Projekttests bestanden','applied-untested':'Angewendet · ohne Projekttest','rolled-back':'Test fehlgeschlagen · zurückgerollt'};status=labels[result.receipt.status]||result.receipt.status;output.appendLine(status+'\n'+(result.receipt.test?.output||'')+'\nBeleg: '+result.receipt_path);output.show(true);vscode.window.showInformationMessage(status);return result;}finally{busy=false;changed.fire();}
  })
 };
 for(const [name,handler]of Object.entries(commands))context.subscriptions.push(vscode.commands.registerCommand(name,handler));
 context.subscriptions.push(vscode.workspace.onDidChangeConfiguration(event=>{if(event.affectsConfiguration('codestudio')){proposal=null;if(event.affectsConfiguration('codestudio.hostBinding')){engine?.dispose();engine=null;}else if(engine&&!busy&&!engine.closed)engine.request('reject').catch(()=>{});changed.fire();}}),{dispose:()=>engine?.dispose()});
 if(!context.globalState.get('welcomeShown')){context.globalState.update('welcomeShown',true);vscode.commands.executeCommand('workbench.view.extension.codestudio');}
 return {status:()=>({status,busy,proposal_id:proposal?.proposal_id,workspace:folder?.uri.fsPath})};
}
module.exports={activate};
