/* feedback：通过 ctx 访问其他模块；变量保留在本模块闭包内。 */
KB.define('feedback',($kb)=>{'use strict';
let feedbackQueue=[];

let activeFeedback=null;

let feedbackOrigin=null;

let modelPollTimer=null;

let verificationTimer=null;

const feedbackSeen=new Set();

try{for(const key of JSON.parse(sessionStorage.getItem('kb-dismissed-errors')||'[]'))feedbackSeen.add(key);}catch(e){}

function parseIssueText(p){if(!p)return '';const total=(p.pending||0)+(p.failed||0)+(p.repaired||0)+(p.archived||0);return total?`解析问题：待处理 ${p.pending||0} 页 · 失败 ${p.failed||0} 页 · 已修复 ${p.repaired||0} 项 · 历史保留 ${p.archived||0} 项`:'';}

function feedbackKey(text){let n=2166136261;for(const c of String(text))n=Math.imul(n^c.charCodeAt(0),16777619);return String(n>>>0);}

function formatErrorDetail(detail){try{const data=JSON.parse(detail);return typeof data.error==='string'?data.error:data.error?.message||data.message||detail;}catch(e){return String(detail);}}

function showError(message,options={}){
  const detail=options.detail||String(message||'操作未完成'),key=options.key||feedbackKey((options.title||'')+detail);
  if(!options.manual&&(feedbackSeen.has(key)||activeFeedback?.key===key||feedbackQueue.some(e=>e.key===key)))return;
  feedbackQueue.push({message:String(message||'操作未完成'),detail,title:options.title||'操作未完成',key});openNextError();
}

function openNextError(){
  const dialog=document.querySelector('#error-dialog');
  if(!dialog||dialog.open||document.querySelector('#evidence-dialog')?.open||!feedbackQueue.length)return;
  activeFeedback=feedbackQueue.shift();feedbackOrigin=document.activeElement;
  document.querySelector('#error-title').textContent=activeFeedback.title;
  document.querySelector('#error-summary').textContent=activeFeedback.message;
  document.querySelector('#error-details').textContent=formatErrorDetail(activeFeedback.detail);
  document.querySelector('#error-technical').open=false;
  const c=$kb.core.state.status.index_health||$kb.core.state.status.coverage||{};
  document.querySelector('#error-impact').textContent=c.sources&&c.vector_pending===0?
    `向量索引已完成，共 ${c.sources.toLocaleString()} 个来源单元。完成进度与已有检索会保留。`:
    '已完成的缓存和上一可用索引会保留。';
  dialog.showModal();document.querySelector('#error-dismiss').focus();
}

function initFeedback(){
  const dialog=document.querySelector('#error-dialog');
  document.querySelector('#error-dismiss').onclick=()=>dialog.close();
  document.querySelector('#error-close').onclick=()=>dialog.close();
  dialog.onclick=e=>{if(e.target===dialog)dialog.close();};
  dialog.addEventListener('close',()=>{
    if(activeFeedback)feedbackSeen.add(activeFeedback.key);
    try{sessionStorage.setItem('kb-dismissed-errors',JSON.stringify([...feedbackSeen].slice(-100)));}catch(e){}
    activeFeedback=null;if(feedbackOrigin?.isConnected)feedbackOrigin.focus();openNextError();
  });
  document.querySelector('#evidence-dialog').addEventListener('close',openNextError);
}

function taskErrorEvents(s){
  const model=s.local_model_state||{},setup=s.local_setup||{},control=s.model_control||{};
  if(control.state==='running'&&control.action==='load'&&control.role==='text')return [];
  if(model.state==='failed')return [{key:'model:'+feedbackKey(model.updated+model.error),title:'关系核验模型未能启动',
    message:/allocate|CUDA_Host|memory/i.test(model.error||'')?'本地关系核验模型无法分配所需内存，加载已停止。':'本地关系核验模型暂不可用，任务结果与缓存已保留。',detail:model.error||setup.message||'未返回详细原因'}];
  if(control.state==='failed')return [{key:'control:'+control.id,title:'模型操作未完成',message:'模型操作未能完成，可查看详情后重试。',detail:control.error||'未返回详细原因'}];
  if(setup.state==='failed'&&!['running','ready','stopped'].includes(model.state))return [{key:'setup:'+feedbackKey(setup.updated+setup.message),title:'原文处理暂未完成',message:'原文处理暂未完成，向量索引结果会保留。',detail:setup.message}];
  const analysis=s.analysis_state||{};
  if(analysis.job&&['failed','deferred'].includes(analysis.state))return [{key:'verification:'+analysis.job,title:'研究关系核验暂未完成',message:'本轮核验已暂缓，已完成的证据与缓存会保留。',detail:analysis.error||analysis.reason||'请检查模型与费用设置'}];
  const job=(s.jobs||[]).find(j=>['update','vectors'].includes(j.kind));
  if(['failed','partial'].includes(job?.status)&&(!s.updated||job.started>s.updated))return [{key:'job:'+job.id,title:job.kind==='vectors'?'向量索引未完成':'知识库更新未完成',message:'本次任务未能全部完成，仍可使用上一可用版本并复用缓存续算。',detail:job.error||job.progress}];
  return [];
}

function monitorTaskErrors(){for(const event of taskErrorEvents($kb.core.state.status))showError(event.message,event);}

function indexProgressView(s){
  const i=s.indexing||{},c=s.index_health||s.coverage||{},live=i.state==='running'&&['vectors','publishing','awaiting_publication'].includes(i.phase);
  const total=live?i.total:c.sources||0,completed=live?i.completed:c.embedding_compatible===false?(c.usable_vector_count||0):(c.vector_count||0);
  if(!total)return null;
  const done=completed>=total;
  return {total,completed,percent:Math.min(100,100*completed/total),done,live,
    title:live?(i.phase==='vectors'?'正在计算向量':'正在发布索引'):c.embedding_compatible===false?'旧向量保留，当前权重待索引':done?(s.index_health?'向量索引已完成':'已发布版本向量已就绪'):c.live_vector_pending?'新原文待向量化':'向量索引待续算',
    note:live?`其中复用 ${(i.reused||0).toLocaleString()}，本轮新完成 ${(i.new_completed||0).toLocaleString()}，已缓存待发布 ${(i.cached_unpublished||0).toLocaleString()}。当前检索仍使用已发布版本，发布后自动更新覆盖率。`:
      c.embedding_compatible===false?c.vector_warning:done?'当前发布版本的全部向量已就绪，可检索原文、图表、公式与笔记。':`当前可检索 ${total.toLocaleString()} 个来源单元，已发布向量 ${completed.toLocaleString()}；待补 ${(total-completed).toLocaleString()}，其中新识别原文 ${(c.live_vector_pending||0).toLocaleString()}。现有索引与缓存已保留。`};
}

function indexProgressHTML(s){
  const p=indexProgressView(s),local=s.local_setup||{},model=s.local_model_state||{};
  let html=p?`<section class="index-progress ${p.done&&!p.live?'is-complete':''}" aria-labelledby="index-progress-title"><div class="progress-heading"><strong id="index-progress-title">${$kb.core.esc(p.title)}</strong><span class="badge ${p.done&&!p.live?'good':''}">${p.live?'本轮任务':p.done?'已发布':'待处理'}</span></div><div class="progress-number">${p.completed.toLocaleString()} <span>/ ${p.total.toLocaleString()}</span><b>${p.percent.toFixed(1)}%</b></div><div class="progress-track" role="progressbar" aria-label="向量索引进度" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${p.percent.toFixed(1)}"><i style="width:${p.percent}%"></i></div><p>${$kb.core.esc(p.note)}</p>${!p.live&&s.updated?`<small>最近发布 ${$kb.core.esc($kb.core.fmtDate(s.updated))}</small>`:''}</section>`:'';
  if(model.state==='failed'||local.state==='failed')html+='<div class="task-followup"><span>关系核验暂缓</span><button class="button small" id="view-model-error">查看详情</button></div>';
  else if(s.relation_pipeline?.local_enabled)html+=`<p class="small-text muted">${$kb.core.esc(verificationWorkflow(s.relation_pipeline))}</p>`;
  else if(local.message)html+=`<p class="small-text muted">${$kb.core.esc(local.message)}</p>`;
  return html;
}

function renderIndexProgress(){
  const host=document.querySelector('#index-live');if(!host)return;const html=indexProgressHTML($kb.core.state.status);if(host.innerHTML!==html)host.innerHTML=html;
  const button=document.querySelector('#view-model-error');if(button)button.onclick=()=>{
    const event=taskErrorEvents($kb.core.state.status)[0];if(event)showError(event.message,{...event,manual:true});
  };
}

function indexHealthHTML(s){
  const h=s.index_health||{},c=s.coverage||{},i=s.indexing||{},format=n=>Number(n||0).toLocaleString('zh-CN');
  if(!s.index_health)return '<div class="callout warn">后台仍在运行旧版索引统计，请重启工作台服务后查看实时明细。当前仅能确认已发布版本，新增来源数量尚未返回。</div>';
  const rows=h.by_kind||Object.entries(c.by_kind||{}).map(([kind,sources])=>({kind,sources,published_sources:sources}));
  const column=(row,key)=>Number.isFinite(row[key])?format(row[key]):'—';
  const cached=i.state==='running'&&i.by_kind;
  return `<div class="metrics index-metrics"><div class="metric"><strong>${format(h.sources??c.sources)}</strong><small>当前可检索来源</small></div><div class="metric"><strong>${format(h.vector_count??c.vector_count)}</strong><small>已发布向量</small></div><div class="metric"><strong>${format(h.vector_pending??c.vector_pending)}</strong><small>待向量化来源</small></div><div class="metric"><strong>${format(h.live_vector_pending)}</strong><small>其中新增识别原文</small></div></div><p class="small-text muted">已发布来源 ${format(h.published_sources??c.sources)}，其中待补向量 ${format(h.published_pending??c.vector_pending)}。新增原文已参与关键词检索，增量向量发布后纳入融合检索。</p><div class="table-scroll"><table class="status-table"><thead><tr><th>来源类型</th><th>可检索单元</th><th>已发布向量</th><th>已发布待补</th><th>新增待补</th><th>本轮已缓存</th></tr></thead><tbody>${rows.map(r=>`<tr><th scope="row">${$kb.core.esc($kb.core.sourceNames[r.kind]||r.kind)}</th><td>${column(r,'sources')}</td><td>${column(r,'vectors')}</td><td>${column(r,'published_pending')}</td><td>${column(r,'live_pending')}</td><td>${cached?format(cached[r.kind]):'—'}</td></tr>`).join('')}</tbody></table></div><p class="small-text muted">向量模型 ${$kb.core.esc(h.embedding?.model||'—')} · ${format(h.embedding?.dims)} 维 · 当前发布版本 ${$kb.core.esc(h.version||s.active_version||'尚未发布')}</p><p class="small-text muted">查询向量缓存：就绪 ${format(s.query_vectors?.ready)}，排队 ${format(s.query_vectors?.queued)}，暂不可用 ${format(s.query_vectors?.unavailable)}。查询缓存不计入来源向量数量。</p>`;
}

function renderIndexDetails(){
  const s=$kb.core.state.status,c=s.coverage||{},h=s.index_health||{},i=s.indexing||{};
  renderVectorAction();
  const host=document.querySelector('#index-health');if(!host)return;
  const html=indexHealthHTML(s);if(host.innerHTML!==html)host.innerHTML=html;
  const freshness=document.querySelector('#index-refresh');if(freshness)freshness.textContent=window.STATIC_GRAPH?'离线快照，不自动刷新':!s.index_health?'后台统计待更新，请重启工作台服务':`每2秒自动刷新 · 最近观测 ${s.observed_at?new Date(s.observed_at).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'等待数据'}`;
  const task=document.querySelector('#index-task');if(task)task.textContent=i.state==='running'?`当前${i.kind==='vectors'?'向量索引':'完整更新'}任务：${jobProgressText(i.progress)||'正在准备'}${i.updated?' · 更新 '+$kb.core.fmtDate(i.updated):''}`:(h.vector_pending?'可在研究调度台安排向量索引，补齐已有来源的缺失向量。':'当前没有索引任务运行。');
  for(const [id,value] of [['#library-metric',s.papers||0],['#fulltext-metric',c.fulltext_papers||0],['#external-metric',s.external||0]]){const el=document.querySelector(id);if(el)el.textContent=value.toLocaleString('zh-CN');}
  const network=document.querySelector('#network-summary');if(network)network.textContent=`已完成节点采集 ${s.frontier?.complete||0} 项 · 继续排队 ${s.frontier?.pending||0} 项 · 待审批全文 ${s.pending_approvals||0} 篇`;
  const warnings=document.querySelector('#source-inspection');if(warnings){const current=c.warnings||[],archive=c.warnings_archive||[];const issueText=parseIssueText(s.parse_issues);const issueLine=issueText?`<p class="small-text muted">${$kb.core.esc(issueText)}</p>`:'';const archiveLine=archive.length?`<p class="small-text muted">另有 ${archive.length} 项历史来源警告已归档，<button class="link-button" id="archive-warnings">查看归档记录</button>（不计入当前待办）。</p>`:'';const content=(current.length?`<div class="section-heading"><h2>来源检查</h2><button class="button small" id="source-warnings">查看 ${current.length} 项详情</button></div><p class="small-text muted">部分来源仍需处理，已发布的索引可继续使用。</p>${archiveLine}`:archiveLine)+issueLine;if(warnings.innerHTML!==content)warnings.innerHTML=content;warnings.hidden=!content;const button=document.querySelector('#source-warnings');if(button)button.onclick=()=>showError('部分来源未能完成处理，已发布结果会保留。',{title:'来源检查详情',detail:($kb.core.state.status.coverage.warnings||[]).join('\n'),manual:true});const archived=document.querySelector('#archive-warnings');if(archived)archived.onclick=()=>showError('历史来源警告已归档，不属于当前待办。',{title:'历史来源警告归档',detail:(($kb.core.state.status.coverage.warnings_archive||[]).map(w=>typeof w==='string'?w:`[${w.archived_at||''}] ${w.reason||''}：${w.warning}`).join('\n')),manual:true});}
}

function vectorActionView(s,starting=false,offline=false){
  const i=s.indexing||{},h=s.index_health||s.coverage||{},running=i.state==='running';
  const label=running?(i.kind==='vectors'?'向量索引进行中':'完整更新进行中'):starting?'正在启动…':h.vector_count&&h.vector_pending?'继续向量索引':'开始向量索引';
  const blocked=['running','stopping'].includes(s.analysis_state?.state)||s.local_model_state?.state==='running'||s.recognition_state?.state==='running';
  return {label,disabled:offline||starting||running||blocked||!s.index_actions?.vectors,
    note:offline?'离线快照不能启动任务，请打开本地工作台。':running?(i.kind==='vectors'?'仅补齐本地向量：':'完整更新正在进行：')+jobProgressText(i.progress):starting?'正在提交本地向量任务…':blocked?'原文识别或研究核验正在运行，完成或停止后可补齐向量。':!s.index_actions?.vectors?'新的独立向量接口需在当前任务结束后重启后台载入。':'只使用已收录来源，复用向量缓存；不采集全文、不执行OCR或切块、不联网扩展、不调用云端分析。'};
}

function renderVectorAction(){
  const note=document.querySelector('#vector-action-status');if(!note)return;
  note.textContent=vectorActionView($kb.core.state.status,false,Boolean(window.STATIC_GRAPH)).note;
}

function jobProgressText(progress=''){
  return {'chunk.py':'正在切分PDF文本（尚未进入向量计算）','extract_figures.py':'正在提取图表','extract_formulas.py':'正在提取公式','extract_refs.py':'正在提取参考文献'}[progress]||progress;
}

function statusPollDelay(s,page,hidden=false){return hidden?30000:page==='status'||s.indexing?.state==='running'?2000:30000;}

function renderModelControls(data){
  const host=document.querySelector('#model-controls');if(!host)return;
  if(window.STATIC_GRAPH){host.innerHTML='<p class="small-text muted">模型控制请打开本地工作台。</p>';return;}
  const signature=JSON.stringify([data,$kb.core.state.status.last_local_model_error?.error]);
  if(host.dataset.modelSignature===signature)return;
  host.dataset.modelSignature=signature;
  const control=data.control||{},busy=control.state==='running',names={embedding:'向量模型',ocr:'图文识别',text:'关系核验'};
  host.innerHTML=`<div class="section-heading"><h2>本地模型</h2><button class="button small" data-model-stop="all">停止本应用模型</button></div><p class="small-text muted">任务按需加载模型，完成后释放或在空闲时自动释放。手动停止会保留已完成的缓存。</p><div class="model-list">${(data.items||[]).map(p=>{
    const loading=busy&&control.action==='load'&&control.role===p.role;
    return `<div class="model-row"><div><strong>${names[p.role]}</strong><small>${$kb.core.esc(p.model)}</small></div><span class="badge ${p.loaded?'good':loading?'warn':''}">${p.loaded?'已加载':loading?'加载中':p.installed===false?'等待下载完成':'未加载'}</span><div class="model-actions"><button class="button small" data-model-load="${p.role}" aria-label="加载${names[p.role]}" ${busy||p.loaded||p.installed===false?'disabled':''}>加载</button><button class="button small" data-model-stop="${p.role}" aria-label="停止${names[p.role]}" ${!p.loaded&&!loading?'disabled':''}>停止</button></div></div>`;
  }).join('')}</div><p class="small-text muted" aria-live="polite">${busy?(control.action==='load'?'正在加载模型…':'正在停止模型…'):''}</p>${$kb.core.state.status.last_local_model_error?.error?'<button class="button small" id="model-last-error">上次模型异常详情</button>':''}`;
  host.querySelectorAll('[data-model-load]').forEach(b=>b.onclick=()=>modelAction('load',b.dataset.modelLoad));
  host.querySelectorAll('[data-model-stop]').forEach(b=>b.onclick=()=>modelAction('stop',b.dataset.modelStop));
  const history=host.querySelector('#model-last-error');if(history)history.onclick=()=>showError('这是上次模型加载的记录，当前运行状态以模型列表为准。',{title:'上次模型异常详情',detail:$kb.core.state.status.last_local_model_error.error,manual:true});
}

async function modelAction(action,role){
  try{await $kb.core.api('/api/models',{action,role});await $kb.status.refreshStatus();renderModelControls(await $kb.core.api('/api/models'));pollModelOperation();}
  catch(error){showError(error.message,{title:action==='load'?'模型未能加载':'模型未能停止'});}
}

function pollModelOperation(){
  clearTimeout(modelPollTimer);
  modelPollTimer=setTimeout(async()=>{
    try{
      const data=await $kb.core.api('/api/models');
      await $kb.status.refreshStatus();renderModelControls(data);
      if(data.control?.state==='running')pollModelOperation();
    }catch(error){showError(error.message,{title:'无法读取本地模型状态'});}
  },2000);
}

function jobRowsHTML(jobs){
  const names={queued:'等待开始',complete:'已完成',partial:'部分完成',running:'运行中',failed:'失败',interrupted:'已停止',deferred:'待续跑',awaiting_configuration:'待配置',paused:'已暂停'};
  const kinds={update:'完整更新知识库',vectors:'向量索引',relations:'研究关系核验',model:'本地模型操作'};
  return jobs.map(j=>`<tr><td>${$kb.core.fmtDate(j.started)}</td><td>${$kb.core.esc(kinds[j.kind]||'后台任务')}</td><td><span class="badge ${j.status==='complete'?'good':j.status==='failed'?'bad':'warn'}">${$kb.core.esc(names[j.status]||j.status)}</span></td><td>${j.error||['failed','deferred','partial'].includes(j.status)?`<span>任务待处理</span> <button class="button small" data-job-error="${$kb.core.esc(j.id)}">查看详情</button>`:$kb.core.esc(jobProgressText(j.progress))}</td></tr>`).join('');
}

function renderJobRows(){const host=document.querySelector('#jobs-body');if(!host)return;host.innerHTML=jobRowsHTML($kb.core.state.status.jobs||[]);host.querySelectorAll('[data-job-error]').forEach(button=>button.onclick=()=>{const job=$kb.core.state.status.jobs.find(j=>j.id===button.dataset.jobError);showError('该次任务未完成，已保留可用索引与缓存。',{title:'任务详情',detail:job.error||job.progress,manual:true,key:'job:'+job.id});});}

function verificationScope(filters={}){
  return [filters.project?'当前课题':'全库',filters.direction?$kb.core.dirs[filters.direction]:'全部研究方向',filters.q?'关键词“'+filters.q+'”':''].filter(Boolean).join(' · ');
}

function verificationWorkflow(pipeline={}){return pipeline.local_enabled===false?'使用已发布原文进行云端分析与复核；缺少全文时列入审批。':'GLM-OCR识别原文 → '+(pipeline.local_verifier||'本地模型')+'核验关系 → '+(pipeline.cloud_reviewer||'云端模型')+'独立复核。包括尚未形成研究链的文献，缺少全文时列入审批。';}

function verificationProgressView(a){
  const preparing=a.stage==='recognition'&&Number(a.ocr_papers_total)>0;
  const done=preparing?(a.ocr_papers_done||0):(a.completed||0),total=preparing?a.ocr_papers_total:(a.total||0);
  return {preparing,done,total,percent:total?Math.min(100,100*done/total):a.state==='complete'?100:0,
    label:preparing?'原文准备阶段进度':'研究关系核验进度'};
}

function verificationPanelHTML(){
  return '<section id="relation-verification" class="verification-card" aria-label="研究关系核验状态"><div class="verification-heading"><strong>研究链与关系核验</strong><button id="verification-task" class="button small">查看调度台</button></div><p id="verification-scope" class="small-text muted"></p><p id="verification-workflow" class="small-text muted"></p><div id="verification-progress" hidden><div class="progress-track" role="progressbar" aria-label="研究关系核验进度" aria-valuemin="0" aria-valuemax="100"><i></i></div><p id="verification-result" class="small-text"></p></div><p id="method-progress" class="small-text muted" aria-live="polite"></p></section>';
}

function renderVerificationStatus(){
  const a=$kb.core.state.status.analysis_state||{},active=['running','stopping'].includes(a.state);
  const host=document.querySelector('#relation-verification');if(!host)return;
  if(active&&!window.STATIC_GRAPH)pollVerification();
  host.querySelector('#verification-workflow').textContent=verificationWorkflow($kb.core.state.status.relation_pipeline);
  host.querySelector('#verification-scope').textContent=a.job?'任务范围：'+verificationScope(a.filters):'尚未安排核验任务';
  const link=host.querySelector('#verification-task');link.hidden=Boolean(window.STATIC_GRAPH);
  link.textContent=a.job?'查看核验任务':'前往研究调度台';
  link.onclick=()=>$kb.dashboard.taskDashboard.showTask(a.job?'job:'+a.job:'');
  const progress=host.querySelector('#verification-progress');progress.hidden=!a.job;
  if(a.job){
    const view=verificationProgressView(a),bar=progress.querySelector('[role=progressbar]');
    bar.setAttribute('aria-label',view.label);bar.setAttribute('aria-valuenow',view.percent.toFixed(1));bar.querySelector('i').style.width=view.percent+'%';
    host.querySelector('#verification-result').textContent=view.preparing?
      '原文准备（含 OCR）：'+view.done+' / '+view.total+' 篇 · 关系核验 '+(a.completed||0)+' / '+(a.total||0)+' 篇':
      (active?'正在核验：':'上次核验：')+(a.completed||0)+' / '+(a.total||0)+' 篇 · 本轮处理 '+(a.processed||0)+' 篇 · 缓存复用 '+(a.reused||0)+' 篇'+(active?'':' · 新增已核 '+(a.confirmed_added||0)+' 条 / 候选 '+(a.candidates_added||0)+' 条');
  }
  const names={running:'正在核验，执行进度见研究调度台。',stopping:'正在停止；完成的证据与缓存会保留。',complete:'上次核验已结束，结果已同步到图谱和专题。',interrupted:'核验已停止，已完成结果保留。',paused:'核验已暂停。',deferred:'本轮核验暂缓，已完成结果保留。',failed:'本轮核验未完成，已完成结果保留。'};
  host.querySelector('#method-progress').textContent=window.STATIC_GRAPH?'离线核验快照。任务操作请打开本地研究调度台。':(a.job?names[a.state]||'已记录核验任务。':'新建任务、停止和续跑统一在研究调度台操作。');
}

function pollVerification(){
  if(verificationTimer)return;
  verificationTimer=setTimeout(async()=>{verificationTimer=null;await $kb.status.refreshStatus();},2000);
}
return {get feedbackQueue(){return feedbackQueue;},set feedbackQueue(value){feedbackQueue=value;},
get activeFeedback(){return activeFeedback;},set activeFeedback(value){activeFeedback=value;},
get feedbackOrigin(){return feedbackOrigin;},set feedbackOrigin(value){feedbackOrigin=value;},
get modelPollTimer(){return modelPollTimer;},set modelPollTimer(value){modelPollTimer=value;},
get verificationTimer(){return verificationTimer;},set verificationTimer(value){verificationTimer=value;},
get feedbackSeen(){return feedbackSeen;},
get feedbackKey(){return feedbackKey;},set feedbackKey(value){feedbackKey=value;},
get formatErrorDetail(){return formatErrorDetail;},set formatErrorDetail(value){formatErrorDetail=value;},
get showError(){return showError;},set showError(value){showError=value;},
get openNextError(){return openNextError;},set openNextError(value){openNextError=value;},
get initFeedback(){return initFeedback;},set initFeedback(value){initFeedback=value;},
get taskErrorEvents(){return taskErrorEvents;},set taskErrorEvents(value){taskErrorEvents=value;},
get monitorTaskErrors(){return monitorTaskErrors;},set monitorTaskErrors(value){monitorTaskErrors=value;},
get parseIssueText(){return parseIssueText;},set parseIssueText(value){parseIssueText=value;},
get indexProgressView(){return indexProgressView;},set indexProgressView(value){indexProgressView=value;},
get indexProgressHTML(){return indexProgressHTML;},set indexProgressHTML(value){indexProgressHTML=value;},
get renderIndexProgress(){return renderIndexProgress;},set renderIndexProgress(value){renderIndexProgress=value;},
get indexHealthHTML(){return indexHealthHTML;},set indexHealthHTML(value){indexHealthHTML=value;},
get renderIndexDetails(){return renderIndexDetails;},set renderIndexDetails(value){renderIndexDetails=value;},
get vectorActionView(){return vectorActionView;},set vectorActionView(value){vectorActionView=value;},
get renderVectorAction(){return renderVectorAction;},set renderVectorAction(value){renderVectorAction=value;},
get jobProgressText(){return jobProgressText;},set jobProgressText(value){jobProgressText=value;},
get statusPollDelay(){return statusPollDelay;},set statusPollDelay(value){statusPollDelay=value;},
get renderModelControls(){return renderModelControls;},set renderModelControls(value){renderModelControls=value;},
get modelAction(){return modelAction;},set modelAction(value){modelAction=value;},
get pollModelOperation(){return pollModelOperation;},set pollModelOperation(value){pollModelOperation=value;},
get jobRowsHTML(){return jobRowsHTML;},set jobRowsHTML(value){jobRowsHTML=value;},
get renderJobRows(){return renderJobRows;},set renderJobRows(value){renderJobRows=value;},
get verificationScope(){return verificationScope;},set verificationScope(value){verificationScope=value;},
get verificationWorkflow(){return verificationWorkflow;},set verificationWorkflow(value){verificationWorkflow=value;},
get verificationProgressView(){return verificationProgressView;},set verificationProgressView(value){verificationProgressView=value;},
get verificationPanelHTML(){return verificationPanelHTML;},set verificationPanelHTML(value){verificationPanelHTML=value;},
get renderVerificationStatus(){return renderVerificationStatus;},set renderVerificationStatus(value){renderVerificationStatus=value;},
get pollVerification(){return pollVerification;},set pollVerification(value){pollVerification=value;}};
});
