const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=require('./web-source.cjs').source();
function evaluate(expression,status={}){
  const context={state:{status},sourceNames:{paper_text:'论文原文'},esc:String,fmtDate:()=> '12:16',sessionStorage:{getItem:()=> '[]'}};
  return JSON.parse(vm.runInNewContext(source+'\nJSON.stringify('+expression+')',context));
}

test('verification workflow reflects the configured model instead of a fixed 35B label',()=>{
  const text=evaluate('verificationWorkflow(state.status.relation_pipeline)',{relation_pipeline:{local_verifier:'gemma4:e4b',cloud_reviewer:'DeepSeek-V4.1-Flash'}});
  assert.ok(text.includes('gemma4:e4b'));assert.ok(text.includes('DeepSeek-V4.1-Flash'));assert.ok(!text.includes('35B'));
});
test('completed vector progress remains at 100 percent while the following model task fails',()=>{
  const status={coverage:{sources:47439,vector_count:47439,vector_pending:0},indexing:{state:'idle'},local_model_state:{state:'failed',error:'CUDA_Host allocation failed'}};
  const view=evaluate('indexProgressView(state.status)',status);
  assert.equal(view.done,true);assert.equal(view.percent,100);assert.equal(view.completed,47439);
});
test('retained vectors with unverified weights are not shown as ready for semantic search',()=>{
  const view=evaluate('indexProgressView(state.status)',{index_health:{sources:100,vector_count:100,embedding_compatible:false,usable_vector_count:0,vector_warning:'保留旧向量，使用关键词检索'}});
  assert.equal(view.done,false);assert.equal(view.percent,0);assert.equal(view.completed,0);
  assert.match(view.title,/旧向量保留/);assert.match(view.note,/关键词检索/);
});
test('live vector progress and published coverage are different states',()=>{
  const view=evaluate('indexProgressView(state.status)',{coverage:{sources:100,vector_count:10},indexing:{state:'running',phase:'vectors',total:100,completed:60}});
  assert.equal(view.completed,60);assert.equal(view.done,false);assert.equal(view.live,true);
});
test('page body contains the result and a detail entry, never the technical model error',()=>{
  const html=evaluate('indexProgressHTML(state.status)',{coverage:{sources:100,vector_count:100,vector_pending:0},local_model_state:{state:'failed',error:'PRIVATE_TECHNICAL_ERROR'}});
  assert.ok(html.includes('100.0%'));assert.ok(html.includes('查看详情'));assert.ok(!html.includes('PRIVATE_TECHNICAL_ERROR'));
});
test('the same polled failure retains a stable notification key; a user stop is not a failure',()=>{
  const status={local_model_state:{state:'failed',error:'allocation failed',updated:'2026-10-31T12:16:26+08:00'}};
  assert.deepEqual(evaluate('taskErrorEvents(state.status)',status),evaluate('taskErrorEvents(state.status)',status));
  assert.deepEqual(evaluate('taskErrorEvents(state.status)',{local_model_state:{state:'stopped'}}),[]);
});
test('technical JSON is expanded only inside the detail view',()=>{
  assert.equal(evaluate('formatErrorDetail(JSON.stringify({error:"first\\nsecond"}))'),'first\nsecond');
});

test('dismissing a polled error prevents repeated dialogs while a different failure still opens',()=>{
  let opened=0;
  const elements={};
  for(const id of ['error-dialog','error-title','error-summary','error-details','error-technical','error-dismiss','error-close','error-impact','evidence-dialog']){
    elements['#'+id]={open:false,focus(){},addEventListener(name,callback){this[name]=callback;}};
  }
  const dialog=elements['#error-dialog'];
  dialog.showModal=()=>{dialog.open=true;opened++;};
  const context={state:{status:{coverage:{sources:10,vector_pending:0}}},sessionStorage:{getItem:()=> '[]',setItem(){}},
    document:{querySelector:id=>elements[id],activeElement:{isConnected:true,focus(){}}}};
  vm.createContext(context);
  vm.runInContext(source+'\ninitFeedback();showError("First",{key:"first"});showError("First",{key:"first"});',context);
  assert.equal(opened,1);
  dialog.open=false;dialog.close();
  vm.runInContext('showError("First",{key:"first"});',context);
  assert.equal(opened,1);
  vm.runInContext('showError("Second",{key:"second"});',context);
  assert.equal(opened,2);
});

test('verification result panel offers navigation without start or stop controls',()=>{
  const html=evaluate('verificationPanelHTML()');
  assert.match(html,/verification-task/);assert.match(html,/verification-progress/);
  assert.doesNotMatch(html,/verification-(start|stop)/);
});

test('deferred verification keeps technical failure text inside the dialog event',()=>{
  const state={analysis_state:{job:'verify-1',state:'deferred',reason:'PRIVATE_TECHNICAL_DETAIL'}};
  const events=evaluate('taskErrorEvents(state.status)',state);
  assert.equal(events[0].detail,'PRIVATE_TECHNICAL_DETAIL');assert.ok(!events[0].message.includes('PRIVATE_TECHNICAL_DETAIL'));
});

test('OCR preparation displays its own progress without claiming relation verification is complete',()=>{
  const view=evaluate('verificationProgressView(state.status.analysis_state)',{analysis_state:{state:'running',stage:'recognition',ocr_papers_done:16,ocr_papers_total:267,completed:0,total:267}});
  assert.equal(view.done,16);assert.equal(view.total,267);assert.ok(view.percent>5&&view.percent<7);assert.equal(view.label,'原文准备阶段进度');
  const after=evaluate('verificationProgressView(state.status.analysis_state)',{analysis_state:{state:'running',stage:'local_verification',ocr_papers_done:267,ocr_papers_total:267,completed:2,total:267}});
  assert.equal(after.done,2);assert.equal(after.label,'研究关系核验进度');
  const finished=evaluate('verificationProgressView(state.status.analysis_state)',{analysis_state:{state:'complete',completed:267,total:267}});
  assert.equal(finished.percent,100);
});


test('new recognized sources reduce total coverage even when the published version is complete',()=>{
  const status={coverage:{sources:47439,vector_count:47439,vector_pending:0},index_health:{sources:52828,vector_count:47439,vector_pending:5389,live_vector_pending:5389}};
  const view=evaluate('indexProgressView(state.status)',status);
  assert.equal(view.done,false);assert.equal(view.total,52828);assert.equal(view.completed,47439);
  assert.ok(view.percent>89&&view.percent<90);assert.match(view.title,/新原文/);
  const html=evaluate('indexProgressHTML(state.status)',status);
  assert.match(html,/89.8%/);assert.doesNotMatch(html,/全部向量已就绪/);
});

test('all batches cached does not label the unpublished build as published',()=>{
  const status={index_health:{sources:10,vector_count:8,vector_pending:2,published_sources:8,live_vector_pending:2,by_kind:[{kind:'paper_text',sources:10,vectors:8,published_pending:0,live_pending:2}]},indexing:{state:'running',phase:'awaiting_publication',completed:10,total:10,reused:8,new_completed:2,cached_unpublished:2,by_kind:{paper_text:10}}};
  const html=evaluate('indexProgressHTML(state.status)',status);
  assert.match(html,/100.0%/);assert.match(html,/已缓存待发布 2/);assert.match(html,/本轮任务/);assert.doesNotMatch(html,/>已发布</);
  const table=evaluate('indexHealthHTML(state.status)',status);
  assert.match(table,/论文原文/);assert.match(table,/已发布向量/);assert.match(table,/<td>10<\/td><td>8<\/td>/);
});

test('polling replaces source details and totals without rebuilding the page',()=>{
  const nodes={'#index-health':{innerHTML:''},'#index-refresh':{textContent:''},'#index-task':{textContent:''},'#library-metric':{textContent:''}};
  const state={status:{papers:3,index_health:{sources:5,vector_count:4,vector_pending:1,live_vector_pending:1,by_kind:[]}}};
  const context={state,sourceNames:{},esc:String,fmtDate:String,window:{},document:{querySelector:id=>nodes[id]},sessionStorage:{getItem:()=> '[]'}};
  vm.createContext(context);vm.runInContext(source+'\nrenderIndexDetails();',context);
  const before=nodes['#index-health'].innerHTML;assert.equal(nodes['#library-metric'].textContent,'3');
  state.status.papers=4;state.status.index_health.sources=7;state.status.index_health.live_vector_pending=3;
  vm.runInContext('renderIndexDetails();',context);
  assert.notEqual(nodes['#index-health'].innerHTML,before);assert.equal(nodes['#library-metric'].textContent,'4');assert.match(nodes['#index-refresh'].textContent,/每2秒/);
});

test('status and running jobs refresh every two seconds; hidden and other idle pages back off',()=>{
  assert.equal(evaluate('statusPollDelay({},"status")'),2000);
  assert.equal(evaluate('statusPollDelay({indexing:{state:"running"}},"graph")'),2000);
  assert.equal(evaluate('statusPollDelay({},"graph")'),30000);
  assert.equal(evaluate('statusPollDelay({},"status",true)'),30000);
});


test('an old running backend is identified instead of reporting unknown new sources as zero',()=>{
  const html=evaluate('indexHealthHTML(state.status)',{coverage:{sources:47439,vector_count:47439,vector_pending:0}});
  assert.match(html,/重启工作台服务/);assert.match(html,/新增来源数量尚未返回/);assert.doesNotMatch(html,/待向量化来源/);
});


test('the independent vector button starts or resumes while full updates remain a distinct operation',()=>{
  const enabled={index_actions:{vectors:true},index_health:{vector_count:0,vector_pending:5}};
  let view=evaluate('vectorActionView(state.status)',enabled);
  assert.equal(view.label,'开始向量索引');assert.equal(view.disabled,false);assert.match(view.note,/不执行OCR或切块/);assert.match(view.note,/不调用云端分析/);
  enabled.index_health.vector_count=10;
  assert.equal(evaluate('vectorActionView(state.status)',enabled).label,'继续向量索引');
  enabled.indexing={state:'running',kind:'update',progress:'chunk.py'};
  view=evaluate('vectorActionView(state.status)',enabled);
  assert.equal(view.label,'完整更新进行中');assert.equal(view.disabled,true);assert.match(view.note,/正在切分PDF文本/);
  enabled.indexing={state:'running',kind:'vectors',progress:'Qwen 向量化 64/100；复用 10'};
  view=evaluate('vectorActionView(state.status)',enabled);
  assert.equal(view.label,'向量索引进行中');assert.match(view.note,/仅补齐本地向量/);
});

test('offline pages and backends without vector-only capability cannot launch a full update by accident',()=>{
  const status={index_actions:{vectors:true}};
  assert.equal(evaluate('vectorActionView(state.status,false,true)',status).disabled,true);
  const old=evaluate('vectorActionView(state.status)',{});
  assert.equal(old.disabled,true);assert.match(old.note,/独立向量接口/);
});

test('task rows show the job type and translate the chunking stage',()=>{
  const html=evaluate('jobRowsHTML(state.status.jobs)',{jobs:[{id:'u',kind:'update',status:'running',progress:'chunk.py'},{id:'v',kind:'vectors',status:'partial',progress:'cached'}]});
  assert.match(html,/完整更新知识库/);assert.match(html,/向量索引/);assert.match(html,/正在切分PDF文本/);assert.match(html,/部分完成/);assert.doesNotMatch(html,/chunk.py/);
});

test('parse issue text separates pending, failed, repaired and archived buckets',()=>{
  const text=evaluate('parseIssueText({pending:736,failed:2,repaired:14,archived:189})',{});
  assert.match(text,/待处理 736 页/);assert.match(text,/失败 2 页/);assert.match(text,/已修复 14 项/);assert.match(text,/历史保留 189 项/);
  assert.equal(evaluate('parseIssueText({pending:0,failed:0,repaired:0,archived:0})',{}),'');
});
