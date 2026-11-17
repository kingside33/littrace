/* approvals：通过 ctx 访问其他模块；变量保留在本模块闭包内。 */
KB.define('approvals',($kb)=>{'use strict';
const PAGE=50;
let state={offset:0,total:0,pending:0,items:[],hasMore:false,loading:false};
function cardHTML(a){
  return `<article class="approval"><div><input type="checkbox" data-approval="${$kb.core.esc(a.paper_id)}" aria-label="选择${$kb.core.esc(a.paper.title)}" ${a.status!=='pending'?'disabled':''}></div><div><span class="badge ${a.status==='pending'?'warn':a.status==='rejected'?'bad':'good'}">${$kb.core.esc({pending:'待审批',approved:'已批准，等待获取',rejected:'已拒绝',downloaded:'全文已获取'}[a.status]||a.status)}</span><span class="small-text muted">${a.paper.year||''}</span><h3>${$kb.core.esc(a.paper.title)}</h3><div class="summary">${$kb.core.esc(a.paper.abstract||'暂无摘要')}</div><div class="reason"><strong>为什么重要</strong>${$kb.core.esc(a.reason)}<br><strong>需要核实</strong>${$kb.core.esc(a.question)}</div>${a.error?`<div class="callout warn">${$kb.core.esc(a.error)}</div>`:''}<div class="approval-footer"><button class="button small" data-paper="${$kb.core.esc(a.paper_id)}">查看文献关系</button>${a.status==='rejected'?`<button class="button small" data-reopen="${$kb.core.esc(a.paper_id)}">重新列入待审批</button>`:''}</div></div></article>`;
}
function bind(){
  const all=$kb.core.$('#select-all');if(all)all.onchange=e=>$kb.core.$$('[data-approval]:not(:disabled)').forEach(x=>x.checked=e.target.checked);
  const more=$kb.core.$('#approvals-more');if(more)more.onclick=()=>load(false);
  const decide=async decision=>{
    const ids=$kb.core.$$('[data-approval]:checked').map(x=>x.dataset.approval);
    if(!ids.length)return $kb.core.toast('请先选择待审批论文');
    const buttons=[$kb.core.$('#approve-selected'),$kb.core.$('#reject-selected')].filter(Boolean);
    if(buttons.some(b=>b.disabled))return;
    buttons.forEach(b=>{b.disabled=true;});
    try{await $kb.core.api('/api/approvals/decide',{paper_ids:ids,decision});$kb.core.toast('审批结果已保存');await $kb.status.refreshStatus();await approvalsPage();}
    catch(e){$kb.feedback.showError(e.message);buttons.forEach(b=>{b.disabled=false;});}
  };
  const approve=$kb.core.$('#approve-selected'),reject=$kb.core.$('#reject-selected');
  if(approve)approve.onclick=()=>decide('approved');if(reject)reject.onclick=()=>decide('rejected');
  $kb.core.$$('[data-paper]').forEach(el=>el.onclick=()=>$kb.detail.showPaper(el.dataset.paper));
  $kb.core.$$('[data-reopen]').forEach(el=>el.onclick=async()=>{
    if(el.disabled)return;const label=el.textContent;el.disabled=true;el.textContent='处理中…';
    try{await $kb.core.api('/api/approvals/decide',{paper_ids:[el.dataset.reopen],decision:'pending'});await $kb.status.refreshStatus();await approvalsPage();}
    catch(e){el.disabled=false;el.textContent=label;$kb.feedback.showError(e.message,{title:'未能重新列入待审批'});}
  });
}
function render(){
  if($kb.core.state.page!=='approvals')return;
  const list=state.items.map(cardHTML).join('')||$kb.core.empty('当前没有待审批全文','在图谱中选中关键库外论文可发起申请；证据分析也会把需要核实的关键文献列到这里。');
  $kb.core.$('#main').innerHTML=$kb.core.intro('FULL-TEXT APPROVAL','让关键全文经过你的判断','库外先保存元数据与摘要。这里说明为什么需要全文，以及获取后将核实什么；批准后在下一轮更新中获取。')+`<div class="approval-actions"><label class="small-text"><input type="checkbox" id="select-all"> 全选待审批</label><button class="button primary" id="approve-selected">批准所选</button><button class="button danger" id="reject-selected">拒绝所选</button><span class="spacer"></span><span class="muted small-text">共 ${state.total} 条 · 待审批 ${state.pending} 条 · 已加载 ${state.items.length} 条</span></div><div id="approval-list">${list}</div>${state.hasMore?`<p style="text-align:center"><button class="button" id="approvals-more">加载更多（剩余 ${Math.max(0,state.total-state.items.length)} 条）</button></p>`:''}`;
  bind();
}
async function load(reset){
  if(state.loading)return;
  state.loading=true;
  try{
    if(reset){state={offset:0,total:0,pending:0,items:[],hasMore:false,loading:true};}
    const data=await $kb.core.api('/api/approvals?'+new URLSearchParams({offset:state.offset,limit:PAGE}));
    state.items=state.items.concat(data.items||[]);state.total=data.total||0;state.pending=data.pending||0;state.offset=state.items.length;state.hasMore=Boolean(data.has_more);
  }finally{state.loading=false;}
  render();
}
async function approvalsPage(){await load(true);}
return {get approvalsPage(){return approvalsPage;},set approvalsPage(value){approvalsPage=value;}};
});
