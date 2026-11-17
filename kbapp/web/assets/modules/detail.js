/* detail：通过 ctx 访问其他模块；变量保留在本模块闭包内。 */
KB.define('detail',($kb)=>{'use strict';
let paperGeneration=0;

let referenceTimer=null;

function positionDetail(){const detail=$kb.core.$('#detail');if(detail)detail.style.top=window.matchMedia('(max-width:850px)').matches?Math.ceil($kb.core.$('.rail').getBoundingClientRect().bottom)+'px':'';}

function syncPaperUrl(id){try{if(typeof location==='undefined')return;const params=new URLSearchParams(location.search);if(id)params.set('paper',id);else params.delete('paper');const qs=params.toString();history.replaceState(null,'',location.pathname+(qs?'?'+qs:'')+location.hash);}catch(e){}}

async function showPaper(id,opts={}){
  const g=++paperGeneration;
  clearTimeout(referenceTimer);
  if(!opts.keepScroll){$kb.core.state.detailOpener=document.activeElement;$kb.core.state.detailOpenerKey=document.activeElement?.dataset?.focusKey;}
  try{const data=await $kb.core.api('/api/paper?paper='+encodeURIComponent(id));if(g!==paperGeneration)return;const p=data.paper;$kb.core.state.detail=id;syncPaperUrl(id);$kb.core.$('#detail').hidden=false;document.body.classList.add('detail-open');positionDetail();
    const rows=data.relations.filter(r=>r.status!=='rejected');const semantic=rows.filter(r=>r.kind!=='cites');
    $kb.core.$('#detail').innerHTML=`<button class="close" id="close-detail" aria-label="关闭详情">×</button><div class="eyebrow">PAPER & EVIDENCE</div><span class="badge ${p.kind==='library'?'good':''}">${p.kind==='library'?'库内文献':'库外文献'}</span><span class="badge">${p.year||'年份待核'}</span><h2>${$kb.core.esc(p.title)}</h2><p>${$kb.core.esc((p.authors||[]).join('、')||p.author||'')}</p><div class="toolbar"><a class="button primary" href="${$kb.core.safe($kb.core.metaLink(p))}">阅读原文 ↗</a><button class="button" id="paper-neighbors">展开上下游</button></div>${p.kind!=='library'?'<button class="button small" id="request-fulltext">申请获取关键论文全文</button>':''}<div class="section"><h3>摘要</h3><p class="abstract">${$kb.core.esc(p.abstract||'尚未获取摘要，可在下次元数据更新后查看。')}</p></div><div class="section"><h3>研究关系 <span class="muted">${semantic.length}</span></h3>${semantic.length?semantic.slice(0,40).map(r=>`<div class="relation-row" data-relation="${$kb.core.esc(r.id)}" role="button" tabindex="0" aria-label="查看关系证据：${r.status==='candidate'?'待核':'已确认'}${$kb.core.esc($kb.core.relationNames[r.kind])}"><span class="badge ${r.status==='candidate'?'warn':'good'}">${r.status==='candidate'?'待核':'已确认'}</span>${$kb.core.esc($kb.core.relationNames[r.kind])}<p>${$kb.core.esc(r.explanation||'')}</p></div>`).join(''):'<p>当前已知引用事实尚未形成已核实的研究关系。</p>'}<p class="small-text muted">直接引用与被引：${rows.filter(r=>r.kind==='cites').length} 条</p></div>`;
    $kb.core.$('#detail h2').insertAdjacentHTML('afterbegin',$kb.references.citationBadge(p));
    $kb.core.$('#detail h2').insertAdjacentHTML('afterend','<p id="paper-citation" class="small-text">'+$kb.references.citationBadge(p,true)+'</p>');
    if(opts.parent)$kb.core.$('#detail .eyebrow').insertAdjacentHTML('afterend','<button class="link-button small-text" id="reference-back">← 返回上一文献</button>');
    if($kb.core.$('#reference-back'))$kb.core.$('#reference-back').onclick=()=>showPaper(opts.parent);
    $kb.core.$('#detail .abstract').parentElement.insertAdjacentHTML('afterend','<section class="section" id="paper-references"></section>');
    if(data.references)$kb.references.mountReferences(data.references,id,g);
    if(!window.STATIC_GRAPH)mountDocuments(p,g);
    const u=data.understanding,coverage=u?.coverage||{},sync=data.note_sync||{};
    $kb.core.$('#detail').insertAdjacentHTML('beforeend',`<section class="section"><h3>全文理解与证据缺口</h3><span class="badge ${coverage.complete?'good':'warn'}">${u?.verification_status==='stale'?'版本变化，待重读':coverage.complete?'全文精读完成':u?'部分理解':'尚未精读'}</span><p>${(coverage.read_pages||[]).length}/${coverage.total_pages||0} 页已读${coverage.missing_pages?.length?' · 缺失页 '+coverage.missing_pages.join('、'):''}</p>${!window.STATIC_GRAPH?'<button class="button small" id="read-paper">到调度台安排精读</button>':''}${(u?.claims||[]).map(c=>`<details><summary>${$kb.core.esc(c.category)} · ${$kb.core.esc(c.text)}</summary>${verificationHTML(c.verification)}${(c.evidence||[]).map(e=>`<blockquote>${$kb.core.esc(e.quote)}</blockquote><a href="${$kb.core.safe(e.url)}">原文 p.${e.page_start||'?'} ↗</a>`).join('')}${c.numbers?.length?`<div class="table-scroll"><table><thead><tr><th>指标</th><th>值与单位</th><th>基线</th><th>实验条件</th></tr></thead><tbody>${c.numbers.map(n=>`<tr><td>${$kb.core.esc(n.metric)}</td><td>${$kb.core.esc(n.value)} ${$kb.core.esc(n.unit)}</td><td>${$kb.core.esc(n.baseline)}</td><td>${$kb.core.esc(n.conditions)}</td></tr>`).join('')}</tbody></table></div>`:''}</details>`).join('')}${(u?.gaps||[]).map(g=>`<p class="small-text muted">${$kb.core.esc(g)}</p>`).join('')}</section><section class="section"><h3>笔记同步</h3><p>${$kb.core.esc({synced:'专属区块已同步',conflict:'发现人工编辑，已保留；自动版本待合并',not_generated:'尚未生成自动精读区块'}[sync.status]||sync.status||'离线快照不包含最新笔记状态')}</p>${sync.diff?`<details><summary>查看待合并差异</summary><pre class="note-diff">${$kb.core.esc(sync.diff)}</pre></details>`:''}</section>`);
    if($kb.core.$('#read-paper'))$kb.core.$('#read-paper').onclick=()=>{closeDetail();return $kb.dashboard.taskDashboard.prepareTask({kind:'maintenance',paper_ids:[id],stages:['understanding'],paperLabel:p.citekey||p.title});};
    const figures=u?.figures?.length?u.figures:(u?.figure_inventory||[]);
    if(figures.length){$kb.core.$('#detail').insertAdjacentHTML('beforeend',`<section class="section"><h3>${u?.figures?.length?'关键图表与图注证据':'已提取图表，解读待核'}</h3>${figures.map((f,i)=>`<button class="button small" data-reading-figure="${i}">${$kb.core.esc(f.label||'图表')} · p.${f.page||'?'} · 查看原图</button><p class="small-text">${$kb.core.esc(f.caption||'')}</p>`).join('')}</section>`);$kb.core.$$('[data-reading-figure]').forEach(b=>b.onclick=()=>{const f=figures[+b.dataset.readingFigure];$kb.core.$('#dialog-content').innerHTML=`<h2>原始图表 · p.${f.page||'?'}</h2><img style="max-width:100%" src="/api/asset?source_id=${encodeURIComponent(f.id)}" alt="${$kb.core.esc(f.caption||f.label||'原始图表')}"><p>${$kb.core.esc(f.caption||'')}</p><a href="${$kb.core.safe(f.url)}">打开PDF对应页 ↗</a>`;if(!$kb.core.$('#evidence-dialog').open)$kb.core.$('#evidence-dialog').showModal();});}
    $kb.core.$('#close-detail').onclick=closeDetail;$kb.core.$('#paper-neighbors').onclick=async()=>{if($kb.core.state.page!=='graph')await $kb.shell.page('graph');await $kb.graph.loadGraph(false,id);};
    if($kb.core.$('#request-fulltext'))$kb.core.$('#request-fulltext').onclick=async()=>{await $kb.core.api('/api/approvals/request',{paper_id:id,reason:'用户标记为关键论文，需要核实研究链条',question:'核实本文的问题、方法继承、具体改进及适用条件'});$kb.core.toast('已加入网页全文审批清单，批准前只保存元数据与摘要');$kb.status.refreshStatus();};
    $kb.core.$$('#detail [data-relation]').forEach(el=>{const open=()=>showRelation(rows.find(r=>r.id===el.dataset.relation));el.onclick=open;el.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();open();}};});
    if($kb.core.state.page==='graph'){if($kb.core.state.graphMode==='network'){$kb.graph.netSelect(id);requestAnimationFrame($kb.graph.netResize);}else $kb.graph.drawGraph();}
    if(!opts.keepScroll){$kb.core.$('#detail').scrollTop=0;$kb.core.$('#close-detail').focus();}
  }catch(e){if(g!==paperGeneration)return;$kb.feedback.showError(e.message);}
}

function closeDetail(){
  clearTimeout(referenceTimer);syncPaperUrl(null);
  paperGeneration++;$kb.core.state.detail=null;$kb.core.$('#detail').hidden=true;document.body.classList.remove('detail-open');
  if($kb.core.state.detailOpener?.isConnected)$kb.core.state.detailOpener.focus();else if($kb.core.state.detailOpenerKey&&$kb.core.$(`[data-focus-key="${CSS.escape($kb.core.state.detailOpenerKey)}"]`))$kb.core.$(`[data-focus-key="${CSS.escape($kb.core.state.detailOpenerKey)}"]`).focus();else $kb.core.$('nav button.active')?.focus();
  if($kb.core.state.page==='graph'&&$kb.core.state.graphMode==='network'){$kb.graph.netSelect(null);requestAnimationFrame(()=>requestAnimationFrame($kb.graph.netResize));}
  else $kb.graph.drawGraph();
}

function showRelation(r){
  if(!r)return;const dlg=$kb.core.$('#evidence-dialog');
  $kb.core.$('#dialog-content').innerHTML=`<div class="eyebrow">RELATION EVIDENCE</div><h2>${$kb.core.esc($kb.core.relationNames[r.kind])}</h2><span class="badge ${r.status==='candidate'?'warn':'good'}">${r.status==='candidate'?'待核候选':'已确认'}</span><span class="badge">${$kb.core.esc({citation:'引用记录',fulltext:'原文证据',abstract:'摘要证据',inference:'推断线索',human:'人工确认'}[r.level]||r.level)}</span><p>${$kb.core.esc(r.explanation||'')}</p>${[['前作的问题',r.previous_problem],['具体变化',r.change],['适用条件',r.conditions]].filter(x=>x[1]).map(([k,v])=>`<h3>${k}</h3><p>${$kb.core.esc(v)}</p>`).join('')}<h3>证据与出处</h3>${(r.evidence||[]).filter(e=>e.quote).map(e=>`<div class="dialog-evidence">${e.ocr_provenance?.model?'<span class="badge">'+$kb.core.esc(e.ocr_provenance.model)+' 识别</span><br>':''}${$kb.core.esc(e.quote)}${e.url?`<br><a href="${$kb.core.safe(e.url)}">查看来源${e.page?' · p.'+e.page:''} ↗</a>`:''}</div>`).join('')||'<p class="muted">这条记录只证明引用关系；没有可定位的语义证据。</p>'}${!window.STATIC_GRAPH?`<div class="field"><label for="relation-edit">人工解释 / 修正</label><textarea id="relation-edit" rows="3">${$kb.core.esc(r.explanation||'')}</textarea></div><div class="toolbar" style="margin-top:20px"><button class="button primary" id="confirm-relation">确认关系</button><button class="button danger" id="reject-relation">拒绝关系</button><span class="muted small-text">人工判断会持续保留</span></div>`:''}`;
  if(!window.STATIC_GRAPH){
    $kb.core.$('#relation-edit').parentElement.insertAdjacentHTML('beforebegin',`<div class="toolbar"><label>关系类型 <select id="relation-kind">${Object.entries($kb.core.relationNames).map(([k,v])=>`<option value="${k}" ${k===r.kind?'selected':''}>${v}</option>`).join('')}</select></label><label><input type="checkbox" id="relation-reverse">反转前作与后作方向</label></div>`);
    let saving=false;
    const save=async status=>{
      if(saving)return;saving=true;
      const kind=$kb.core.$('#relation-kind').value,reverse=$kb.core.$('#relation-reverse').checked,explanation=$kb.core.$('#relation-edit').value;
      const controls=['#confirm-relation','#reject-relation','#relation-kind','#relation-reverse'].map(s=>$kb.core.$(s)).filter(Boolean);
      controls.forEach(el=>{el.disabled=true;});dlg.setAttribute('aria-busy','true');
      try{
        await $kb.core.api('/api/relations/'+encodeURIComponent(r.id),{status,kind,reverse,explanation});
        Object.assign(r,{status,kind,explanation});
        $kb.core.toast('人工判断已保存');dlg.close();
        await $kb.graph.afterRelationSaved(r);
      }catch(e){$kb.feedback.showError(e.message);}
      finally{saving=false;dlg.removeAttribute('aria-busy');controls.forEach(el=>{el.disabled=false;});}
    };
    $kb.core.$('#confirm-relation').onclick=()=>save('confirmed');$kb.core.$('#reject-relation').onclick=()=>save('rejected');}
  if(!dlg.open)dlg.showModal();
  if(r.verification)$kb.core.$('#dialog-content').insertAdjacentHTML('beforeend',verificationHTML(r.verification));
  if(r.local_review_verdict)$kb.core.$('#dialog-content').insertAdjacentHTML('beforeend',`<div class="callout"><strong>${$kb.core.esc(r.local_reviewed_by||'本地关系核验')}</strong><br>${$kb.core.esc(r.local_review_verdict.reason||'未提供本地核验说明')}</div>`);
  if(r.independent_review)$kb.core.$('#dialog-content').insertAdjacentHTML('beforeend',`<div class="callout"><strong>${$kb.core.esc(r.reviewed_by||'模型复核')}</strong><br>${$kb.core.esc(r.independent_review)}</div>`);
  if(r.evidence_stale)$kb.core.$('#dialog-content').insertAdjacentHTML('afterbegin','<div class="callout warn">原证据版本已变化，需重新核对；人工判断已保留。</div>');
  if(r.pipeline_legacy)$kb.core.$('#dialog-content').insertAdjacentHTML('afterbegin','<div class="callout warn">旧模型流程记录，保留原有结果；待按GLM-OCR识别、本地核验、DeepSeek复核的新流程重核。</div>');
}

function verificationHTML(value){
  if(!value)return '';
  const states={supported:'支持',contradicted:'明确反驳',insufficient:'证据不足',execution_error:'执行失败',not_requested:'未请求'};
  return `<details class="verification-trace"><summary>${$kb.core.esc(value.label||'核验记录')} · ${({high:'高',medium:'中',low:'低'}[value.confidence]||'未记录')}置信度 · 补证 ${value.supplement_rounds||0} 轮</summary><p>本地：${$kb.core.esc(states[value.local_state]||'未记录')} — ${$kb.core.esc(value.local?.reason||'未记录')}</p><p>云端：${$kb.core.esc(states[value.cloud_state]||'未记录')} — ${$kb.core.esc(value.cloud?.reason||'未记录')}</p>${value.supplement_reasons?.length?`<p>补证原因：${value.supplement_reasons.map($kb.core.esc).join('；')}</p>`:''}<p>最终：${$kb.core.esc(value.reason||value.label)}</p><small>规则 ${$kb.core.esc(value.rule_version)} · 证据等级仍以原文／摘要标记为准</small></details>`;
}

async function mountDocuments(p,g){
  const host=document.createElement('section');host.className='section';host.id='paper-documents';$kb.core.$('#detail').appendChild(host);
  const url=id=>'/api/documents/'+encodeURIComponent(id);
  const labels={NATIVE_TEXT:'原生文字页',SCANNED:'扫描页',MIXED:'混合页',LOW_QUALITY:'低质量页',FAILED:'解析失败页',complete:'完成',pending:'待处理／待复核',failed:'失败',partial:'部分完成',interrupted:'已中断，可继续',queued:'排队中',running:'处理中',stopping:'停止中',confirmed:'人工已确认',rejected:'识别有误',formula:'公式',table:'表格',image:'图片'};
  const label=value=>labels[value]||value;
  const jobMessage=value=>{if(value.error)return value.error;try{const result=JSON.parse(value.progress),items=Array.isArray(result)?result:[result];return `完成 ${items.reduce((n,r)=>n+(r.complete||0),0)} 页，待处理 ${items.reduce((n,r)=>n+(r.pending||0),0)} 页，失败 ${items.reduce((n,r)=>n+(r.failed||0)+(r.error?1:0),0)} 页`;}catch{return value.progress||'';}};
  const fields=['doi','type','date','volume','issue','pages','extra','url','library_id','zotero_key','zotero_version'];
  const metadata=`<details><summary>Zotero 元数据与来源快照</summary><dl>${fields.map(k=>`<dt>${$kb.core.esc(k)}</dt><dd>${$kb.core.esc(p[k]??'未提供')}</dd>`).join('')}</dl><p>Collections：${$kb.core.esc((p.collections||[]).join(' / '))}</p><p>Tags：${$kb.core.esc((p.tags||[]).join('、'))}</p><pre class="note-diff">${$kb.core.esc(JSON.stringify(p.raw_zotero||{},null,2))}</pre></details>`;
  let data;
  async function refresh(){
    data=await $kb.core.api('/api/documents?paper='+encodeURIComponent(p.id));if(g!==paperGeneration)return;
    host.innerHTML=`<h3>附件版本与页面复核</h3>${metadata}<p>已登记 ${data.items.length} 个文档版本；Zotero 附件 ${(p.attachments||[]).length} 个。</p>${p.kind==='library'?'<button class="button small" id="parse-documents">解析／继续全部 PDF 附件</button>':''}<p id="document-job" role="status"></p><select id="document-select" aria-label="附件版本">${data.items.map(d=>`<option value="${$kb.core.esc(d.id)}">${$kb.core.esc(d.attachment_key||'主附件')} · ${d.page_count} 页 · ${d.sha256.slice(0,8)}</option>`).join('')}</select><input id="document-page" type="number" min="1" value="1" aria-label="PDF 页码" style="width:85px"><button class="button small" id="document-open">查看页面</button><div id="document-page-view"></div>${data.conflicts.length?`<details><summary>元数据冲突 ${data.conflicts.length} 条</summary>${data.conflicts.map(c=>`<p>${$kb.core.esc(c.field)}：保留 ${$kb.core.esc(c.authoritative)}；提取值 ${$kb.core.esc(c.proposed)}（待复核）</p>`).join('')}</details>`:''}`;
    if($kb.core.$('#parse-documents'))$kb.core.$('#parse-documents').onclick=()=>start({paper_id:p.id});
    $kb.core.$('#document-open').onclick=openPage;
    if(data.items.length)await openPage();
  }
  async function start(payload){
    try{const job=await $kb.core.api('/api/documents/process',payload);const label=$kb.core.$('#document-job');label.innerHTML='解析任务已启动 <button class="button small" id="document-stop">停止</button>';
      $kb.core.$('#document-stop').onclick=()=>$kb.core.api('/api/documents/jobs/'+job.id+'/stop',{});
      while(g===paperGeneration){await new Promise(r=>setTimeout(r,2000));const value=await $kb.core.api('/api/documents/jobs/'+job.id);if(g!==paperGeneration)return;
        label.firstChild.textContent=(labels[value.status]||value.status)+' · '+jobMessage(value)+' ';
        if(!['queued','running','stopping'].includes(value.status)){await refresh();$kb.core.$('#document-job').textContent=(labels[value.status]||value.status)+' · '+jobMessage(value);break;}}
    }catch(e){$kb.feedback.showError(e.message);}
  }
  async function openPage(){
    const id=$kb.core.$('#document-select')?.value,n=Number($kb.core.$('#document-page')?.value);if(!id)return;
    const target=$kb.core.$('#document-page-view'),d=data.items.find(x=>x.id===id),status=d.pages.find(x=>x.page===n);
    if(!status){target.innerHTML='<p>该页尚未处理，请启动附件解析。</p>';return;}
    try{const page=await $kb.core.api(url(id)+'/pages/'+n);if(g!==paperGeneration||$kb.core.$('#document-select')?.value!==id||Number($kb.core.$('#document-page')?.value)!==n)return;
      target.innerHTML=`<p>${$kb.core.esc(label(page.classification))} · ${$kb.core.esc(label(page.status))} · ${$kb.core.esc(page.payload.error||'')}</p><a class="button small" href="${url(id)}/file#page=${n}" target="_blank">PDF 原文 p.${n} ↗</a><button class="button small" id="document-retry">重处理此页</button><details open><summary>原文页面</summary><img style="max-width:100%" src="${url(id)}/pages/${n}/image" alt="PDF 原始页面 ${n}"></details><details><summary>原生文字</summary><pre class="note-diff">${$kb.core.esc(page.native_text)}</pre></details><details open><summary>OCR／Markdown</summary><pre class="note-diff">${$kb.core.esc(page.ocr_text||'该页未使用 OCR')}</pre></details>${page.blocks.filter(b=>['formula','table','image'].includes(b.kind)).map(b=>`<details><summary>${$kb.core.esc(label(b.kind))} · ${$kb.core.esc(label(b.payload.review_status||'待复核'))}</summary><img style="max-width:100%" src="${url(id)}/pages/${n}/image?block=${encodeURIComponent(b.id)}" alt="原文区域"><pre class="note-diff">${$kb.core.esc(b.text)}</pre><button class="button small" data-block="${$kb.core.esc(b.id)}" data-decision="confirmed">人工确认</button><button class="button small" data-block="${$kb.core.esc(b.id)}" data-decision="rejected">识别有误</button></details>`).join('')}<details><summary>本页参考文献与引用语境（${(page.references||[]).length} / ${(page.citation_contexts||[]).length}）</summary>${(page.references||[]).map(r=>`<pre class="note-diff">${$kb.core.esc(r.raw)}</pre>`).join('')}${(page.citation_contexts||[]).map(c=>`<p>${$kb.core.esc(c.section)} · 用途待核验</p><blockquote>${$kb.core.esc(c.text)}</blockquote>`).join('')}</details>`;
      $kb.core.$('#document-retry').onclick=()=>start({paper_id:p.id,document_id:id,pages:[n]});
      target.querySelectorAll('[data-block]').forEach(b=>b.onclick=async()=>{try{await $kb.core.api(url(id)+'/blocks/'+encodeURIComponent(b.dataset.block)+'/review',{status:b.dataset.decision});await openPage();}catch(e){$kb.feedback.showError(e.message);}});
    }catch(e){target.textContent=e.message;}
  }
  try{await refresh();}catch(e){host.textContent='附件页面功能尚不可用：'+e.message;}
}
return {get paperGeneration(){return paperGeneration;},set paperGeneration(value){paperGeneration=value;},
get referenceTimer(){return referenceTimer;},set referenceTimer(value){referenceTimer=value;},
get positionDetail(){return positionDetail;},set positionDetail(value){positionDetail=value;},
get showPaper(){return showPaper;},set showPaper(value){showPaper=value;},
get closeDetail(){return closeDetail;},set closeDetail(value){closeDetail=value;},
get showRelation(){return showRelation;},set showRelation(value){showRelation=value;},
get verificationHTML(){return verificationHTML;},set verificationHTML(value){verificationHTML=value;},
get mountDocuments(){return mountDocuments;},set mountDocuments(value){mountDocuments=value;}};
});
