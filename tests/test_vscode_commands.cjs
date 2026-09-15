const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path'),{EventEmitter}=require('node:events');
async function exercise(settings,command='codestudio.runTests'){
 const handlers={},requests=[],errors=[];const uri={scheme:'file',fsPath:'C:/fixture',toString:()=> 'file:///C:/fixture'};
 const vscode={EventEmitter:class{constructor(){this.event=()=>{};}fire(){}},window:{createOutputChannel:()=>({appendLine(){},show(){}}),registerTreeDataProvider:()=>({}),showInputBox:async()=> 'Build fixture',showWarningMessage:async()=> 'Autonom entwickeln',showInformationMessage(){},showErrorMessage:e=>errors.push(e)},workspace:{isTrusted:true,workspaceFolders:[{uri}],textDocuments:[],getConfiguration:()=>({get:(k,d)=>({testCommand:['python','test.py'],model:'local',contextTokens:8192,...settings}[k]??d)}),registerTextDocumentContentProvider:()=>({}),onDidChangeConfiguration:()=>({})},commands:{registerCommand:(k,v)=>(handlers[k]=v,{}),executeCommand:()=>{}}};
 const spawn=()=>{const child=new EventEmitter();child.stdout=new EventEmitter();child.stdout.setEncoding=()=>{};child.stderr={resume(){}};child.stdin=new EventEmitter();child.stdin.write=line=>{const q=JSON.parse(line);requests.push(q);queueMicrotask(()=>child.stdout.emit('data',JSON.stringify({id:q.id,ok:true,result:q.command==='test'?{returncode:0,output:'real test fixture'}:q.command==='autonomous'?{receipt:{status:'succeeded',test:{returncode:0}},receipt_path:'fixture.json'}:{}})+'\n'));};child.stdin.end=()=>{};child.kill=()=>{};return child;};
 const box={module:{exports:{}},require:n=>n==='vscode'?vscode:n==='child_process'?{spawn}:n==='fs'?{existsSync:()=>true}:n==='./editor_context'?{collect:()=>({}),taskWithContext:t=>t}:require(n),Buffer,process,setInterval,clearInterval,setTimeout,queueMicrotask};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../vscode/extension.js'),'utf8'),box);
 box.module.exports.activate({subscriptions:[],extensionPath:'C:/extension',globalStorageUri:{fsPath:'C:/state'},globalState:{get:()=>true}});
 const result=await handlers[command]();return {result,requests,errors,handlers};
}
test('real Run Tests handler configures and executes engine without options argument',async()=>{
 const {result,requests,errors,handlers}=await exercise({});assert.equal(result.returncode,0);assert.deepEqual(requests.map(x=>x.command),['configure','test']);assert.deepEqual(requests[0].fallback_models,[]);assert.equal(typeof handlers['codestudio.fallbackModels'],'function');assert.deepEqual(errors,[]);
});

test('ordinary VS Code autonomous command uses saved profiles and all configured limits',async()=>{
 const profiles=[{name:'unit',argv:['C:/python.exe','unit.py'],timeout_sec:30},{name:'integration',argv:['C:/node.exe','test.cjs'],timeout_sec:60}];
 const {requests,result,errors}=await exercise({testCommand:[],testProfiles:profiles,outputTokens:8192,contextTokens:32768,autonomousSteps:9,autonomousMinutes:90,autonomousRepairs:3,autonomousModelCalls:100},'codestudio.autonomous');
 assert.deepEqual(requests.map(r=>r.command),['configure','autonomous']);
 assert.deepEqual(requests[0].test_profiles,profiles);assert.equal(requests[0].output_tokens,8192);
 assert.deepEqual(requests[1].limits,{steps:9,repairs:3,seconds:5400,model_calls:100});
 assert.equal(requests[1].workflow,undefined);assert.equal(result.receipt.status,'succeeded');assert.deepEqual(errors,[]);
});
test('saved multiple profiles also drive the ordinary test button',async()=>{
 const profiles=[{argv:['C:/python.exe','test.py'],timeout_sec:30}];
 const {requests,result}=await exercise({testCommand:[],testProfiles:profiles});
 assert.equal(result.returncode,0);assert.deepEqual(requests[0].test_profiles,profiles);
});
