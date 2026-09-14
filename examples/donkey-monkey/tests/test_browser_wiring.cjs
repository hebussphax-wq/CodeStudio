const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),ids=[...fs.readFileSync(path.join(root,'index.html'),'utf8').matchAll(/id="([^"]+)"/g)].map(m=>m[1]);
let handlers={},frames=[],click,updates=[],finish=null; const children=[];const els={};
for(const id of ids)els[id]={textContent:'',addEventListener:(name,f)=>{if(id==='start'&&name==='click')click=f;},getContext:()=>({})};
Object.defineProperty(els.hud,'innerHTML',{get:()=>'',set:()=>{throw Error('HUD must not replace cached child elements via innerHTML');}});
let hudOwn='';Object.defineProperty(els.hud,'textContent',{get:()=>hudOwn+' '+children.map(x=>x.textContent).join(' ')+' '+['score','level','lives','status'].filter(k=>els[k]).map(k=>els[k].textContent).join(' '),set:v=>hudOwn=String(v)});
els.hud.appendChild=x=>children.push(x);els.hud.append=(...xs)=>children.push(...xs);
const api={levels:[{name:'Jungle Level 1'}],createGame:()=>({levelIndex:0,lives:3,score:0,status:'playing',x:50,y:468}),update:(g,k,dt)=>{updates.push({...k}); if(finish)g.status=finish;}};
const world={console,document:{getElementById:id=>els[id]??null,createElement:()=>({textContent:'',style:{}})},Image:class{},DonkeyMonkey:api,DMRenderer:{draw(){}},requestAnimationFrame:f=>frames.push(f),addEventListener:(t,f)=>(handlers[t]??=[]).push(f)};world.window=world;
vm.runInNewContext(fs.readFileSync(path.join(root,'main.js'),'utf8'),world,{filename:'main.js'});
let now=0;function tick(){now+=1000/60;let f=frames;frames=[];f.forEach(fn=>fn(now));}function key(t,code,repeat=false){(handlers[t]??[]).forEach(f=>f({code,key:code==='Space'?' ':code,repeat,preventDefault(){}}));}
click();tick();assert.match(els.hud.textContent,/Leben.{0,5}3|3.{0,5}Leben/i,'HUD must show lives');
key('keydown','Space');tick();key('keydown','Space',true);tick();tick();assert.equal(updates.slice(-3).filter(x=>x.jump).length,1,'holding Space must send one jump pulse');key('keyup','Space');key('keydown','Space');tick();assert.equal(updates.at(-1).jump,true);key('keyup','Space');
key('keydown','Space');key('keyup','Space');tick();assert.equal(updates.at(-1).jump,true,'a quick Space tap between animation frames must not disappear');tick();assert.equal(updates.at(-1).jump,false,'quick tap is consumed once');
finish='gameover';tick();assert.match(els.hud.textContent,/Enter/i,'terminal HUD explains restart');finish=null;key('keydown','Enter');tick();assert.doesNotMatch(els.hud.textContent,/Game.?Over|Gewonnen/i,'restart clears terminal message');assert.match(els.hud.textContent,/Leben.{0,5}3|3.{0,5}Leben/i);
finish='won';tick();assert.match(els.hud.textContent,/gewonnen/i);finish=null;click();tick();assert.doesNotMatch(els.hud.textContent,/gewonnen/i);console.log('PASS browser HUD, jump pulse, terminal status and restart');
