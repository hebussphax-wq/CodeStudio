'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
test('all settings written by project commands support folder scope',()=>{
 const source=fs.readFileSync(path.join(__dirname,'../vscode/extension.js'),'utf8');
 const properties=require('../vscode/package.json').contributes.configuration.properties;
 const keys=[...source.matchAll(/await update\('([^']+)'/g)].map(m=>m[1]);
 assert.ok(keys.includes('model')&&keys.includes('contextTokens')&&keys.includes('testCommand'));
 for(const key of keys)assert.equal(properties['codestudio.'+key]?.scope,'resource','WorkspaceFolder update requires resource scope: '+key);
 assert.equal(properties['codestudio.hostBinding'].scope,'window');
});
