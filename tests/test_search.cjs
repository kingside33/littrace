const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=require('./web-source.cjs').source();

function setup(responses){
  const elements=new Map(),requests=[];
  const element=key=>{if(!elements.has(key))elements.set(key,{innerHTML:'',value:'',checked:false,insertAdjacentHTML(){},querySelector(){return null;}});return elements.get(key);};
  const context={console,URL,URLSearchParams,setTimeout:callback=>{callback();return 1;},clearTimeout(){},
    document:{readyState:'loading',addEventListener(){},querySelector:element,querySelectorAll:()=>[]},
    mockAPI:async path=>{requests.push(new URL(path,'http://local'));return responses.shift();}};
  vm.createContext(context);
  vm.runInContext(source+"\napi=mockAPI;state.page='search';state.status.cloud_enabled=true;globalThis.view={searchPage,retrievalSummary,renderResults,answerHTML,verificationHTML,comparisonHTML};",context);
  return {context,elements,requests,view:context.view,element};
}

test('semantic search waits for vector completion and never requests a keyword preview',async()=>{
  const app=setup([{vector_status:'queued',mode:'bm25',results:[]},{vector_status:'ready',mode:'bm25+vector(rrf)',results:[]}]);
  app.view.searchPage();app.element('#query').value='narrow passages';
  app.element('#search-form').onsubmit({preventDefault(){}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(app.requests.length,2);
  assert.ok(app.requests.every(url=>url.searchParams.get('vectors')==='true'&&url.searchParams.get('wait_vectors')==='true'));
  assert.equal(app.requests[1].searchParams.get('resume_model'),'false');
  assert.match(app.element('#results').innerHTML,/语义向量 \+ 关键词融合/);
  assert.doesNotMatch(app.element('#results').innerHTML,/正在排队/);
});

test('a superseded search cannot overwrite the current question results',async()=>{
  const app=setup([]);let resolve;
  app.context.mockAPI=()=>new Promise(done=>{resolve=done;});
  vm.runInContext('api=mockAPI;',app.context);app.view.searchPage();app.element('#query').value='first';
  app.element('#search-form').onsubmit({preventDefault(){}});
  vm.runInContext('searchGeneration++;',app.context);
  app.element('#results').innerHTML='current question';
  resolve({mode:'bm25',results:[]});await new Promise(done=>setImmediate(done));
  assert.equal(app.element('#results').innerHTML,'current question');
});

test('actual retrieval mode and pending coverage are distinct in results and answers',()=>{
  const app=setup([]),data={mode:'two-stage-hybrid+rerank',vector_status:'cached',coverage:{vectors:47439,published_sources:47439,live_vector_pending:5389},results:[{id:'s',kind:'paper_text',title:'Semantic source',text:'Evidence',retrieval:{vector_rank:1,keyword_rank:null}}]};
  app.view.renderResults(data);const html=app.element('#results').innerHTML;
  assert.match(html,/语义向量 \+ 关键词融合/);assert.match(html,/语义命中/);
  assert.match(html,/47,439 \/ 47,439/);assert.match(html,/5,389 条新原文待向量化/);
  assert.match(app.view.answerHTML({answer:'Answer',search:data}),/语义向量 \+ 关键词融合/);
  assert.match(app.view.retrievalSummary({mode:'two-stage-bm25+rerank'}),/关键词检索/);
  assert.doesNotMatch(app.view.retrievalSummary({mode:'two-stage-bm25+rerank'}),/关键词融合/);
  assert.match(app.view.retrievalSummary({mode:'two-stage-mixed+rerank'}),/部分查询/);
});


test('concurrent refresh triggers share one status request',async()=>{
  const app=setup([]);let resolve,calls=0;
  app.context.mockAPI=()=>{calls++;return new Promise(done=>{resolve=done;});};
  for(const name of ['renderIndexProgress','renderIndexDetails','renderJobRows','monitorTaskErrors'])app.context[name]=()=>{};
  vm.runInContext('api=mockAPI;renderMethodProgress=()=>{};renderCostMetric=()=>{};',app.context);
  const first=vm.runInContext('refreshStatus()',app.context),second=vm.runInContext('refreshStatus()',app.context);
  assert.equal(calls,1);resolve({});await Promise.all([first,second]);
  const third=vm.runInContext('refreshStatus()',app.context);assert.equal(calls,2);resolve({});await third;
});


test('vector indexing sends only the vectors action and deduplicates clicks while starting',async()=>{
  const app=setup([]),sent=[];let resolve;
  app.context.window={};
  app.context.mockAPI=(path,body)=>{sent.push({path,body});return new Promise(done=>{resolve=done;});};
  vm.runInContext("api=mockAPI;state.status.index_actions={vectors:true};",app.context);
  const first=vm.runInContext("taskDashboard.submit('vectors')",app.context),second=vm.runInContext("taskDashboard.submit('vectors')",app.context);
  assert.equal(sent.length,1);assert.equal(sent[0].path,'/api/jobs');assert.equal(sent[0].body.action,'vectors');
  resolve({state:'started'});await Promise.all([first,second]);
  vm.runInContext("state.status.indexing={state:'running',kind:'vectors'}",app.context);
  await assert.rejects(()=>vm.runInContext("taskDashboard.submit('vectors')",app.context));assert.equal(sent.length,1);
});

test('a failed dashboard submission releases its guard for retry',async()=>{
  const app=setup([]),errors=[];
  app.context.window={};app.context.mockAPI=async()=>{throw Error('queue busy');};app.context.capture=message=>errors.push(message);
  vm.runInContext('api=mockAPI;state.status.index_actions={vectors:true};',app.context);
  await assert.rejects(()=>vm.runInContext("taskDashboard.submit('vectors')",app.context),/queue busy/);
  app.context.mockAPI=async()=>({state:'started'});vm.runInContext('api=mockAPI;',app.context);
  assert.equal((await vm.runInContext("taskDashboard.submit('vectors')",app.context)).state,'started');
});

test('verification trace separates local uncertainty, cloud support and evidence grade',()=>{
  const {view}=setup([]);
  const html=view.verificationHTML({label:'云端复核通过',confidence:'medium',local_state:'insufficient',cloud_state:'supported',local:{reason:'上下文不足'},cloud:{reason:'补证支持'},supplement_rounds:1,supplement_reasons:['<unsafe>'],rule_version:'v2'});
  assert.match(html,/中置信度/);assert.match(html,/本地：证据不足/);assert.match(html,/云端：支持/);
  assert.match(html,/&lt;unsafe&gt;/);assert.match(html,/证据等级仍以原文／摘要标记为准/);
});
test('comparison shows explicit noncomparable conditions alongside all four fields',()=>{
  const {view}=setup([]);
  const html=view.comparisonHTML({comparison:[{title:'paper',method:[],assumptions:[],metrics:[],limitations:[]}],comparison_warnings:['实验条件不同，不可直接比较']},'c');
  for(const text of ['方法','假设','指标','局限','不可直接比较'])assert.ok(html.includes(text));
});
