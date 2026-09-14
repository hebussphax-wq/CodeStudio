'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const {collect,relativeFile,taskWithContext}=require('../vscode/editor_context');
const uri=p=>({scheme:'file',fsPath:p}),folder={name:'Game',uri:uri('C:/Games/DonkeyMonkey')};
test('project isolation and protected paths',()=>{
 assert.equal(relativeFile(folder,uri('C:/Games/Other/game.js')),null);
 assert.equal(relativeFile(folder,uri('C:/Games/DonkeyMonkey/.env')),null);
 assert.equal(relativeFile(folder,uri('C:/Games/DonkeyMonkey/game.js')),'game.js');
 assert.equal(relativeFile(folder,{scheme:'https',fsPath:'C:/Games/DonkeyMonkey/game.js'}),null);
});
test('active selection and diagnostics reach agent as bounded data',()=>{
 const doc={uri:uri('C:/Games/DonkeyMonkey/game.js'),languageId:'javascript',isDirty:false,getText:()=> 'const password="private_value";'};
 const diag={severity:0,range:{start:{line:4,character:2}},message:'Missing export update'};
 const fake={window:{activeTextEditor:{document:doc,selection:{isEmpty:false,active:{line:3}}}},workspace:{textDocuments:[doc]},languages:{getDiagnostics:()=>[[doc.uri,Array(30).fill(diag)],[uri('C:/Games/Other/no.js'),[diag]]]}};
 const snapshot=collect(fake,folder);
 assert.equal(snapshot.active.path,'game.js');assert.equal(snapshot.active.line,4);
 assert.equal(snapshot.diagnostics.length,20);assert.equal(snapshot.diagnostics[0].line,5);
 assert.ok(!JSON.stringify(snapshot).includes('private_value'));
 assert.match(taskWithContext('Repair game',snapshot),/Missing export update/);
 assert.throws(()=>taskWithContext('a'.repeat(24000),snapshot),/zu lang/);
});
test('no editor and no diagnostics does not invent capability proof',()=>{
 const snapshot=collect({window:{},workspace:{textDocuments:[]},languages:{getDiagnostics:()=>[]}},folder);
 assert.equal(snapshot.active,null);assert.deepEqual(snapshot.diagnostics,[]);
 assert.ok(snapshot.limits.some(x=>x.includes('not test results')));
});
