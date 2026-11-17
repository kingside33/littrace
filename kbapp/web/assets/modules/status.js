/* status：通过 ctx 访问其他模块；变量保留在本模块闭包内。 */
KB.define('status',($kb)=>{'use strict';
let statusRequest=null;

let statusPollTimer=null;

let statusPolling=false;

async function refreshStatus(detail=$kb.core.state.page==='status'){
  if(statusRequest){await statusRequest;if(detail&&$kb.core.state.status.status_detail!==true)return refreshStatus(true);return;}
  statusRequest=refreshStatusNow(detail).finally(()=>{statusRequest=null;});
  return statusRequest;
}

async function refreshStatusNow(detail=false){try{const prior=$kb.core.state.status||{},priorAnalysis=prior.analysis_state;const incoming=await $kb.core.api(detail?'/api/status':'/api/status?detail=false');if(prior.data_revision&&JSON.stringify(prior.data_revision)!==JSON.stringify(incoming.data_revision))$kb.core.clearReadCache();$kb.core.state.status={...incoming};if(!incoming.index_health&&prior.active_version===incoming.active_version&&prior.index_health)$kb.core.state.status.index_health=prior.index_health;const s=$kb.core.state.status;for(const [id,value] of [['graph-library-count',s.papers],['graph-external-count',s.external]]){const el=$kb.core.$('#'+id);if(el)el.textContent=(value||0).toLocaleString();}$kb.core.$('#pending-count').textContent=s.pending_approvals||0;$kb.core.$('#last-update').textContent='最近发布 '+$kb.core.fmtDate(s.updated);$kb.core.$('#service-state').textContent=window.STATIC_GRAPH?'离线图谱快照':'本地知识库已连接';$kb.core.$('.rail-foot small').textContent=s.schedule?.enabled?'北京时间 09:00 增量更新':'自动启动任务尚未安装';const publication=$kb.core.$('#publication-state');if(publication)publication.textContent='索引发布：'+$kb.core.fmtDate(s.index_published_at||s.updated)+' · 最近完整更新结束：'+(s.update_finished_at?$kb.core.fmtDate(s.update_finished_at):'尚未记录')+' · 导出：'+({synced:'已同步',running:'同步中',failed:'失败，可单独重试'}[s.export_sync?.status]||'未记录');renderMethodProgress();$kb.feedback.renderIndexProgress();$kb.feedback.renderIndexDetails();$kb.feedback.renderJobRows();renderCostMetric();$kb.feedback.monitorTaskErrors();const finished=priorAnalysis?.job&&priorAnalysis.job===s.analysis_state?.job&&['running','stopping'].includes(priorAnalysis.state)&&!['running','stopping'].includes(s.analysis_state.state);
const countChanged=Boolean(prior.observed_at||prior.data_revision||prior.updated)&&(JSON.stringify(prior.method_relations||null)!==JSON.stringify(s.method_relations||null)||JSON.stringify(prior.relations||null)!==JSON.stringify(s.relations||null));
const versionChanged=Boolean(prior.updated)&&prior.updated!==s.updated;
const pending=Boolean($kb.core.state.deferredRefresh);
if(!countChanged&&!versionChanged&&!finished&&!pending)return;
if($kb.graph.busyUser()){$kb.core.state.deferredRefresh=true;return;}
$kb.core.state.deferredRefresh=false;$kb.graph.hideStaleBanner();
if($kb.core.state.page==='graph'){
  if($kb.core.state.graphMode==='network'){if(versionChanged||finished)await $kb.graph.reloadGraphKeepingPosition();else await $kb.graph.refreshGraphEdges();}
  else if(versionChanged||finished){await $kb.graph.reloadGraphKeepingPosition();$kb.graph.renderOverview(await $kb.core.api('/api/overview'));}
  else $kb.graph.showStaleBanner();
}else if($kb.core.state.page==='chains'){
  if(versionChanged||finished)await $kb.graph.reloadChainsKeepingPosition();else $kb.graph.showStaleBanner();
}}catch(e){$kb.core.$('#service-state').textContent='本地服务未连接';const freshness=$kb.core.$('#index-refresh');if(freshness)freshness.textContent='连接中断，当前显示上次成功观测的数据；正在重试。';}}

function renderMethodProgress(){$kb.feedback.renderVerificationStatus();}

async function statusPage(){
  refreshStatus(true);if($kb.core.state.page!=='status')return;
  const s=$kb.core.state.status;
  $kb.core.$('#main').innerHTML=$kb.core.intro('INDEX HEALTH','知道每一次更新完成到哪里','实时区分可检索来源、已发布向量与新增待处理原文；本轮计算缓存发布后才计入可用向量。')+`
  <div class="toolbar"><p class="small-text muted" id="index-refresh" role="status" style="flex:1"></p><button class="button" id="index-dashboard" type="button" ${window.STATIC_GRAPH?'hidden':''}>查看研究调度台</button><button class="button small" id="index-refresh-now" ${window.STATIC_GRAPH?'disabled':''}>立即刷新</button></div>
  <div class="metrics"><div class="metric"><strong id="library-metric"></strong><small>库内文献</small></div><div class="metric"><strong id="fulltext-metric"></strong><small>全文进入索引的文献</small></div><div class="metric"><strong id="external-metric"></strong><small>已采集库外节点</small></div><div class="metric" id="cost-metric"><strong></strong><small></small></div></div>
  <p id="publication-state" class="small-text muted">索引发布：${$kb.core.esc($kb.core.fmtDate(s.index_published_at||s.updated))} · 最近完整更新结束：${s.update_finished_at?$kb.core.esc($kb.core.fmtDate(s.update_finished_at)):"尚未记录"} · 导出：${({synced:"已同步",running:"同步中",failed:"失败，可单独重试"}[s.export_sync?.status]||"未记录")}</p>
  <div id="index-live" aria-live="polite"></div><p id="index-task" class="small-text muted" aria-live="polite"></p>
  <section class="settings-section" aria-labelledby="vector-actions-title"><h2 id="vector-actions-title">向量索引与完整更新</h2><p class="small-text muted" id="vector-action-status" role="status"></p><p class="small-text muted">完整更新和向量索引统一在研究调度台新建任务，此页显示索引状态。完整更新包含文献同步、OCR、切块、向量及关系分析；向量索引只补齐已有来源。</p></section>
  <section class="settings-section"><h2>统一来源索引</h2><div id="index-health"></div></section>
  <section class="settings-section" id="model-controls" aria-label="本地模型控制"></section>
  <section class="settings-section"><h2>两层网络与任务状态</h2><p id="network-summary" class="small-text muted"></p><p class="small-text muted">自动启动：${s.schedule?.enabled?'已配置':'尚未安装启动任务'} · 时区 Asia/Shanghai · 每天 09:00</p><div class="table-scroll"><table class="status-table"><thead><tr><th>开始</th><th>任务类型</th><th>状态</th><th>进度</th></tr></thead><tbody id="jobs-body"></tbody></table></div></section>
  <section class="settings-section" id="source-inspection" hidden></section>`;
  $kb.feedback.renderIndexProgress();$kb.feedback.renderIndexDetails();$kb.feedback.renderJobRows();renderCostMetric();
  $kb.core.$('#index-dashboard').onclick=()=>$kb.dashboard.taskDashboard.showTask();
  $kb.core.$('#index-refresh-now').onclick=()=>pollWorkbenchStatus();
  if(!window.STATIC_GRAPH){clearTimeout(statusPollTimer);statusPollTimer=setTimeout(pollWorkbenchStatus,2000);}
  if(window.STATIC_GRAPH)$kb.feedback.renderModelControls({items:[]});
  else{try{$kb.feedback.renderModelControls(await $kb.core.api('/api/models'));}catch(error){$kb.feedback.showError(error.message,{title:'无法读取本地模型状态'});}}
}

async function pollWorkbenchStatus(){
  if(window.STATIC_GRAPH||statusPolling)return;
  clearTimeout(statusPollTimer);statusPolling=true;
  try{
    await refreshStatus();
    if($kb.core.state.page==='status'&&!document.hidden){try{const models=await $kb.core.api('/api/models');if($kb.core.state.page==='status')$kb.feedback.renderModelControls(models);}catch(error){$kb.feedback.showError(error.message,{title:'无法读取本地模型状态'});}}
  }finally{statusPolling=false;statusPollTimer=setTimeout(pollWorkbenchStatus,$kb.feedback.statusPollDelay($kb.core.state.status,$kb.core.state.page,document.hidden));}
}

function renderCostMetric(){
  const host=$kb.core.$('#cost-metric');if(!host)return;const s=$kb.core.state.status;
  host.querySelector('strong').textContent=s.today_cost_known===false?'未计价':'¥'+(s.today_cost||0);
  host.querySelector('small').textContent=s.today_cost_known===false?'今日 token：输入 '+(s.today_input_tokens||0)+' / 输出 '+(s.today_output_tokens||0)+' · '+(s.budget_mode==='unlimited'?'暂不限额':'按限额'):'今日模型已用 / 预留费用';
}
return {get statusRequest(){return statusRequest;},set statusRequest(value){statusRequest=value;},
get statusPollTimer(){return statusPollTimer;},set statusPollTimer(value){statusPollTimer=value;},
get statusPolling(){return statusPolling;},set statusPolling(value){statusPolling=value;},
get refreshStatus(){return refreshStatus;},set refreshStatus(value){refreshStatus=value;},
get refreshStatusNow(){return refreshStatusNow;},set refreshStatusNow(value){refreshStatusNow=value;},
get renderMethodProgress(){return renderMethodProgress;},set renderMethodProgress(value){renderMethodProgress=value;},
get statusPage(){return statusPage;},set statusPage(value){statusPage=value;},
get pollWorkbenchStatus(){return pollWorkbenchStatus;},set pollWorkbenchStatus(value){pollWorkbenchStatus=value;},
get renderCostMetric(){return renderCostMetric;},set renderCostMetric(value){renderCostMetric=value;}};
});
