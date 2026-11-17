const test=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const source=require('./web-source.cjs').source();
function setup(){
  const requests=[],context={state:{status:{cloud_enabled:true,index_actions:{vectors:true}}},send:async(url,body)=>{requests.push(JSON.parse(JSON.stringify({url,body})));return {job:'new-job'};}};
  vm.createContext(context);vm.runInContext(source+';api=send;',context);
  return {context,requests,submit:(kind,options={})=>context.KB.context.dashboard.taskDashboard.submit(kind,options)};
}
test('dashboard verification submits the explicitly selected scope once',async()=>{
  const app=setup(),filters={project:false,direction:'02',q:'RRT*'};
  await app.submit('verification',{filters});
  assert.deepEqual(app.requests,[{url:'/api/verification',body:{action:'start',filters}}]);
  app.context.KB.context.core.state.status.analysis_state={state:'running'};
  await assert.rejects(()=>app.submit('verification',{filters}),/核验正在进行/);
  assert.equal(app.requests.length,1);
});
test('paper navigation preserves the selected paper and reading stage at the shared submit point',async()=>{
  const app=setup();
  await app.submit('maintenance',{filters:{project:false},paper_ids:['paper-a'],stages:['understanding']});
  assert.deepEqual(app.requests[0],{url:'/api/agent-runs',body:{kind:'maintenance',filters:{project:false},paper_ids:['paper-a'],stages:['understanding']}});
});
test('update has one job action and dashboard cannot launch search-page functions',async()=>{
  const app=setup();await app.submit('update');
  assert.deepEqual(app.requests[0],{url:'/api/jobs',body:{action:'update'}});
  for(const kind of ['answer','compare'])await assert.rejects(()=>app.submit(kind),/有效的调度任务/);
  assert.equal(app.requests.length,1);
});

test('page title follows the selected page',()=>{
  const context={state:{status:{}},send:async()=>({})};
  vm.createContext(context);vm.runInContext(require('./web-source.cjs').source(),context);
  assert.equal(context.KB.context.shell.pageTitle('search'),'证据检索与问答 · 文献研究工作台');
  assert.equal(context.KB.context.shell.pageTitle('unknown'),'文献研究工作台 · 文献研究工作台');
});
