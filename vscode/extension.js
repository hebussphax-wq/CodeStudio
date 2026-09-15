'use strict';
const vscode=require('vscode'),fs=require('fs'),path=require('path'),crypto=require('crypto'),{spawn}=require('child_process');
const editorContext=require('./editor_context');

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
   if(Buffer.byteLength(this.buffer)>8*1024*1024){this.dispose();this.fail(Error('CodeStudio-Antwort zu groß. Auftrag wird gestoppt; Beleg prüfen.'));return;}
   let end;while((end=this.buffer.indexOf('\n'))>=0){const line=this.buffer.slice(0,end);this.buffer=this.buffer.slice(end+1);try{const r=JSON.parse(line);if(r.event==='log'){output.appendLine(r.text);continue;}if(r.event==='autonomous'){this.onEvent?.(r);continue;}const p=this.pending.get(r.id);if(p){this.pending.delete(r.id);r.ok?p.resolve(r.result):p.reject(Error(r.error));}}catch(e){this.fail(e);}}
  });
  this.child.stderr.resume();
  this.child.on('error',e=>this.fail(e));this.child.on('close',()=>this.fail(Error('CodeStudio-Kern beendet. Eventuelle Änderungen anhand der Laufbelege prüfen.')));
  this.child.stdin.on('error',e=>this.fail(e));
 }
 request(command,data={}){if(command==='autonomous')this.autonomousId=data.run_id;if(this.closed)return Promise.reject(Error('CodeStudio-Kern ist nicht mehr verbunden.'));return new Promise((resolve,reject)=>{const id=++this.sequence;this.pending.set(id,{resolve,reject,command});this.child.stdin.write(JSON.stringify({id,command,...data})+'\n');});}
 fail(error){this.closed=true;for(const p of this.pending.values())p.reject(error);this.pending.clear();}
 dispose(){const working=[...this.pending.values()].some(p=>['apply','autonomous','test'].includes(p.command));if(this.autonomousId&&!this.closed)this.child.stdin.write(JSON.stringify({command:'cancel',run_id:this.autonomousId})+'\n');this.child.stdin.end();if(!working)this.child.kill();}
}

function activate(context){
 const output=vscode.window.createOutputChannel('CodeStudio');
 const changed=new vscode.EventEmitter(),documents=new Map();
 let engine=null,folder=null,proposal=null,busy=false,activeRun=null,status='Projekt öffnen und Aufgabe beschreiben';
 const boundWorkspaces=new Map();
 const config=()=>vscode.workspace.getConfiguration('codestudio',folder?.uri);
 const testProfiles=()=>config().get('testProfiles',[]);
 const hasTests=()=>testProfiles().length>0||config().get('testCommand',[]).length>0;
 const configureRequest=()=>({context_tokens:config().get('contextTokens'),...(testProfiles().length?{test_profiles:testProfiles()}:{test_argv:config().get('testCommand',[])}),...(!config().get('hostBinding')?{output_tokens:config().get('outputTokens',4096),ollama_url:config().get('ollamaUrl','http://127.0.0.1:11434'),fallback_models:config().get('fallbackModels',[])}:{})});
 const settingsIdentity=()=>JSON.stringify({endpoint:config().get('ollamaUrl'),model:config().get('model'),fallbacks:config().get('fallbackModels',[]),context:config().get('contextTokens'),output:config().get('outputTokens',4096),profiles:testProfiles(),calls:config().get('autonomousModelCalls',80),tests:config().get('testCommand',[]),hostBinding:config().get('hostBinding',''),steps:config().get('autonomousSteps',6),repairs:config().get('autonomousRepairs',3),minutes:config().get('autonomousMinutes',30)});
 const item=(label,command,arg,icon)=>{const i=new vscode.TreeItem(label);if(command)i.command={command,title:label,arguments:arg===undefined?[]:[arg]};if(icon)i.iconPath=new vscode.ThemeIcon(icon);return i;};
 const provider={onDidChangeTreeData:changed.event,getTreeItem:x=>x,getChildren:()=>[
  item(status,null,null,busy?'sync~spin':'info'),
  item(config().get('hostBinding')?'Modellsteuerung: TobyKi-Hostsitzung':'Modellsteuerung: eigenständiges Ollama',null,null,'plug'),
  item('Autonom entwickeln','codestudio.autonomous',undefined,'rocket'),
   item('Ersatzmodelle: '+config().get('fallbackModels',[]).length,'codestudio.fallbackModels',undefined,'server'),
  item('Testprofile laden','codestudio.testProfiles',undefined,'beaker'),
  item('Laufgrenzen einstellen','codestudio.limits',undefined,'settings'),
  item('Modul-Workflow laden','codestudio.workflow',undefined,'list-tree'),
  ...(!config().get('hostBinding')?[item('Grafik lokal mit ComfyUI erzeugen','codestudio.graphics',undefined,'file-media')]:[]),
  ...(activeRun?[item('Autonomen Auftrag stoppen','codestudio.stop',undefined,'debug-stop')]:[]),
  item('Aufgabe planen und Diff erzeugen','codestudio.generate',undefined,'edit'),
  item('Projekt und Editorkontext prüfen','codestudio.inspect',undefined,'inspect'),
  item('Projektdatei öffnen','codestudio.projectFile',undefined,'go-to-file'),
  item('Probleme in VS Code anzeigen','codestudio.problems',undefined,'error'),
  item('Projekttests jetzt ausführen','codestudio.runTests',undefined,'testing-run-icon'),
  ...(!config().get('hostBinding')?[item('Ollama: '+config().get('ollamaUrl','http://127.0.0.1:11434'),'codestudio.provider',undefined,'plug')]:[]),
  item('Modell: '+config().get('model'),'codestudio.model',undefined,'server'),
  item('Kontext: '+config().get('contextTokens')+' Tokens','codestudio.context',undefined,'settings'),
  item(hasTests()?'Projekttests eingestellt':'Projekttests einstellen','codestudio.tests',undefined,'beaker'),
  ...(proposal?[...proposal.changes.map(c=>item(c.path,'codestudio.openDiff',c.path,'diff')),item('Geprüften Diff anwenden','codestudio.apply',undefined,'check'),item('Vorschlag verwerfen','codestudio.reject',undefined,'close')]:[]),
  item('Gestoppten Auftrag mit anderem Ansatz fortsetzen','codestudio.resume',undefined,'debug-continue'),
  item('Autonome Aufträge und QC-Belege','codestudio.history',undefined,'history'),
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
   engine.onEvent=r=>{if(activeRun&&r.status==='running'){status='Autonom: '+r.completed_steps+' Schritte bearbeitet';changed.fire();}};
  }
  return engine;
 }
 function dirty(){return vscode.workspace.textDocuments.some(d=>d.isDirty&&vscode.workspace.getWorkspaceFolder(d.uri)?.uri.toString()===folder?.uri.toString());}
 async function guard(fn){if(busy){vscode.window.showInformationMessage('CodeStudio arbeitet noch.');return;}try{return await fn();}catch(e){status='Vorgang fehlgeschlagen';output.appendLine(e.message);output.show(true);vscode.window.showErrorMessage(e.message);changed.fire();throw e;}}
 async function update(key,value){if(value===undefined)return;await config().update(key,value,vscode.ConfigurationTarget.WorkspaceFolder);proposal=null;if(engine&&!engine.closed)await engine.request('reject');changed.fire();}
 async function showDiff(rel){if(!proposal)return;const change=proposal.changes.find(c=>c.path===rel);if(!change)return;const base=vscode.Uri.parse('codestudio-diff:/'+proposal.proposal_id+'/'+encodeURIComponent(rel));const before=base.with({query:'before'}),after=base.with({query:'after'});documents.set(before.toString(),change.before||'');documents.set(after.toString(),change.after||'');await vscode.commands.executeCommand('vscode.diff',before,after,rel+' · CodeStudio-Vorschlag',{preview:true});}
 const commands={
  'codestudio.inspect':()=>guard(async()=>{if(!await ensure())return;const snapshot=editorContext.collect(vscode,folder);const uri=vscode.Uri.parse('codestudio-diff:/editor-context/'+Date.now()+'.json');documents.set(uri.toString(),JSON.stringify(snapshot,null,2));await vscode.window.showTextDocument(await vscode.workspace.openTextDocument(uri));return snapshot;}),
  'codestudio.problems':()=>vscode.commands.executeCommand('workbench.actions.view.problems'),
  'codestudio.projectFile':()=>guard(async()=>{if(!await ensure())return;const files=await vscode.workspace.findFiles(new vscode.RelativePattern(folder,'**/*'), '**/{.git,node_modules,.venv,dist,build,target}/**',300);const items=files.map(uri=>({label:editorContext.relativeFile(folder,uri),uri})).filter(x=>x.label);const selected=await vscode.window.showQuickPick(items,{placeHolder:'Datei im gewählten CodeStudio-Projekt öffnen'});if(selected)await vscode.window.showTextDocument(await vscode.workspace.openTextDocument(selected.uri));}),
  'codestudio.runTests':()=>guard(async()=>{const client=await ensure();if(!client)return;if(dirty())throw Error('Offene Änderungen zuerst speichern.');if(!hasTests())throw Error('Zuerst Projekttests einstellen.');busy=true;changed.fire();try{await client.request('configure',configureRequest());const result=await client.request('test');output.appendLine(result.output||'');output.show(true);status=result.returncode===0?'Projekttests bestanden':'Projekttests fehlgeschlagen';return result;}finally{busy=false;changed.fire();}}),
  'codestudio.history':()=>guard(async()=>{const client=await ensure();if(!client)return;const result=await client.request('history');const chosen=await vscode.window.showQuickPick(result.runs.map(r=>({label:r.status+' · '+r.task,description:r.run_id,receipt:r.receipt_path})),{placeHolder:'Autonomen Auftrag und QC-Beleg öffnen'});if(chosen)await vscode.window.showTextDocument(await vscode.workspace.openTextDocument(vscode.Uri.file(chosen.receipt)));}),
  'codestudio.output':()=>output.show(),
  'codestudio.openDiff':showDiff,
  'codestudio.provider':()=>guard(async()=>{const client=await ensure();if(!client)return;if(config().get('hostBinding'))throw Error('Modelladresse wird durch die Hostbindung bestimmt.');const value=await vscode.window.showInputBox({prompt:'Adresse des lokalen Ollama-Dienstes',value:config().get('ollamaUrl','http://127.0.0.1:11434'),ignoreFocusOut:true});if(value===undefined)return;await client.request('configure',{...configureRequest(),ollama_url:value});await update('ollamaUrl',value);const result=await client.request('models');status=result.models.length+' Modelle am gewählten Dienst';changed.fire();}),
  'codestudio.model':()=>guard(async()=>{const client=await ensure();if(!client)return;await client.request('configure',configureRequest());const r=await client.request('models');const model=await vscode.window.showQuickPick(r.models,{placeHolder:'Installiertes Ollama-Modell'});await update('model',model);}),
  'codestudio.fallbackModels':()=>guard(async()=>{const client=await ensure();if(!client)return;if(config().get('hostBinding'))throw Error('Ersatzmodelle werden durch den Host verwaltet.');await client.request('configure',configureRequest());const r=await client.request('models');const selected=await vscode.window.showQuickPick(r.models.filter(m=>m!==config().get('model')).map(m=>({label:m,picked:config().get('fallbackModels',[]).includes(m)})),{canPickMany:true,placeHolder:'Bis zu 3 lokale Ersatzmodelle; Reihenfolge wie angezeigt. Wechsel nach zwei erfolglosen Modulversuchen.'});if(selected===undefined)return;if(selected.length>3)throw Error('Höchstens drei Ersatzmodelle wählen.');await update('fallbackModels',selected.map(m=>m.label));}),
  'codestudio.context':()=>guard(async()=>{if(!await ensure())return;if(config().get('hostBinding')){vscode.window.showInformationMessage('Kontext und Ressourcen werden durch die gebundene TobyKi-Hostkonfiguration bestimmt. Nach Änderungen erneut aus TobyKi öffnen.');return;}const value=await vscode.window.showQuickPick(['8192','16384','32768','65536','131072'],{placeHolder:'Kontextfenster – größere Werte benötigen mehr Speicher'});if(value)await update('contextTokens',Number(value));}),
  'codestudio.testProfiles':()=>guard(async()=>{
   const client=await ensure();if(!client)return;
   const picked=await vscode.window.showOpenDialog({canSelectMany:false,filters:{'Testprofile':['json']},openLabel:'Testprofile wählen'});if(!picked?.length)return;
   if(picked[0].scheme!=='file')throw Error('Lokale JSON-Datei erforderlich.');
   const raw=fs.readFileSync(picked[0].fsPath,'utf8');if(Buffer.byteLength(raw)>120000)throw Error('Testprofil-Datei zu groß.');
   const data=JSON.parse(raw),profiles=Array.isArray(data)?data:data.test_profiles;
   if(!Array.isArray(profiles)||!profiles.length)throw Error('Nichtleere Liste test_profiles erforderlich.');
   await vscode.window.showTextDocument(await vscode.workspace.openTextDocument(picked[0]));
   await client.request('configure',{...configureRequest(),test_profiles:profiles});
   await update('testProfiles',profiles);status=profiles.length+' Testprofile eingestellt';changed.fire();
  }),
  'codestudio.limits':()=>guard(async()=>{
   if(!await ensure())return;
   await vscode.commands.executeCommand('workbench.action.openSettings','@ext:tobyki.codestudio');
  }),
  'codestudio.tests':()=>guard(async()=>{if(!await ensure())return;const value=await vscode.window.showInputBox({prompt:'Testprogramm und Argumente als JSON-Liste; [] bedeutet ungeprüft',value:JSON.stringify(config().get('testCommand',[])),ignoreFocusOut:true});if(value===undefined)return;const args=JSON.parse(value);if(!Array.isArray(args)||!args.every(x=>typeof x==='string'&&x))throw Error('Argumentliste erforderlich.');await update('testCommand',args);await update('testProfiles',[]);}),
  'codestudio.generate':options=>guard(async()=>{
   const client=await ensure();if(!client)return;
   if(dirty())throw Error('Offene Änderungen zuerst speichern, damit CodeStudio dieselben Dateien prüft wie der Editor.');
   const task=options?.task||await vscode.window.showInputBox({prompt:'Was soll CodeStudio ändern? Aufgabe und Akzeptanzkriterien.',ignoreFocusOut:true});if(!task)return;
   busy=true;proposal=null;status='Scout → Planer → Coder → Reviewer';changed.fire();output.show(true);
   try{
    const settings=settingsIdentity();
    await client.request('configure',configureRequest());
    proposal=await client.request('analyze',{task:editorContext.taskWithContext(task,editorContext.collect(vscode,folder)),model:options?.model||config().get('model')});proposal.settings=settings;
    if(settings!==settingsIdentity()){proposal=null;await client.request('reject');throw Error('Einstellungen während der Planung geändert. Bitte neu analysieren.');}
    status='Vorschlag prüfen · '+proposal.changes.length+' Datei(en)';
    output.appendLine((proposal.plan.plan||[]).join('\n'));output.appendLine(proposal.diff);
    if(proposal.changes.length)await showDiff(proposal.changes[0].path);
    return {proposal_id:proposal.proposal_id,diff:proposal.diff,workspace:proposal.workspace};
   }finally{busy=false;changed.fire();}
  }),
  'codestudio.stop':async()=>{if(engine&&activeRun){status='Stoppen und Änderungen zurückrollen';changed.fire();return engine.request('cancel',{run_id:activeRun});}},
  'codestudio.graphics':()=>guard(async()=>{
   const client=await ensure();if(!client)return;
   const graphicsWorkspace=folder.uri.toString(),graphicsSettings=settingsIdentity();
   const assertGraphicsCurrent=()=>{if(dirty()||!vscode.workspace.isTrusted||settingsIdentity()!==graphicsSettings||!vscode.workspace.workspaceFolders?.some(f=>f.uri.toString()===graphicsWorkspace))throw Error('Projekt oder Einstellungen geändert; Grafikauftrag bleibt im Beleg erhalten.');};
   const endpoint=await vscode.window.showInputBox({prompt:'Lokaler ComfyUI-Dienst',value:'http://127.0.0.1:8189'});if(!endpoint)return;
   const models=await client.request('graphics_models',{endpoint});
   const checkpoint=await vscode.window.showQuickPick(models.models,{placeHolder:'Vorhandenes Grafikmodell wählen'});if(!checkpoint)return;
   const prompt=await vscode.window.showInputBox({prompt:'Welche Grafik soll lokal entstehen?',ignoreFocusOut:true});if(!prompt)return;
   const target=await vscode.window.showInputBox({prompt:'Neuer PNG-Pfad im Projekt',value:'assets/background.png'});if(!target)return;
   if(await vscode.window.showWarningMessage('Grafik mit '+checkpoint+' lokal erzeugen und als '+target+' in '+folder.uri.fsPath+' speichern?',{modal:true},'Grafik erzeugen')!=='Grafik erzeugen')return;
   busy=true;status='ComfyUI erzeugt die Grafik';changed.fire();output.show(true);
   try{
    assertGraphicsCurrent();
    let job=await client.request('graphics_submit',{endpoint,prompt,target,checkpoint,approved:true});
    output.appendLine('Grafikauftrag: '+job.id);
    const until=Date.now()+300000;
    while(job.status==='queued'&&Date.now()<until){await new Promise(r=>setTimeout(r,1000));assertGraphicsCurrent();job=await client.request('graphics_collect',{endpoint,asset_id:job.id});}
    status=job.status==='succeeded'?'Grafik erzeugt und geprüft':job.status==='failed'?'Grafikerzeugung fehlgeschlagen':'Grafik noch in Bearbeitung · Beleg prüfen';
    output.appendLine(JSON.stringify(job));
    if(job.status==='succeeded')await vscode.commands.executeCommand('vscode.open',vscode.Uri.file(path.join(folder.uri.fsPath,target)));
    return job;
   }finally{busy=false;changed.fire();}
  }),
  'codestudio.workflow':async()=>{
   if(busy)return;
   const picked=await vscode.window.showOpenDialog({canSelectMany:false,filters:{'CodeStudio Workflow':['json']},openLabel:'Workflow prüfen'});
   if(!picked?.length)return;
   if(picked[0].scheme!=='file')throw Error('Lokale Workflow-Datei erforderlich.');
   const raw=fs.readFileSync(picked[0].fsPath,'utf8');if(Buffer.byteLength(raw)>120000)throw Error('Workflow zu groß.');
   const bundle=JSON.parse(raw);
   if(!bundle.task||!bundle.workflow||!Array.isArray(bundle.test_profiles))throw Error('Workflow benötigt Aufgabe, Module und Testprofile.');
   await vscode.window.showTextDocument(await vscode.workspace.openTextDocument(picked[0]));
   const yes=await vscode.window.showWarningMessage('Diesen geöffneten Workflow samt sichtbaren Testprogrammen für das gewählte Projekt verwenden?',{modal:true},'Workflow verwenden');
   if(yes==='Workflow verwenden')return commands['codestudio.autonomous']({task:bundle.task,workflow:bundle.workflow,test_profiles:bundle.test_profiles});
  },
  'codestudio.resume':()=>guard(async()=>{
   const client=await ensure();if(!client)return;
   await client.request('configure',configureRequest());
   const history=await client.request('history');
   const chosen=await vscode.window.showQuickPick(history.runs.filter(r=>r.resumable).map(r=>({label:r.status+' · '+r.task,description:r.run_id,run:r.run_id})),{placeHolder:'Gesicherten, zurückgerollten Auftrag wählen'});
   if(!chosen)return;
   const info=await client.request('resume_info',{run_id:chosen.run});
   const approach=await vscode.window.showInputBox({prompt:'Was wird konkret geändert? Fehlerursache, präzisere Reparatur oder anderes zuvor gewähltes Modell. Gleiche fehlerhafte Ergebnisse werden nicht erneut getestet.',ignoreFocusOut:true,validateInput:v=>v.trim().length<12?'Geänderten Ansatz konkret beschreiben.':undefined});
   if(!approach)return;
   return commands['codestudio.autonomous']({task:info.task,resume_from:chosen.run,changed_approach:approach});
  }),
  'codestudio.autonomous':options=>guard(async()=>{
   const client=await ensure();if(!client)return;
   if(dirty())throw Error('Offene Änderungen zuerst speichern.');
   if(!options?.test_profiles&&!hasTests())throw Error('Zuerst Projekttests einstellen. Autonomer Abschluss benötigt echte Tests.');
   const task=options?.task||await vscode.window.showInputBox({prompt:'Autonom entwickeln: Ziel und überprüfbare Akzeptanzkriterien',ignoreFocusOut:true});if(!task)return;
   const settings=settingsIdentity(),workspace=folder.uri.toString();
   const limits={steps:config().get('autonomousSteps',6),repairs:config().get('autonomousRepairs',3),seconds:config().get('autonomousMinutes',30)*60,model_calls:config().get('autonomousModelCalls',80)};
   const approval=await vscode.window.showWarningMessage('CodeStudio bearbeitet '+folder.uri.fsPath+' autonom: planen, Dateien ändern, Projekttests ausführen und Fehler reparieren. Bis '+limits.steps+' Schritte, '+limits.repairs+' Reparaturen, '+(limits.seconds/60)+' Minuten und '+limits.model_calls+' Modellaufrufe. Bei Abbruch/Fehlschlag werden die eigenen Änderungen zurückgerollt. Vorhandene Tests bleiben erhalten.',{modal:true},'Autonom entwickeln');
   if(approval!=='Autonom entwickeln')return;
   if(dirty()||settings!==settingsIdentity()||!vscode.workspace.isTrusted||!vscode.workspace.workspaceFolders?.some(f=>f.uri.toString()===workspace))throw Error('Projekt oder Einstellungen geändert. Auftrag neu starten.');
   busy=true;proposal=null;activeRun=crypto.randomBytes(16).toString('hex');status='Autonom: planen und abarbeiten';changed.fire();output.show(true);
   let stopSent=false;
   const watch=setInterval(()=>{if(!stopSent&&(dirty()||settings!==settingsIdentity()||!vscode.workspace.isTrusted||!vscode.workspace.workspaceFolders?.some(f=>f.uri.toString()===workspace))){stopSent=true;client.request('cancel',{run_id:activeRun}).catch(()=>{});}},250);
   try{
    await client.request('configure',{...configureRequest(),...(options?.test_profiles?{test_profiles:options.test_profiles}:{})});
    const result=await client.request('autonomous',{run_id:activeRun,approved:true,task:options?.resume_from?task:editorContext.taskWithContext(task,editorContext.collect(vscode,folder)),model:config().get('model'),limits,...(options?.resume_from?{resume_from:options.resume_from,changed_approach:options.changed_approach}:{}),...(options?.workflow?{workflow:options.workflow}:{})});
    const labels={succeeded:'Autonom abgeschlossen · Tests und QC bestanden',cancelled:'Abgebrochen · Änderungen zurückgerollt',timed_out:'Zeitbudget erreicht · zurückgerollt',budget_exhausted:'Budget erreicht · zurückgerollt',stalled:'Kein Fortschritt nach vier Versuchen · Auftrag gestoppt',failed:'Auftrag fehlgeschlagen · zurückgerollt',conflict:'Fremde Änderung erkannt · Auftrag gestoppt',rollback_conflict:'Konflikt · Sicherung prüfen',recovery_required:'Prozessende unklar · Wiederherstellung prüfen',blocked:'Auftrag blockiert · Ursache prüfen'};
    status=labels[result.receipt.status]||result.receipt.status;
    output.appendLine(status+'\n'+(result.receipt.error||'')+'\n'+(result.receipt.test?.output||'')+'\nBeleg: '+result.receipt_path);
    for(const attempt of result.receipt.attempts||[])output.appendLine(attempt.diff||'');
    if(result.receipt.candidate_unavailable)output.appendLine(result.receipt.candidate_unavailable);
    if(result.receipt.candidate)output.appendLine('Kandidat gesichert: '+result.receipt.candidate.path+' · Wiederaufnahme über „Gestoppten Auftrag mit anderem Ansatz fortsetzen“.');
    if(result.receipt.experience)output.appendLine('Geprüfter Laufbeleg für diese Reparatur: '+result.receipt.experience.path);
    if(result.receipt.blocker_report){
     const report=result.receipt.blocker_report;
     output.appendLine('\nFEHLERANALYSE\n'+report.summary+'\nOrt: '+report.location+
      '\nAbbruchgrund: '+(report.stop_reason||report.summary)+'\nBelegt: '+report.known+
      (report.last_test_failure?'\nLetzter Testfehler: '+report.last_test_failure.test_profile+' · '+report.last_test_failure.known:'')+
      '\nNoch unklar: '+report.unknown+'\nNächster Schritt: '+report.next_action+
      '\nRückrollen bestätigt: '+(report.rollback_verified?'ja':'nein; Sicherung prüfen'));
     vscode.window.showWarningMessage(status+': '+report.known.slice(0,240));
    }else vscode.window.showInformationMessage(status);
    output.show(true);return result;
   }finally{clearInterval(watch);activeRun=null;busy=false;changed.fire();}
  }),
  'codestudio.reject':()=>guard(async()=>{if(engine&&!engine.closed)await engine.request('reject');proposal=null;status='Vorschlag verworfen';changed.fire();}),
  'codestudio.apply':()=>guard(async()=>{
   if(!proposal||!engine)throw Error('Zuerst einen aktuellen Diff erzeugen.');
   const assertCurrent=()=>{if(!vscode.workspace.isTrusted||!vscode.workspace.workspaceFolders?.some(f=>f.uri.toString()===folder.uri.toString())||proposal.settings!==settingsIdentity())throw Error('Projekt oder Einstellungen geändert. Bitte neu analysieren.');if(dirty())throw Error('Ungespeicherte Editoränderungen: zuerst speichern und neu analysieren.');};
   assertCurrent();
   if(dirty())throw Error('Ungespeicherte Editoränderungen: zuerst speichern und neu analysieren.');
   const noTests=!hasTests();
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
