const test=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
function dashboard(){const context={};vm.createContext(context);vm.runInContext(require('./web-source.cjs').source(),context);return context.KB.context.dashboard.taskDashboard;}

test('primary action follows supported controls and stopping cannot submit again',()=>{
 const view=dashboard().primaryAction;
 assert.equal(view({status:'interrupted',actions:['resume']}).action,'resume');
 assert.equal(view({status:'running',actions:['stop']}).action,'stop');
 assert.equal(view({status:'stopping',actions:['stop']}).disabled,true);
 assert.equal(view({status:'running',actions:[]}).disabled,true);
 assert.equal(view({status:'complete',actions:[]}).label,'开始任务');
});
test('phase list has one row per phase and only two current objects',()=>{
 const ui=dashboard(),html=ui.phaseHTML([{id:'review-cloud',title:'云端复核',roles:[],status:'running',total:125,completed:100,active_count:3,active_items:[{title:'<Paper A>'},{title:'Paper B'},{title:'hidden-third'}],details:[]}],{id:'run'});
 assert.equal((html.match(/data-phase=/g)||[]).length,1);
 assert.match(html,/100 \/ 125 完成/);assert.match(html,/另 1 项并行/);
 assert.match(html,/&lt;Paper A&gt;/);assert.doesNotMatch(html,/hidden-third|type="checkbox"/);
});
test('single paper scope keeps paper identity and stage visible',()=>{
 assert.match(dashboard().scopeText({paper_ids:['p'],paperLabel:'Paper P'}),/Paper P · 仅全文理解/);
});
