const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {test} = require('node:test');
const root = path.resolve(__dirname,'..');
const api = require(path.join(root,'game.js'));
const {levels,createGame,update} = api;
function run(game,input,frames=15){for(let i=0;i<frames;i++)update(game,input,1/60);return game;}

test('Four distinct complete levels and independent game state',()=>{
 assert.equal(levels.length,4);
 assert.equal(new Set(levels.map(l=>JSON.stringify(l.platforms))).size,4);
 for(let i=0;i<4;i++){
  const l=levels[i],g=createGame(i);
  assert.ok(l.name&&l.platforms.length>=4&&l.ladders.length>=3);
  assert.ok(l.start&&l.exit&&l.bananas.length>0);
  for(const p of [...l.platforms,...l.ladders,l.exit])assert.ok([p.x,p.y,p.w,p.h].every(Number.isFinite)&&p.w>0&&p.h>0);
  assert.equal(g.levelIndex,i);assert.equal(g.status,'playing');assert.equal(g.lives,3);
  assert.equal(g.x,l.start.x);assert.equal(g.y,l.start.y);
  assert.notEqual(g.bananas,l.bananas);
 }
});
test('Left/right controls move player and jump rises from ground',()=>{
 const g=createGame();run(g,{},20);const x=g.x;
 run(g,{right:true},12);assert.ok(g.x>x+5,'right should move');
 const xr=g.x;run(g,{left:true},12);assert.ok(g.x<xr-5,'left should move');
 const p=levels[0].platforms.slice().sort((a,b)=>b.y-a.y)[0];
 g.x=p.x+Math.min(p.w-30,60);g.y=p.y-32;g.vx=0;g.vy=0;g.onGround=true;
 const y=g.y;run(g,{jump:true},5);assert.ok(g.y<y-2,'Space jump should rise');
 run(g,{},110);assert.ok(g.lives>0&&g.y<=540,'player should land or respawn');
});
test('Up and down climb a ladder',()=>{
 const l=levels[0].ladders[0];
 for(const [key,sign] of [['up',-1],['down',1]]){
  const g=createGame();g.x=l.x+l.w/2-12;g.y=l.y+l.h/2-16;g.vx=0;g.vy=0;
  const y=g.y;run(g,{[key]:true},5);assert.ok((g.y-y)*sign>0,key+' should climb');
 }
});
test('Gravity lands on a platform',()=>{
 const g=createGame(),p=levels[0].platforms.slice().sort((a,b)=>b.y-a.y)[0];
 g.x=p.x+Math.min(70,p.w-30);g.y=p.y-90;g.vx=0;g.vy=0;g.barrels=[];
 run(g,{},40);assert.ok(Math.abs(g.y-(p.y-32))<2,'feet should rest on platform');
});
test('Bananas score, falls cost a life, and barrels spawn',()=>{
 const g=createGame(),b=g.bananas[0];g.x=b.x-12;g.y=b.y-16;const score=g.score;
 run(g,{},1);assert.ok(g.score>score,'banana should score');
 const lives=g.lives;g.y=800;run(g,{},1);assert.equal(g.lives,lives-1);
 const h=createGame();run(h,{},360);assert.ok(h.barrels.length>0,'rolling hazards should spawn');
});
test('All exits progress, fourth exit wins, exhausted lives end the game',()=>{
 for(let i=0;i<4;i++){
  const g=createGame(i),e=levels[i].exit;g.x=e.x;g.y=e.y;g.vy=0;run(g,{},1);
  if(i<3)assert.equal(g.levelIndex,i+1);else assert.equal(g.status,'won');
 }
 const g=createGame();g.lives=1;g.y=800;run(g,{},1);assert.equal(g.status,'gameover');
});
test('Browser entry point is offline, titled and connected to the game',()=>{
 for(const name of ['index.html','style.css','main.js'])assert.ok(fs.statSync(path.join(root,name)).size>100,name+' must be implemented');
 const html=fs.readFileSync(path.join(root,'index.html'),'utf8');
 assert.match(html,/Donkey Monkey/i);assert.match(html,/<canvas\b/i);
 assert.ok(html.indexOf('game.js')>=0&&html.indexOf('main.js')>html.indexOf('game.js'));
 assert.doesNotMatch(html,/(?:src|href)\s*=\s*["']https?:/i);
 const ui=fs.readFileSync(path.join(root,'main.js'),'utf8');
 assert.match(ui,/requestAnimationFrame/);assert.match(ui,/ArrowLeft/);assert.match(ui,/ArrowRight/);
 assert.match(ui,/ArrowUp/);assert.match(ui,/ArrowDown/);assert.match(ui,/Space|['"] ['"]/);
});
