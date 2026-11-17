/* dashboard：通过 ctx 访问其他模块；变量保留在本模块闭包内。 */
KB.define('dashboard',($kb)=>{'use strict';
let researchTimer=null;

const taskDashboard=(()=>{
  const names={ended:'阶段已结束',unsupported:'证据反驳',queued:'排队中',running:'执行中',stopping:'正在停止',interrupted:'已停止',paused:'已暂停',partial:'处理结束，部分完成',blocked:'受阻待处理',failed:'执行失败',complete:'已完成',deferred:'等待复核',waiting_resource:'等待模型资源',waiting_approval:'等待全文审批',cached:'缓存复用',idle:'等待下一阶段',not_recorded:'未记录',candidate:'待核提议',ready:'补证就绪',not_applicable:'无关系线索',structure_error:'关系结构错误',verified:'核验通过',conflict:'判断冲突',insufficient:'证据不足',execution_error:'核验失败'};
  const icons={coordinator:'git-branch',reader:'book',retriever:'search',lineage_researcher:'chart-dots',writer:'file-text',verifier:'shield-check',cloud_reviewer:'cloud-check',ocr:'scan',indexer:'database'};
  const icon=(name)=>`<img class="dash-icon" src="${$kb.core.iconURL(name)}" alt="" width="18" height="18">`;
  const kinds={maintenance:'全文理解与笔记维护',lineage:'方法谱系补全',verification:'研究链与关系核验',update:'完整更新知识库',vectors:'向量索引'};
  const normalizeKind=k=>['verify','relations'].includes(k)?'verification':k;
  let selected='',selectedKind='',historyMode=false,draft=null,pendingId=null,cursor=0,events=[],snapshot=null,busy=false,epoch=0,roleFilter='',taskFilter='all',controlBusy=false,awaitingSnapshot=false;
  const expandedPhases=new Set(),scopeDrafts=new Map();
  const currentTask=()=>draft?null:snapshot?.selected;
  function primaryAction(t){
    if(t?.status==='stopping')return {label:'正在停止',disabled:true};
    if(t?.actions?.includes('stop'))return {label:'停止',action:'stop'};
    if(t?.actions?.includes('resume'))return {label:'续跑',action:'resume'};
    if(t&&['queued','running'].includes(t.status))return {label:'执行中',disabled:true};
    return {label:'开始任务',action:'start',disabled:!kinds[selectedKind]};
  }
  function scopeText(value={}){
    if(value.paper_ids?.length)return '指定文献：'+(value.paperLabel||value.paper_ids.join('、'))+' · 仅全文理解';
    if(['update','vectors'].includes(selectedKind))return selectedKind==='update'?'全库增量更新':'已有来源 · 补齐向量';
    const f=value.filters||value;
    return [f.project?'仅当前课题':'全库',f.direction?($kb.core.dirs[f.direction]||f.direction):'',f.q?'关键词：'+f.q:''].filter(Boolean).join(' · ');
  }
  function currentScope(){const t=currentTask();return draft|| (t?{filters:t.filters||t.control?.filters||{},paper_ids:t.paper_ids,stages:t.stages}:scopeDrafts.get(selectedKind)||{filters:{project:true}});}
  function resetView(){cursor=0;events=[];roleFilter='';expandedPhases.clear();++epoch;clearTimeout(researchTimer);}
  async function chooseKind(kind){selectedKind=kind;try{localStorage.setItem('research-selected-kind',kind);}catch{}historyMode=false;draft=null;remember('');try{localStorage.setItem('research-selected-kind',kind);}catch{}resetView();snapshot=null;renderToolbar();$kb.core.$('#dash-workspace').innerHTML=$kb.core.loading();await refresh(epoch);}
  function beginDraft(value={}){
    if(controlBusy||creating)return;
    const kind=normalizeKind(value.kind||selectedKind||'maintenance');
    if(!kinds[kind])return;
    selectedKind=kind;try{localStorage.setItem('research-selected-kind',kind);}catch{}historyMode=false;draft={...value,kind,filters:{project:true,...(scopeDrafts.get(kind)?.filters||{}),...(value.filters||{})}};
    roleFilter='';expandedPhases.clear();const options=$kb.core.$('#dash-scope-options');if(options)options.dataset.scopeKey='';renderToolbar();renderTask();
  }
  const status=x=>names[x]||x||'未记录';
  const taskStatus=t=>t.attention?.label||status(t.status);
  const duration=x=>x==null?'未记录':x<60?`${x} 秒`:`${Math.floor(x/60)} 分 ${x%60} 秒`;
  const number=x=>x==null?'—':Number(x).toLocaleString('zh-CN');
  function remember(id){selected=id;try{localStorage.setItem('research-selected-task',id);}catch{}}
  function badge(value,label){return `<span class="dash-badge is-${$kb.core.esc(value)}"><i></i>${$kb.core.esc(label||status(value))}</span>`;}
  function shell(){
    $kb.core.$('#main').innerHTML=`<div class="dashboard"><div class="dash-toolbar dash-command"><div class="dash-command-left"><label class="sr-only" for="dash-kind">任务类型</label><select id="dash-kind" aria-label="任务类型"><option value="" disabled>选择任务类型</option>${Object.entries(kinds).map(([value,title])=>`<option value="${value}">${title}</option>`).join('')}</select><details id="dash-scope-menu" class="dash-scope-menu"><summary id="dash-scope-label">范围设置</summary><div id="dash-scope-options"></div></details></div><div class="dash-command-right"><span id="dash-top-status" role="status"></span><button class="button small primary" id="dash-primary" disabled>读取状态</button><button class="dash-text-button" id="dash-fresh">另开任务</button><button class="button small" id="dash-history-open">历史记录</button><button class="button small" id="dash-library">文献概览</button></div></div><div id="dash-top-note"></div><div id="dash-connection" role="status" hidden></div><div id="dash-workspace">${$kb.core.loading()}</div><dialog class="dash-history" id="dash-history" aria-labelledby="dash-history-title"><div class="dash-history-heading"><h2 id="dash-history-title">历史记录</h2><button class="button small" id="dash-history-close">关闭</button></div><p class="small-text muted">按需查看往期任务与执行结果</p><select id="dash-task-filter" aria-label="筛选历史任务"><option value="all">全部任务</option><option value="active">正在进行</option><option value="attention">需要关注</option><option value="complete">已完成</option></select><div id="dash-task-list"></div></dialog></div>`;
    $kb.core.$('#dash-kind').onchange=e=>chooseKind(e.target.value);
    $kb.core.$('#dash-task-filter').value=taskFilter;$kb.core.$('#dash-task-filter').onchange=e=>{taskFilter=e.target.value;renderList();};
    $kb.core.$('#dash-primary').onclick=runPrimary;
    $kb.core.$('#dash-fresh').onclick=()=>beginDraft();
    $kb.core.$('#dash-history-open').onclick=()=>$kb.core.$('#dash-history').showModal();
    $kb.core.$('#dash-history-close').onclick=()=>$kb.core.$('#dash-history').close();
    $kb.core.$('#dash-history').onclick=e=>{if(e.target===$kb.core.$('#dash-history'))e.target.close();};
    $kb.core.$('#dash-library').onclick=showOverview;
  }
  function renderToolbar(){
    if(!$kb.core.$('#dash-kind'))return;
    const t=currentTask(),action=primaryAction(t),scope=currentScope(),locked=Boolean(t&&!draft);
    $kb.core.$('#dash-kind').value=kinds[selectedKind]?selectedKind:'';
    $kb.core.$('#dash-kind').disabled=controlBusy||creating;
    const primary=$kb.core.$('#dash-primary');primary.textContent=controlBusy||creating?'正在提交…':action.label;primary.disabled=controlBusy||creating||Boolean(action.disabled)||!snapshot||awaitingSnapshot;
    $kb.core.$('#dash-fresh').hidden=Boolean(draft)||!kinds[selectedKind];$kb.core.$('#dash-fresh').disabled=controlBusy||creating||Boolean(t&&['queued','running','stopping'].includes(t.status));
    $kb.core.$('#dash-top-status').innerHTML=t?badge(t.status,t.attention?.label):'<span class="muted">待开始</span>';
    const label=$kb.core.$('#dash-scope-label');label.textContent=scopeText(scope);label.title=label.textContent;
    const options=$kb.core.$('#dash-scope-options');
    // 轮询只更新状态，不替换正在编辑的范围表单。
    const key=selectedKind+'|'+(t?.id||'draft');
    if(options.dataset.scopeKey!==key){
      options.dataset.scopeKey=key;
      const fixed=['update','vectors'].includes(selectedKind)||Boolean(scope.paper_ids?.length),f=scope.filters||{};
      options.innerHTML=`${locked?'<p class="small-text muted">当前任务沿用原范围；修改范围请另开任务。</p>':''}${fixed?`<p class="small-text">${$kb.core.esc(scopeText(scope))}</p>`:`<label class="dash-project"><input type="checkbox" id="dash-project" ${f.project?'checked':''} ${locked?'disabled':''}>仅当前课题</label><label for="dash-direction">研究方向</label><select id="dash-direction" ${locked?'disabled':''}>${$kb.core.directionOptions()}</select><label for="dash-keywords">范围关键词</label><input id="dash-keywords" maxlength="200" placeholder="留空表示不限关键词" value="${$kb.core.esc(f.q||'')}" ${locked?'disabled':''}>`}`;
      if(!fixed){
        $kb.core.$('#dash-direction').value=f.direction||'';
        for(const id of ['dash-project','dash-direction','dash-keywords'])$kb.core.$('#'+id).oninput=()=>{
          const filters={project:$kb.core.$('#dash-project').checked,direction:$kb.core.$('#dash-direction').value,q:$kb.core.$('#dash-keywords').value.trim()};
          scopeDrafts.set(selectedKind,{filters});if(draft)draft.filters=filters;
          label.textContent=scopeText({filters});label.title=label.textContent;
        };
      }
    }
    const activity=t?.activity,role=t?.roles?.find(r=>r.id===activity?.role)?.name||activity?.role;
    const attentionOpen=$kb.core.$('.dash-attention')?.open;
    $kb.core.$('#dash-top-note').innerHTML=(historyMode?`<span class="dash-history-label">正在查看历史：${$kb.core.esc(t?.title||'读取中')}<button class="dash-text-button" id="dash-return-current">返回当前类型</button></span>`:'')+(role?`<span class="small-text muted">当前角色：${$kb.core.esc(role)}</span>`:'')+(t?.attention||t?.error?`<details class="dash-attention" ${attentionOpen?'open':''}><summary>${$kb.core.esc(t.attention?.message||'任务未完成，查看原因')}</summary><p>${$kb.core.esc(t.attention?.hint||t.error)}</p></details>`:'');
    if($kb.core.$('#dash-return-current'))$kb.core.$('#dash-return-current').onclick=()=>chooseKind(kinds[selectedKind]?selectedKind:'maintenance');
  }
  async function runPrimary(){
    if(controlBusy||creating||awaitingSnapshot)return;
    const t=currentTask(),choice=primaryAction(t);if(choice.disabled)return;
    controlBusy=true;renderToolbar();
    try{
      if(choice.action==='start'){
        const options=currentScope(),result=await submit(selectedKind,options);if(!result)return;
        const id=['maintenance','lineage'].includes(selectedKind)?result.id:(result.job||result.id?'job:'+(result.job||result.id):'');
        draft=null;historyMode=false;remember(id);resetView();awaitingSnapshot=true;$kb.core.toast(result.message||'任务已提交到研究调度台');
      }else{
        const result=await $kb.core.api(t.control.url,{action:choice.action==='resume'?(t.control.resume_action||'resume'):choice.action,...(t.control.filters?{filters:t.control.filters}:{})});
        if(t.source==='job'&&choice.action==='resume'&&(result.job||result.id)){remember('job:'+(result.job||result.id));resetView();}else{++epoch;clearTimeout(researchTimer);}
      }
      awaitingSnapshot=true;await $kb.status.refreshStatus();await refresh(epoch);
    }catch(e){awaitingSnapshot=false;$kb.feedback.showError(e.message);}finally{controlBusy=false;renderToolbar();}
  }
  async function open(){
    close();const generation=++epoch;
    if(window.STATIC_GRAPH){$kb.core.$('#main').innerHTML=$kb.core.empty('离线研究快照','任务调度请打开本地工作台。');return;}
    try{selectedKind=localStorage.getItem('research-selected-kind')||selectedKind;selected=pendingId!==null?pendingId:historyMode?localStorage.getItem('research-selected-task')||'':selectedKind?'':localStorage.getItem('research-selected-task')||'';}catch{}pendingId=null;
    cursor=0;events=[];snapshot=null;shell();await refresh(generation);
  }
  function close(){++epoch;clearTimeout(researchTimer);$kb.core.$('#dash-history')?.close();}
  async function refresh(generation=epoch){
    if($kb.core.state.page!=='research'||!$kb.core.$('#dash-workspace'))return;
    if(busy){researchTimer=setTimeout(()=>refresh(epoch),300);return;}
    busy=true;
    try{
      const data=await $kb.core.api('/api/task-dashboard?'+new URLSearchParams({task_id:selected,kind:historyMode?'':selectedKind,after_event_id:cursor,limit:100}));
      if(generation!==epoch||$kb.core.state.page!=='research')return;
      if(historyMode&&data.selected)selectedKind=normalizeKind(data.selected.kind);if(!selectedKind&&data.selected){const kind=normalizeKind(data.selected.kind);selectedKind=kinds[kind]?kind:'maintenance';if(!kinds[kind])historyMode=true;}if(!selectedKind)selectedKind='maintenance';
      if(data.selected?.id!==selected){remember(data.selected?.id||'');cursor=0;events=[];roleFilter='';}
      const known=new Set(events.map(e=>e.seq));events.push(...data.events.filter(e=>!known.has(e.seq)));events=events.slice(-1000);cursor=data.cursor;snapshot=data;awaitingSnapshot=false;
      $kb.core.$('#dash-connection').hidden=true;renderList();renderToolbar();renderTask();
      clearTimeout(researchTimer);researchTimer=setTimeout(()=>refresh(epoch),data.has_more?150:data.active?2000:10000);
    }catch(e){
      if(generation!==epoch||!$kb.core.$('#dash-connection'))return;
      $kb.core.$('#dash-connection').hidden=false;$kb.core.$('#dash-connection').textContent=`连接中断，保留最后状态${snapshot?' · 更新于 '+$kb.core.fmtDate(snapshot.updated):''}。正在重试。`;
      clearTimeout(researchTimer);researchTimer=setTimeout(()=>refresh(epoch),5000);
    }finally{busy=false;}
  }
  function renderList(){
    if(!snapshot||!$kb.core.$('#dash-task-list'))return;
    const items=snapshot.items.filter(t=>taskFilter==='all'||(taskFilter==='active'?['running','queued','stopping'].includes(t.status):taskFilter==='complete'?t.status==='complete':['failed','partial','blocked','deferred','interrupted','paused'].includes(t.status)));
    const list=$kb.core.$('#dash-task-list'),scroll=list.scrollTop,focus=document.activeElement?.dataset?.taskId;
    $kb.core.$('#dash-task-list').innerHTML=items.map(t=>`<button class="dash-task-link ${t.id===selected?'selected':''}" data-task-id="${$kb.core.esc(t.id)}" aria-pressed="${t.id===selected}"><span class="task-dot is-${$kb.core.esc(t.status)}"></span><span><strong>${$kb.core.esc(t.title)}</strong><small>${$kb.core.esc(taskStatus(t))} · ${$kb.core.fmtDate(t.created)}</small></span></button>`).join('')||'<p class="small-text muted">此范围暂无任务</p>';
    list.scrollTop=scroll;if(focus)Array.from(list.querySelectorAll('[data-task-id]')).find(b=>b.dataset.taskId===focus)?.focus({preventScroll:true});
    $kb.core.$$('#dash-task-list [data-task-id]').forEach(b=>b.onclick=()=>{draft=null;historyMode=true;remember(b.dataset.taskId);const task=snapshot.items.find(t=>t.id===selected);selectedKind=normalizeKind(task?.kind||'');$kb.core.$('#dash-history').close();resetView();snapshot=null;renderToolbar();refresh(epoch);});
  }
  function roleBubbleHTML(role){
    const items=(role.activities||[]).slice(0,2),count=role.active_count||0;
    const action=count>1?'并行处理 '+count+' 个单元':items[0]?.action||role.message||'等待可处理的证据';
    const subjects=items.map(item=>item.subject).filter(Boolean);
    const more=count>2?' · 另 '+(count-2)+' 个':'';
    return `<span class="agent-bubble ${count?'is-active':''}" data-agent-bubble="${$kb.core.esc(role.id)}" title="${$kb.core.esc([action,...subjects,role.updated?'更新于 '+$kb.core.fmtDate(role.updated):''].filter(Boolean).join(' · '))}"><span class="agent-action">${$kb.core.esc(action)}</span>${subjects.map((subject,index)=>`<span class="agent-subject">${$kb.core.esc(subject)}${index===subjects.length-1?$kb.core.esc(more):''}</span>`).join('')}${subjects.length===1&&items[0]?.detail?`<span class="agent-subject">${$kb.core.esc(items[0].detail)}</span>`:''}${!subjects.length&&role.pending_count?`<span class="agent-subject">${role.pending_count} 个子单元待处理</span>`:''}</span>`;
  }
  function renderTask(){
    const t=currentTask(),host=$kb.core.$('#dash-workspace');if(!host)return;
    if(!t){host.innerHTML=`<section class="dash-empty"><h1>${$kb.core.esc(kinds[selectedKind]||'开始一项研究任务')}</h1><p>确认上方范围后，点击“开始任务”。</p>${draft?.paper_ids?.length?`<p>${$kb.core.esc(scopeText(draft))}</p>`:''}</section>`;return;}
    // 更新时保留键盘焦点、步骤位置和日志阅读位置。
    const focus=document.activeElement?.dataset?.focusKey,stepScroll=$kb.core.$('#dash-steps')?.scrollTop||0,log=$kb.core.$('#dash-events'),logScroll=log?.scrollTop||0,atEnd=!log||log.scrollHeight-log.clientHeight-log.scrollTop<30;
    const parallel=t.parallel||{};
    const activity=t.activity,usage=t.usage,percent=t.total?Math.min(100,Math.round((t.processed||0)/t.total*100)):null;
    const terminal=!['queued','running','stopping'].includes(t.status);
    const cost=usage?(usage.priced&&usage.cost!=null?'¥'+usage.cost.toFixed(4):'未计价'):'未记录';
    host.innerHTML=`<section class="dash-heading"><div><h1>${$kb.core.esc(t.title)}</h1><p>${icon('folder')}${$kb.core.esc(t.scope)}</p></div><div class="dash-counters"><div class="counter-peach"><b>${number(t.processed)}<small> / ${number(t.total)}</small></b><span>已处理单元</span></div><div class="counter-purple"><b>${number(t.verified)}</b><span>已核结论</span></div><div class="counter-teal"><b>${usage&&(usage.input_tokens!=null||usage.output_tokens!=null)?number((usage.input_tokens||0)+(usage.output_tokens||0)):'—'}</b><span>云端 Token</span></div><div class="counter-green"><b>${usage?number(usage.calls):'—'}</b><span>云端调用</span></div></div></section>
      <div class="dash-subline"><span>${icon('clock')}累计历时 ${duration(t.elapsed_seconds)}</span><span>${icon('coins')}费用 ${cost}</span><span>本地调用 ${number(t.local_calls)} · 缓存复用 ${number(t.cache_hits)}</span><span class="spacer"></span></div>
      <div class="dash-secondary">${t.result_available?'<button class="dash-text-button" id="dash-result" data-focus-key="result">查看结论与证据</button>':''}${t.source==='job'?'<button class="dash-text-button" id="dash-job-detail">作业详情</button>':''}</div>
      ${(t.summary||t.skipped?.length||t.gaps?.length)?`<section class="dash-summary"><p class="small-text"><strong>结果摘要：</strong>${$kb.core.esc(t.summary||'未记录')}</p>${t.skipped?.length?`<p class="small-text muted">跳过/警告：${t.skipped.map(v=>$kb.core.esc(v)).join('；')}</p>`:''}${t.gaps?.length?`<p class="small-text muted">剩余缺口：${t.gaps.map(v=>$kb.core.esc(v)).join('；')}</p>`:''}</section>`:''}${t.verification_counts?`<p class="small-text">证据结果：已通过 ${number(t.verification_counts.verified||0)} · 证据不足 ${number(t.verification_counts.insufficient||0)} · 证据反驳 ${number(t.verification_counts.unsupported||0)} · 判断冲突 ${number(t.verification_counts.conflict||0)} · 结构错误 ${number(t.verification_counts.structure_error||0)} · 执行失败 ${number(t.unit_execution_counts?.failed||0)}。${t.stage_applicability?.fulltext_reading?'含全文精读阶段':'本任务不含全文精读阶段'}；未执行阶段不计为核验通过。</p>`:''}

      <section class="dash-execution"><div class="dash-section-heading"><h2>智能体协作与任务步骤</h2><span>实线表示单元内已记录的阶段交接</span><button class="button small" id="dash-clear-role" ${roleFilter?'':'hidden'}>显示全部角色</button></div><div class="dash-execution-grid"><div class="dash-flow" id="dash-flow"><canvas id="dash-connectors" aria-hidden="true"></canvas><div class="dash-nodes">${t.roles.map((r,i)=>`<button class="dash-node role-${$kb.core.esc(r.id)} tone-${i%5} ${roleFilter===r.id?'selected':''}" data-role="${$kb.core.esc(r.id)}" data-focus-key="role-${$kb.core.esc(r.id)}" aria-pressed="${roleFilter===r.id}">${roleBubbleHTML(r)}<span class="node-icon">${icon(icons[r.id]||'git-branch')}<i class="task-dot is-${$kb.core.esc(r.status)}"></i></span><strong>${$kb.core.esc(r.name)}</strong><small>${$kb.core.esc(status(r.status))}</small><span class="node-model">${$kb.core.esc(r.model||'模型未记录')}</span></button>`).join('')}</div><p class="dash-flow-note">${t.dependencies.length?`${t.dependencies.length} 条真实交接 · 点击角色筛选步骤与记录`:'尚无交接记录 · 下次执行后展示真实协作路径'}</p></div><div class="dash-step-panel"><div class="dash-step-summary"><strong>任务步骤</strong><span>${number(t.completed)} 完整完成 / ${number(t.total)}</span></div>${percent==null?(terminal?'<p class="small-text muted">未记录处理单元数'+(t.summary?' · '+$kb.core.esc(t.summary):'')+'</p>':'<p class="small-text muted">正在确定处理范围</p>'):`<div class="dash-progress"><progress value="${t.processed||0}" max="${t.total}" aria-label="处理进度"></progress><span>${percent}% 已处理</span></div>`}${parallel.total_units?`<p class="dash-unit-progress small-text muted">已生成子单元：${number(parallel.completed_units)} / ${number(parallel.total_units)} 完成 · ${number(parallel.running_units)} 个在途。上方统计父单元，子单元随候选召回增加。</p>`:''}<div class="dash-steps" id="dash-steps">${stepHTML(t)}</div></div></div></section>
      <section class="dash-bottom"><div class="dash-log-panel"><div class="dash-section-heading"><h2>执行记录</h2><span>${events.length} 条已载入${snapshot.has_more?' · 正在继续载入':''}</span></div><div class="dash-events" id="dash-events" data-focus-key="events" tabindex="0" aria-label="执行记录">${eventHTML(t)}</div></div><div class="dash-role-panel"><div class="dash-section-heading"><h2>角色状态</h2></div>${t.roles.map(r=>`<button class="dash-role-row" data-role="${$kb.core.esc(r.id)}" data-focus-key="row-${$kb.core.esc(r.id)}"><span class="task-dot is-${$kb.core.esc(r.status)}"></span><span><strong>${$kb.core.esc(r.name)}</strong><small>${$kb.core.esc(status(r.status))} · ${$kb.core.esc(r.model||'模型未记录')}</small></span></button>`).join('')}</div></section><div class="dash-footnote">更新时间 ${$kb.core.fmtDate(snapshot.updated)} · 核验通过数与处理进度分别统计 · 气泡显示执行摘要与处理对象，每2秒刷新</div>`;
    if($kb.core.$('#dash-result'))$kb.core.$('#dash-result').onclick=async()=>{try{const run=await $kb.core.api('/api/agent-runs/'+encodeURIComponent(t.id));$kb.graph.showAnswerDialog(run.result);}catch(e){$kb.feedback.showError(e.message);}};
    if($kb.core.$('#dash-job-detail'))$kb.core.$('#dash-job-detail').onclick=()=>$kb.shell.page('status');
    $kb.core.$$('[data-phase]').forEach(el=>{el.ontoggle=()=>{if(el.open)expandedPhases.add(el.dataset.phase);else expandedPhases.delete(el.dataset.phase);};});
    $kb.core.$$('[data-role]').forEach(b=>b.onclick=()=>{roleFilter=roleFilter===b.dataset.role?'':b.dataset.role;renderTask();});
    $kb.core.$('#dash-clear-role').onclick=()=>{roleFilter='';renderTask();};
    $kb.core.$$('[data-step-relation]').forEach(b=>b.onclick=async()=>{try{$kb.detail.showRelation(await $kb.core.api('/api/relation?rid='+encodeURIComponent(b.dataset.stepRelation)));}catch(e){$kb.feedback.showError(e.message);}});
    $kb.core.$$('[data-step-paper]').forEach(b=>b.onclick=()=>$kb.detail.showPaper(b.dataset.stepPaper));
    $kb.core.$('#dash-steps').scrollTop=stepScroll;$kb.core.$('#dash-events').scrollTop=atEnd?$kb.core.$('#dash-events').scrollHeight:logScroll;
    if(focus)host.querySelector(`[data-focus-key="${CSS.escape(focus)}"]`)?.focus({preventScroll:true});
    drawLinks(t);
  }
  function detailHTML(rows,t){
    return rows.map(s=>`<article class="dash-step is-${$kb.core.esc(s.status)}"><div><strong>${$kb.core.esc(s.title)}</strong><small>${$kb.core.esc(status(s.status))}${s.attempt?` · 第 ${s.attempt} 次执行`:''}</small>${s.subject?.startsWith('relation:')?`<button class="dash-text-button" data-step-relation="${$kb.core.esc(s.subject.slice(9))}" data-focus-key="relation-${$kb.core.esc(s.id)}">查看关系与证据</button>`:s.subject&&s.subject!==t.id?`<button class="dash-text-button" data-step-paper="${$kb.core.esc(s.subject)}" data-focus-key="paper-${$kb.core.esc(s.id)}">查看文献与证据</button>`:''}${s.error?`<details><summary>失败原因</summary><p>${$kb.core.esc(s.error)}</p></details>`:''}</div></article>`).join('');
  }
  function phaseHTML(phases,t){
    return phases.filter(p=>!roleFilter||p.roles.includes(roleFilter)).map(p=>`<details class="dash-phase is-${$kb.core.esc(p.status)}" data-phase="${$kb.core.esc(p.id)}" ${expandedPhases.has(p.id)?'open':''}><summary data-focus-key="phase-${$kb.core.esc(p.id)}"><span class="dash-phase-name">${icon(p.status==='complete'?'circle-check':'circle')}<strong>${$kb.core.esc(p.title)}</strong></span><span class="dash-phase-work">${(p.active_items||[]).slice(0,2).map(x=>`<span title="${$kb.core.esc(x.title)}">${$kb.core.esc(x.title)}</span>`).join('')}${p.active_count>2?`<span>另 ${p.active_count-2} 项并行</span>`:''}<small>${p.total?`${number(p.completed)} / ${number(p.total)} 完成 · `:''}${$kb.core.esc(p.status==='not_applicable'?'不适用':status(p.status))}</small></span></summary><div class="dash-phase-details">${detailHTML(p.details||[],t)||'<p class="small-text muted">暂无已记录明细。</p>'}${p.details_truncated?'<p class="small-text muted">显示优先级最高的20项；阶段统计包含全部单元。</p>':''}</div></details>`).join('')||'<p class="muted small-text">此角色暂无适用步骤。</p>';
  }
  function stepHTML(t){
    if(t.phase_summaries?.length)return phaseHTML(t.phase_summaries,t);
    return detailHTML(t.steps.filter(s=>!roleFilter||s.role===roleFilter),t)||'<p class="muted small-text">暂无已记录步骤。</p>';
  }
  function eventHTML(t){
    const rows=events.filter(e=>!roleFilter||e.role===roleFilter);
    return (t.error?`<details class="dash-error"><summary>最近失败的技术详情</summary><pre>${$kb.core.esc(t.error)}</pre></details>`:'')+(rows.map(e=>`<div class="dash-event is-${$kb.core.esc(e.status)}"><time>${$kb.core.esc(e.created.slice(11,19))}</time><span class="task-dot is-${$kb.core.esc(e.status)}"></span><strong>${$kb.core.esc((t.roles.find(r=>r.id===e.role)||{}).name||e.role)}</strong><span>${$kb.core.esc(e.message)}${e.payload?.model?' · '+$kb.core.esc(e.payload.model):''}</span></div>`).join('')||'<p class="muted">历史任务未记录执行过程。续跑后将从当前步骤开始记录。</p>');
  }
  function drawLinks(t){
    const c=$kb.core.$('#dash-connectors');if(!c)return;const box=c.parentElement.getBoundingClientRect(),dpr=window.devicePixelRatio||1;c.width=box.width*dpr;c.height=box.height*dpr;c.style.width=box.width+'px';c.style.height=box.height+'px';const ctx=c.getContext('2d');ctx.scale(dpr,dpr);ctx.lineWidth=1.6;ctx.strokeStyle='#c8ded2';
    for(const link of t.dependencies){const from=$kb.core.$(`.dash-node[data-role="${CSS.escape(link.source)}"] .node-icon`),to=$kb.core.$(`.dash-node[data-role="${CSS.escape(link.target)}"] .node-icon`);if(!from||!to)continue;const a=from.getBoundingClientRect(),b=to.getBoundingClientRect(),forward=b.left>=a.left,x=(forward?a.right:a.left)-box.left,y=a.top+a.height/2-box.top,u=(forward?b.left:b.right)-box.left,v=b.top+b.height/2-box.top;ctx.beginPath();ctx.moveTo(x,y);ctx.bezierCurveTo((x+u)/2,y,(x+u)/2,v,u,v);ctx.stroke();ctx.beginPath();ctx.moveTo(u+(forward?-5:5),v-3);ctx.lineTo(u,v);ctx.lineTo(u+(forward?-5:5),v+3);ctx.stroke();}
  }
  let creating=false;
  async function submit(kind,options={}){
    if(creating||window.STATIC_GRAPH)return null;
    const s=$kb.core.state.status;
    if(kind==='vectors'){
      const view=$kb.feedback.vectorActionView(s,false,false);
      if(view.disabled)throw Error(view.note);
    }
    if(kind==='verification'){
      if(['running','stopping'].includes(s.analysis_state?.state))throw Error('核验正在进行，请在当前任务中查看或停止。');
      if(s.indexing?.state==='running')throw Error('索引任务正在进行，结束后可安排核验。');
      if(!s.cloud_enabled)throw Error('请先在模型与费用设置中启用复核。');
    }
    let url,body;
    if(['maintenance','lineage'].includes(kind)){
      url='/api/agent-runs';body={kind,filters:options.filters||{},...(options.paper_ids?{paper_ids:options.paper_ids}:{}),...(options.stages?{stages:options.stages}:{})};
    }else if(kind==='verification'){
      url='/api/verification';body={action:'start',filters:options.filters||{}};
    }else if(['update','vectors'].includes(kind)){
      url='/api/jobs';body={action:kind};
    }else throw Error('请选择有效的调度任务类型。');
    creating=true;
    try{return await $kb.core.api(url,body);}finally{creating=false;}
  }
  async function showTask(id=''){
    if(window.STATIC_GRAPH)return;
    pendingId=id||null;draft=null;historyMode=Boolean(id);remember(id);resetView();
    if($kb.core.state.page!=='research')return $kb.shell.page('research');
    pendingId=null;return refresh(epoch);
  }
  async function prepareTask(value){
    await showTask();
    if($kb.core.state.page==='research')beginDraft(value);
  }
  function newTask(value={}){beginDraft(value);}
  async function showOverview(){try{const d=await $kb.core.api('/api/research-overview');$kb.core.$('#dialog-content').innerHTML=`<h2>文献概览</h2><p>全文精读完成 ${d.read_complete}/${d.library} · 部分理解 ${d.read_partial}</p><p>原文已核关系 ${d.lineage_coverage.fulltext} · 摘要支持 ${d.lineage_coverage.abstract} · 待核线索 ${d.lineage_coverage.title}</p><p>${d.note_conflicts} 篇笔记有待合并差异</p>${d.gaps.map(g=>`<p class="small-text muted">${$kb.core.esc(g)}</p>`).join('')}`;$kb.core.$('#evidence-dialog').showModal();}catch(e){$kb.feedback.showError(e.message);}}
  window.addEventListener('resize',()=>{if($kb.core.state.page==='research'&&snapshot?.selected)drawLinks(snapshot.selected);});
  return {open,refresh,close,status,submit,showTask,prepareTask,newTask,roleBubbleHTML,taskStatus,primaryAction,phaseHTML,scopeText};
})();
return {get researchTimer(){return researchTimer;},set researchTimer(value){researchTimer=value;},
get taskDashboard(){return taskDashboard;}};
});
