/* shell：通过 ctx 访问其他模块；变量保留在本模块闭包内。 */
KB.define('shell',($kb)=>{'use strict';
let pageGeneration=0;
const pageScroll=new Map();

function pageTitle(name){return ($kb.core.titleNames[name]||'文献研究工作台')+' · 文献研究工作台';}

function clearPageTimers(){$kb.dashboard.taskDashboard?.close();clearTimeout($kb.graph.graphTimer);clearTimeout($kb.dashboard.researchTimer);$kb.graph.graphTimer=null;$kb.graph.hideLineageTooltip();}

async function page(name){
  pageScroll.set($kb.core.state.page,window.scrollY||0);
  clearPageTimers();
  const generation=++pageGeneration;
  $kb.core.$('#task-dashboard-link').hidden=name==='research'||Boolean(window.STATIC_GRAPH);
  $kb.core.state.page=name;document.title=pageTitle(name);$kb.core.$('#page-label').textContent=$kb.core.titleNames[name];$kb.core.$$('nav button').forEach(b=>b.classList.toggle('active',b.dataset.page===name));$kb.core.$('#main').innerHTML=$kb.core.loading();const activeBtn=$kb.core.$('nav button.active'),navEl=activeBtn&&activeBtn.closest('nav');if(activeBtn&&navEl&&window.matchMedia('(max-width:850px)').matches)navEl.scrollLeft=Math.max(0,activeBtn.offsetLeft-(navEl.clientWidth-activeBtn.offsetWidth)/2);document.body.classList.remove('net-view');
  if(location.hash!=='#'+name)history.replaceState(null,'','#'+name);
  try{await ({research:$kb.graph.researchPage,graph:$kb.graph.graphPage,search:$kb.search.searchPage,chains:$kb.topics.chainsPage,approvals:$kb.approvals.approvalsPage,status:$kb.status.statusPage,settings:$kb.settings.settingsPage}[name])();if(generation===pageGeneration&&window.scrollTo)window.scrollTo(0,pageScroll.get(name)||0);}catch(e){if(generation===pageGeneration){$kb.core.$('#main').innerHTML=$kb.core.empty('暂时无法载入','关闭提示后可重新选择此页面。');$kb.feedback.showError(e.message,{title:'页面暂时无法载入'});};}
}

async function init(){
  document.addEventListener('click',event=>{const link=event.target.closest('[data-claim-ref]');if(!link)return;event.preventDefault();const evidence=document.getElementById(link.dataset.claimRef);if(evidence){evidence.open=true;evidence.scrollIntoView({block:'center'});evidence.querySelector('summary')?.focus();}});
  $kb.feedback.initFeedback();
  try{$kb.core.state.token=(await $kb.core.api('/api/session')).token;$kb.status.refreshStatus();}catch(e){$kb.feedback.showError(e.message);}
  $kb.core.$$('nav button').forEach(b=>b.onclick=()=>{const target=b.dataset.page;if(location.hash==='#'+target)page(target);else location.hash=target;});
  $kb.core.$('#main').addEventListener('click',e=>{
    const btn=e.target.closest('[data-toggle]');
    if(btn){
      if(btn.dataset.toggle==='subtitle'){const open=document.body.classList.toggle('subtitle-open');btn.setAttribute('aria-expanded',String(open));btn.textContent=open?'收起说明':'展开说明';}
      else if(btn.dataset.toggle==='legend'){const top=btn.closest('.graph-top');if(!top)return;const open=top.classList.toggle('legend-open');btn.setAttribute('aria-expanded',String(open));}
      return;
    }
    const act=e.target.closest('[data-empty-act]');if(!act)return;
    const a=act.dataset.emptyAct;
    if(a==='clear-graph'){
      $kb.core.state.graphQuery={...$kb.core.state.graphQuery,project:false,direction:'',q:'',candidates:false};
      if($kb.core.$('#graph-project'))$kb.core.$('#graph-project').checked=false;
      if($kb.core.$('#graph-direction'))$kb.core.$('#graph-direction').value='';
      if($kb.core.$('#graph-search'))$kb.core.$('#graph-search').value='';
      if($kb.core.$('#show-candidates'))$kb.core.$('#show-candidates').checked=false;
      $kb.graph.loadGraph();
    }else if(a==='clear-search'){
      if($kb.core.$('#query')){$kb.core.$('#query').value='';$kb.core.$('#query').focus();}
    }else if(a==='to-graph'){page('graph');}
    else if(a==='to-settings'){page('settings');}
  });$kb.core.$('#close-dialog').onclick=()=>$kb.core.$('#evidence-dialog').close();
  $kb.core.$('#evidence-dialog').onclick=e=>{if(e.target===$kb.core.$('#evidence-dialog'))e.target.close();};
  $kb.core.$('#task-dashboard-link').onclick=()=>$kb.dashboard.taskDashboard.showTask();
  document.addEventListener('keydown',e=>{
    if(e.key!=='Escape')return;
    // 弹窗优先：证据弹窗打开时不关闭右侧详情，避免丢失未保存的人工解释。
    if(document.querySelector('dialog[open]'))return;
    const panel=$kb.core.$('#detail');if(!panel||panel.hidden)return;
    const a=document.activeElement;
    if(a&&(a.tagName==='INPUT'||a.tagName==='TEXTAREA'||a.tagName==='SELECT'||a.isContentEditable))return;
    $kb.detail.closeDetail();
  });
  window.addEventListener('hashchange',()=>{const name=location.hash.slice(1);if($kb.core.titleNames[name]&&name!==$kb.core.state.page)page(name);});
  window.addEventListener('resize',()=>{$kb.detail.positionDetail();if($kb.core.state.page!=='graph')return;if($kb.core.state.graphMode==='chains'&&$kb.core.state.chainGroups)$kb.core.state.chainGroups.forEach((c,i)=>{const host=$kb.core.$('#lineage-'+i);if(host)$kb.graph.drawLineage(c,host);});else if($kb.core.state.graphMode==='network')$kb.graph.netResize();});
  const initial=location.hash.slice(1),pid=new URLSearchParams(location.search).get('paper');
  await page($kb.core.titleNames[initial]?initial:(window.STATIC_GRAPH||pid?'graph':'research'));if(pid)await $kb.detail.showPaper(pid);
  if(!window.STATIC_GRAPH){$kb.status.pollWorkbenchStatus();document.addEventListener('visibilitychange',()=>{if(!document.hidden)$kb.status.pollWorkbenchStatus();});}
}
return {get pageTitle(){return pageTitle;},set pageTitle(value){pageTitle=value;},
get pageGeneration(){return pageGeneration;},set pageGeneration(value){pageGeneration=value;},
get clearPageTimers(){return clearPageTimers;},set clearPageTimers(value){clearPageTimers=value;},
get page(){return page;},set page(value){page=value;},
get init(){return init;},set init(value){init=value;}};
});
