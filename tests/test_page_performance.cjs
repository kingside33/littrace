const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.join(__dirname,'../kbapp/web/assets/modules');

function coreHarness(){
  let core,now=1000;const calls=[];
  vm.runInNewContext(fs.readFileSync(path.join(root,'core.js'),'utf8'),{
    KB:{define:(name,factory)=>core=factory({})},window:{},document:{},URL,
    Date:class extends Date {static now(){return now;}},
    fetch:(url,options)=>new Promise((resolve,reject)=>calls.push({url,options,resolve:data=>resolve({ok:true,json:async()=>data}),reject})),
  });
  return {core,calls,advance:ms=>now+=ms};
}

test('GETs deduplicate in flight and page-return reads reuse a bounded short-lived cache',async()=>{
  const h=coreHarness(),one=h.core.api('/api/methods?direction=02'),two=h.core.api('/api/methods?direction=02');
  assert.equal(h.calls.length,1);h.calls[0].resolve({items:[{id:'sampling'}]});await one;await two;
  await h.core.api('/api/methods?direction=02');assert.equal(h.calls.length,1);
  h.advance(16000);const fresh=h.core.api('/api/methods?direction=02');assert.equal(h.calls.length,2);h.calls[1].resolve({items:[]});await fresh;
});

test('mutations invalidate old GETs and in-flight old responses cannot refill the cache',async()=>{
  const h=coreHarness(),old=h.core.api('/api/chains/summary');
  const change=h.core.api('/api/relations/r',{status:'candidate'});h.calls[1].resolve({});await change;
  const fresh=h.core.api('/api/chains/summary');assert.equal(h.calls.length,3);
  h.calls[2].resolve({revision:'new'});await fresh;h.calls[0].resolve({revision:'old'});await old;
  assert.equal((await h.core.api('/api/chains/summary')).revision,'new');assert.equal(h.calls.length,3);
});

test('failed GETs are retryable and live status is never served from the page cache',async()=>{
  const h=coreHarness(),bad=h.core.api('/api/overview');h.calls[0].reject(Error('offline'));await assert.rejects(bad);
  const retry=h.core.api('/api/overview');h.calls[1].resolve({});await retry;
  for(let i=0;i<2;i++){const status=h.core.api('/api/status?detail=false');h.calls.at(-1).resolve({});await status;}
  assert.equal(h.calls.length,4);
});

function statusHarness(){
  const calls=[],elements=new Map();let status,cleared=0,stale=0;
  const core={state:{page:'research',status:{}},
    $:selector=>selector==='#cost-metric'?null:elements.get(selector)||elements.set(selector,{textContent:''}).get(selector),
    fmtDate:()=>'',clearReadCache:()=>cleared++,
    api:url=>new Promise(resolve=>calls.push({url,resolve}))};
  const ctx={core,feedback:new Proxy({},{get:()=>()=>{}}),graph:{busyUser:()=>false,hideStaleBanner(){},showStaleBanner:()=>stale++}};
  vm.runInNewContext(fs.readFileSync(path.join(root,'status.js'),'utf8'),{KB:{define:(name,factory)=>status=factory(ctx)},window:{},JSON});
  return {status,core,calls,elements,cleared:()=>cleared,stale:()=>stale};
}

test('entering index details waits for a pending light status then explicitly obtains full health',async()=>{
  const h=statusHarness(),light=h.status.refreshStatus(),full=h.status.refreshStatus(true);
  assert.equal(h.calls[0].url,'/api/status?detail=false');
  h.calls[0].resolve({status_detail:false,papers:2,external:1,data_revision:[1,1],active_version:'v'});await light;
  await new Promise(resolve=>setImmediate(resolve));assert.equal(h.calls[1].url,'/api/status');
  h.calls[1].resolve({status_detail:true,papers:2,external:1,data_revision:[1,1],active_version:'v',index_health:{sources:10}});await full;
  assert.equal(h.core.state.status.index_health.sources,10);
  const next=h.status.refreshStatus();h.calls[2].resolve({status_detail:false,papers:3,external:1,data_revision:[2,1],active_version:'new'});await next;
  assert.equal(h.core.state.status.index_health,undefined);assert.equal(h.cleared(),1);
});

test('offline status snapshot supports the lightweight query without performing fetch',async()=>{
  let core;
  vm.runInNewContext(fs.readFileSync(path.join(root,'core.js'),'utf8'),{
    KB:{define:(name,factory)=>core=factory({})},window:{STATIC_GRAPH:{status:{papers:5,index_health:{sources:10}}}},document:{},URL,
  });
  const result=await core.api('/api/status?detail=false');
  assert.equal(result.papers,5);assert.equal(result.index_health.sources,10);assert.equal(result.status_detail,true);
});

test('the first background status observation updates counts without a false stale-graph warning',async()=>{
  const h=statusHarness();h.core.state.page='graph';h.core.state.graphMode='chains';
  const first=h.status.refreshStatus();h.calls[0].resolve({status_detail:false,data_revision:[1,1],relations:{cites:3}});await first;
  assert.equal(h.stale(),0);
  const changed=h.status.refreshStatus();h.calls[1].resolve({status_detail:false,data_revision:[1,2],relations:{cites:4}});await changed;
  assert.equal(h.stale(),1);
});
