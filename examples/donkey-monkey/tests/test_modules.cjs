const assert=require('node:assert/strict');const fs=require('node:fs');const path=require('node:path');const root=path.resolve(__dirname,'..');const mode=process.argv[2];
if(mode==='levels'){
 const {levels}=require('../levels.js');assert.equal(levels.length,4);assert.equal(new Set(levels.map(l=>JSON.stringify(l.platforms))).size,4);
 for(const l of levels){assert.ok(l.name&&l.platforms.length>=4&&l.ladders.length>=3&&l.bananas.length);assert.ok(l.start.y<540&&l.start.y>=0);for(const p of [...l.platforms,...l.ladders,l.exit])assert.ok([p.x,p.y,p.w,p.h].every(Number.isFinite)&&p.w>0&&p.h>0);
 const bottom=Math.max(...l.platforms.map(p=>p.y)),top=Math.min(...l.platforms.map(p=>p.y));
 assert.equal(l.start.y+32,bottom,l.name+': start feet must stand on bottom surface');
 assert.equal(l.exit.y+l.exit.h,top,l.name+': exit feet must stand on HIGHEST surface, not bottom');
 assert.ok(l.platforms.some(p=>p.y===top&&l.exit.x>=p.x&&l.exit.x+l.exit.w<=p.x+p.w),l.name+': exit horizontally on highest platform');
 for(const ladder of l.ladders){const cx=ladder.x+ladder.w/2;for(const y of [ladder.y,ladder.y+ladder.h])assert.ok(l.platforms.some(p=>Math.abs(p.y-y)<0.01&&cx>=p.x&&cx<=p.x+p.w),l.name+': ladder center x='+cx+' endpoint y='+y+' must meet a platform surface');}
 assert.ok(l.platforms.some(p=>l.start.x+24>p.x&&l.start.x<p.x+p.w&&Math.abs(l.start.y+32-p.y)<=1),'start on platform');
 const reached=new Set(l.platforms.filter(p=>Math.abs(l.start.y+32-p.y)<=1));let change=true;while(change){change=false;for(const lad of l.ladders){const touching=l.platforms.filter(p=>lad.x+lad.w>p.x&&lad.x<p.x+p.w&&p.y>=lad.y-3&&p.y<=lad.y+lad.h+3);if(touching.some(p=>reached.has(p)))for(const p of touching)if(!reached.has(p)){reached.add(p);change=true;}}}
 assert.ok([...reached].some(p=>l.exit.x+l.exit.w>p.x&&l.exit.x<p.x+p.w&&l.exit.y+l.exit.h>=p.y-3&&l.exit.y<=p.y),'exit has connected ladder route');}
}else if(mode==='physics'){
 const {movePlayer}=require('../physics.js');const l={platforms:[{x:0,y:500,w:960,h:20},{x:0,y:350,w:700,h:20}],ladders:[{x:100,y:350,w:32,h:150}]};const create=()=>({x:200,y:468,vx:0,vy:0,onGround:true});const run=(g,input,n)=>{for(let i=0;i<n;i++)movePlayer(g,l,input,1/60);};
 let g=create();run(g,{right:true},15);assert.ok(g.x>230,'horizontal position must change');let x=g.x;run(g,{left:true},15);assert.ok(g.x<x-30);
 g=create();run(g,{jump:true},5);assert.ok(g.y<450,'ground jump rises');run(g,{},100);assert.ok(Math.abs(g.y-468)<1,'lands on actual platform: expected y=468, actual y='+g.y+', vy='+g.vy+', onGround='+g.onGround);
 for(const key of ['up','down']){g={...create(),x:104,y:400,onGround:false};run(g,{[key]:true},5);assert.ok(key==='up'?g.y<398:g.y>402,'ladder '+key);}
 g={...create(),y:800};run(g,{},1);assert.ok(g.y>540,'do not clamp falling player onto screen floor');
 g=create();movePlayer(g,l,{right:true},1);assert.ok(g.x<=209.01,'dt must clamp to 0.05, actual x='+g.x);
 g={...create(),x:955};run(g,{right:true},10);assert.ok(g.x<=936,'player bounds');
}else if(mode==='style'){
 const css=fs.readFileSync(path.join(root,'style.css'),'utf8');assert.ok(css.length>250);assert.match(css,/canvas|#game/);assert.match(css,/button|#start/);assert.match(css,/max-width|@media/);
}else if(mode==='html'){
 const html=fs.readFileSync(path.join(root,'index.html'),'utf8');assert.match(html,/Donkey Monkey/);for(const id of ['game','start','hud'])assert.match(html,new RegExp('id=["\']'+id+'["\']'));
 let last=-1;for(const name of ['levels.js','physics.js','game.js','renderer.js','main.js']){let i=html.indexOf(name);assert.ok(i>last,'script ordering '+name);last=i;}assert.doesNotMatch(html,/(?:src|href)=["']https?:/);
}else if(mode==='renderer'){
 const {draw}=require('../renderer.js'),{createGame,levels}=require('../game.js');let calls=0;const ctx=new Proxy({canvas:{width:960,height:540},createLinearGradient:()=>({addColorStop(){}})}, {get:(o,k)=>k in o?o[k]:(...args)=>{calls++;},set:(o,k,v)=>(o[k]=v,true)});
 for(let i=0;i<4;i++)draw(ctx,createGame(i),{levels},null,0);assert.ok(calls>80,'draw complete game geometry');
}else{throw Error('Unknown module test');}console.log('PASS module '+mode);
