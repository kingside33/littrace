const test=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const source=require('./web-source.cjs').source();
function bubble(role){const context={};vm.createContext(context);vm.runInContext(source,context);return context.KB.context.dashboard.taskDashboard.roleBubbleHTML(role);}
test('work bubble shows at most two objects and escapes labels',()=>{
 const html=bubble({id:'verifier',active_count:4,activities:[{action:'核验证据',subject:'<script>first</script>'},{action:'复核',subject:'second'},{action:'third',subject:'SHOULD_NOT_APPEAR'}]});
 assert.equal((html.match(/class="agent-subject"/g)||[]).length,2);assert.match(html,/另 2 个/);assert.match(html,/&lt;script&gt;/);assert.doesNotMatch(html,/<script>|SHOULD_NOT_APPEAR/);
});
test('idle roles show recorded waiting state without invented activity',()=>{
 const html=bubble({id:'retriever',active_count:0,pending_count:3,message:'等待对应的前置证据',activities:[]});
 assert.match(html,/等待对应的前置证据/);assert.match(html,/3 个子单元待处理/);assert.doesNotMatch(html,/is-active/);
});
test('bubble does not render prompts or raw reasoning payloads',()=>{
 const html=bubble({id:'cloud_reviewer',active_count:1,activities:[{action:'独立复核证据与结论',subject:'Paper A',detail:'候选 3/12',prompt:'PRIVATE',reasoning_content:'PRIVATE'}],prompt:'PRIVATE',raw_response:'PRIVATE'});
 assert.match(html,/独立复核证据与结论/);assert.match(html,/候选 3\/12/);assert.equal((html.match(/class="agent-subject"/g)||[]).length,2);assert.doesNotMatch(html,/PRIVATE/);
});


test('connection halt has an actionable label without changing other failures',()=>{
 const context={};vm.createContext(context);vm.runInContext(source,context);
 const label=context.KB.context.dashboard.taskDashboard.taskStatus;
 assert.equal(label({status:'failed',attention:{label:'连接中断 · 待续跑'}}),'连接中断 · 待续跑');
 assert.equal(label({status:'failed'}),'执行失败');
});
