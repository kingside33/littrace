/* search：通过 ctx 访问其他模块；变量保留在本模块闭包内。 */
KB.define('search',($kb)=>{'use strict';
let searchGeneration=0;
const evidenceSelection=new Map();
let lastResults=[];

function answerHTML(data){
  const prefix='answer-'+(++$kb.graph.answerSerial),body=$kb.core.esc(data.answer||'').replace(/\[(\d+)\]/g,(mark,n)=>`<a href="#${prefix}-${n}" data-claim-ref="${prefix}-${n}">${mark}</a>`);
  const labels={abstract:'摘要支持',fulltext:'原文',personal:'个人判断',legacy_mixed:'历史混合笔记'};
  return `<div class="answer"><div class="eyebrow">逐项核验的结论</div>${retrievalSummary(data.search)}${comparisonHTML(data,prefix)}<div class="answer-body">${body}</div>${(data.claims||[]).map(c=>$kb.detail.verificationHTML(c.verification)).join('')}<div class="citations">${(data.citations||[]).map((s,i)=>`<details class="claim-evidence" id="${prefix}-${i+1}"><summary>[${i+1}] ${$kb.core.esc(s.title)} · ${$kb.core.esc(labels[s.evidence_level]||'来源')}${s.page_start?' p.'+s.page_start+(s.page_end&&s.page_end!==s.page_start?'–'+s.page_end:''):''}</summary><blockquote>${$kb.core.esc(s.quote)}</blockquote><a href="${$kb.core.safe(s.url)}">打开对应证据 ↗</a></details>`).join('')}</div>${(data.limitations||[]).map(x=>`<p class="small-text muted">${$kb.core.esc(x)}</p>`).join('')}<details><summary>检索与核验过程</summary><pre>${$kb.core.esc(JSON.stringify(data.retrieval_process||[],null,2))}</pre>${data.verification_gaps?.length?`<h4>未发布的结论与核验理由</h4><pre>${$kb.core.esc(JSON.stringify(data.verification_gaps,null,2))}</pre>`:''}</details></div>`;
}

function comparisonHTML(data,prefix){
  if(!data.comparison?.length)return '';
  const fields={method:'方法',assumptions:'假设',metrics:'指标',limitations:'局限',...((data.comparison||[]).some(p=>'problem' in p)?{problem:'研究问题',object:'研究对象',state_space:'状态空间',constraints:'约束',objective:'目标函数',formulas:'关键公式',algorithm_steps:'算法步骤',improvement:'改进机制',experiments:'实验条件',conclusions:'研究结论',contributions:'作者贡献'}:{})};
  const cell=items=>(items||[]).map(c=>`<p>${$kb.core.esc(c.text)} ${(c.citations||[]).map(n=>`<a href="#${prefix}-${n}" data-claim-ref="${prefix}-${n}">[${n}]</a>`).join('')}</p>`).join('')||'<span class="muted">缺少已核证据</span>';
  return `${(data.comparison_warnings||[]).map(w=>`<p class="verification-trace">可比性：${$kb.core.esc(w)}</p>`).join('')}<div class="table-scroll comparison-table"><table><thead><tr><th>论文</th>${Object.values(fields).map(f=>`<th>${f}</th>`).join('')}</tr></thead><tbody>${data.comparison.map(p=>`<tr><th>${$kb.core.esc(p.title)}</th>${Object.keys(fields).map(k=>`<td>${cell(p[k])}</td>`).join('')}</tr>`).join('')}</tbody></table></div><div class="comparison-cards">${data.comparison.map(p=>`<article><h3>${$kb.core.esc(p.title)}</h3>${Object.entries(fields).map(([key,label])=>`<h4>${label}</h4>${cell(p[key])}`).join('')}</article>`).join('')}</div>`;
}

function searchPage(){
  $kb.core.$('#main').innerHTML=$kb.core.intro('EVIDENCE RETRIEVAL','从问题找到可核查的证据','统一检索原文、图表、公式、研究笔记与批注。检索自动调用本地向量模型，空闲5分钟后卸载。综合问答沿用已配置的生成与核验模型，并附逐条来源。')+`<form id="search-form"><div class="search-box"><input id="query" type="search" placeholder="例如：Informed RRT* 如何改善 RRT* 的采样效率？" aria-label="研究问题" required><button class="button primary" type="submit">检索证据</button><button class="button" type="button" id="ask-button">带引用问答</button></div><div class="toolbar"><select id="source-filter" aria-label="来源类型"><option value="">全部来源</option>${Object.entries($kb.core.sourceNames).map(([k,v])=>`<option value="${k}">${v}</option>`).join('')}</select><select id="direction-filter" aria-label="方向">${$kb.core.directionOptions()}</select><input id="year-filter" placeholder="年份，如 2020-2026" aria-label="年份范围" style="width:160px"><input id="collection-filter" placeholder="Collection 完整路径" aria-label="Collection"><input id="tag-filter" placeholder="标签" aria-label="标签"><button class="button" type="button" id="browse-library">浏览 Zotero 库</button><label><input id="search-project" type="checkbox">当前课题</label></div></form><div id="answer-area"></div><div id="evidence-toolbar" class="toolbar" hidden><span id="evidence-count" class="small-text muted" role="status">已选 0 条证据</span><button class="button primary" type="button" id="evidence-copy" disabled>复制证据为 Markdown</button><button class="button" type="button" id="evidence-clear" disabled>清空选择</button></div><div id="results">${$kb.core.empty('先提出一个研究问题','可以查找原文、图表、公式，或寻找你在笔记与批注中留下的线索。')}</div>`;
  const filters=()=>({kind:$kb.core.$('#source-filter').value,direction:$kb.core.$('#direction-filter').value,year:$kb.core.$('#year-filter').value,project:$kb.core.$('#search-project').checked,collection:$kb.core.$('#collection-filter')?.value||'',tag:$kb.core.$('#tag-filter')?.value||''});
  const syncSearchUrl=()=>{try{if(typeof location==='undefined')return;const params=new URLSearchParams(location.search);const q=$kb.core.$('#query').value.trim();if(q)params.set('q',q);else params.delete('q');for(const [k,v] of Object.entries(filters())){if(v&&v!==false)params.set(k,String(v));else params.delete(k);}const qs=params.toString();history.replaceState(null,'',location.pathname+(qs?'?'+qs:'')+location.hash);}catch(e){}};
  const restoreSearch=()=>{if(typeof location==='undefined')return null;const params=new URLSearchParams(location.search);const q=params.get('q');if(q)$kb.core.$('#query').value=q;const set=(id,key)=>{const el=$kb.core.$(id);if(el&&params.get(key))el.value=params.get(key);};set('#source-filter','kind');set('#direction-filter','direction');set('#year-filter','year');set('#collection-filter','collection');set('#tag-filter','tag');if(params.get('project')==='true'&&$kb.core.$('#search-project'))$kb.core.$('#search-project').checked=true;return q;};
  async function browseLibrary(offset=0){try{const data=await $kb.core.api('/api/library?'+new URLSearchParams({q:$kb.core.$('#query').value,...filters(),offset,limit:50}));renderResults({results:data.items.map(p=>({...p,paper_id:p.id,kind:'metadata',text:p.abstract||'',url:$kb.core.metaLink(p)})),mode:'metadata',total:data.total});$kb.core.$('#answer-area').innerHTML=`<p>匹配 ${data.total} 篇；当前 ${data.total?offset+1:0}–${offset+data.items.length} 篇。</p><button class="button small" id="library-prev" ${offset===0?'disabled':''}>上一页</button> <button class="button small" id="library-next" ${offset+data.items.length>=data.total?'disabled':''}>下一页</button>`;$kb.core.$('#library-prev').onclick=()=>browseLibrary(Math.max(0,offset-50));$kb.core.$('#library-next').onclick=()=>browseLibrary(offset+50);}catch(e){$kb.feedback.showError(e.message);}}
  $kb.core.$('#browse-library').onclick=()=>browseLibrary();
  evidenceSelection.clear();lastResults=[];
  $kb.core.$('#evidence-copy').onclick=()=>copyEvidenceMarkdown();
  $kb.core.$('#evidence-clear').onclick=()=>clearEvidenceSelection();
  if(restoreSearch())setTimeout(()=>{if($kb.core.state.page==='search')run(false);},0);
  const run=async askMode=>{const q=$kb.core.$('#query').value.trim();if(!q)return;syncSearchUrl();
    const yearInput=$kb.core.$('#year-filter'),yearText=yearInput?yearInput.value.trim():'';
    if(yearText&&!/^\d{4}(\s*-\s*\d{4})?$/.test(yearText)){$kb.core.toast('年份格式应为 2020 或 2020-2026');yearInput.focus();return;}
    const generation=++searchGeneration,queryFilters=filters();$kb.core.$('#results').innerHTML=$kb.core.loading();$kb.core.$('#answer-area').innerHTML='';
    if(askMode&&!$kb.core.state.status.cloud_enabled){$kb.core.$('#results').innerHTML=$kb.core.empty('问答尚未启用','请先在“模型与费用设置”中配置并启用问答。证据检索会自动使用本地向量模型。',{label:'去设置',act:'to-settings'});return;}
    try{
      if(!askMode)$kb.core.$('#results').innerHTML='<div class="loading" role="status"><i class="spinner"></i>正在生成查询向量并检索本地证据，首次加载可能需要稍候…</div>';
      let data=askMode?await $kb.graph.pollRun(await $kb.core.api('/api/agent-runs',{kind:'answer',query:q,filters:queryFilters}),generation):await $kb.core.api('/api/search?'+new URLSearchParams({q,...queryFilters,vectors:true,wait_vectors:true}));
      while(!askMode&&data.vector_status==='queued'&&generation===searchGeneration&&$kb.core.state.page==='search'){
        $kb.core.$('#results').innerHTML='<div class="loading" role="status"><i class="spinner"></i>本地向量请求正在排队，完成后自动显示融合结果…</div>';
        await new Promise(resolve=>setTimeout(resolve,1000));
        if(generation!==searchGeneration||$kb.core.state.page!=='search')return;
        data=await $kb.core.api('/api/search?'+new URLSearchParams({q,...queryFilters,vectors:true,wait_vectors:true,resume_model:false}));
      }
      if(generation!==searchGeneration||$kb.core.state.page!=='search')return;
      if(!data)return;
      if(askMode)$kb.core.$('#answer-area').innerHTML=answerHTML(data);
      const found=askMode?data.search:data;renderResults(found||{results:[]});
    }catch(e){
      if(generation!==searchGeneration||$kb.core.state.page!=='search')return;
      $kb.core.$('#results').innerHTML=$kb.core.empty('本次检索未完成','可修改问题或筛选后重试。',{label:'清空关键词',act:'clear-search'});
      $kb.feedback.showError('本次检索未完成，可调整问题或筛选后重试。',{title:'检索未完成',detail:e.message});
    }}
  $kb.core.$('#search-form').onsubmit=e=>{e.preventDefault();run(false);};$kb.core.$('#ask-button').onclick=()=>run(true);
  $kb.core.$('#search-form').insertAdjacentHTML('afterend','<details class="compare-panel"><summary>比较2至4篇论文</summary><label for="compare-query">查找论文</label><input id="compare-query" type="search" placeholder="输入题名或 citekey"><div id="compare-options"></div><div id="compare-selected"></div><button class="button" id="compare-start" type="button">比较方法、假设、指标与局限</button></details>');
  const selected=new Map();let compareLookup=0;
  $kb.core.$('#compare-query').oninput=async()=>{const ticket=++compareLookup;const data=await $kb.core.api('/api/papers?'+new URLSearchParams({q:$kb.core.$('#compare-query').value,limit:10}));if(ticket!==compareLookup||$kb.core.state.page!=='search')return;$kb.core.$('#compare-options').innerHTML=data.items.map(p=>`<button class="button small" data-compare-id="${$kb.core.esc(p.id)}">${$kb.references.citationBadge(p)}${$kb.core.esc(p.citekey||p.title)}</button>`).join('');$kb.core.$$('[data-compare-id]').forEach(b=>b.onclick=()=>{if(selected.size>=4&&!selected.has(b.dataset.compareId)){$kb.core.toast('最多比较4篇论文');return;}selected.set(b.dataset.compareId,data.items.find(p=>p.id===b.dataset.compareId));renderSelected();});};
  function renderSelected(){$kb.core.$('#compare-selected').innerHTML=[...selected.values()].map(p=>`<button class="button small" data-remove-compare="${$kb.core.esc(p.id)}">${$kb.core.esc(p.citekey||p.title)} ×</button>`).join('');$kb.core.$$('[data-remove-compare]').forEach(b=>b.onclick=()=>{selected.delete(b.dataset.removeCompare);renderSelected();});}
  $kb.core.$('#compare-start').onclick=async()=>{if(selected.size<2){$kb.core.toast('请先选择2至4篇论文');return;}const generation=++searchGeneration;try{const result=await $kb.graph.pollRun(await $kb.core.api('/api/agent-runs',{kind:'compare',query:$kb.core.$('#query').value,paper_ids:[...selected.keys()]}),generation);if(result&&generation===searchGeneration&&$kb.core.state.page==='search'){$kb.core.$('#answer-area').innerHTML=answerHTML(result);renderResults(result.search||{results:[]});}}catch(e){$kb.feedback.showError(e.message);}};
}

function paperHitsHTML(papers){
  const items=papers.filter(p=>p.id&&p.title);if(!items.length)return '';
  return `<details class="paper-hits"><summary>相关论文 · ${items.length} 篇</summary>${items.map(p=>`<article><button class="link-button" data-paper="${$kb.core.esc(p.id)}">${p.year||''} ${$kb.references.citationBadge(p)}${$kb.core.esc(p.title)}</button>${p.abstract?`<p class="small-text muted">${$kb.core.esc(p.abstract.length>350?p.abstract.slice(0,350)+'…':p.abstract)}</p>`:''}</article>`).join('')}</details>`;
}

function retrievalSummary(data={}){
  const mode=data.mode||'',mixed=mode.includes('mixed'),hybrid=mode.includes('vector')||mode.includes('hybrid');
  const label=mixed?'部分查询使用语义向量 + 关键词':hybrid?'语义向量 + 关键词融合':mode?'关键词检索':'检索方式待确认';
  const status={cached:'查询向量已缓存',ready:'本地查询向量已生成',queued:'查询向量仍在排队',stopped:'向量模型已停止',unavailable:'向量暂不可用',no_index:'当前范围无向量索引'}[data.vector_status];
  const coverage=data.coverage||{},counts=Number.isFinite(coverage.vectors)?`已发布向量 ${coverage.vectors.toLocaleString('zh-CN')} / ${(coverage.published_sources??coverage.sources).toLocaleString('zh-CN')}`:'';
  return `<p class="small-text muted" role="status">${$kb.core.esc(label)}${status?' · '+$kb.core.esc(status):''}${counts?' · '+$kb.core.esc(counts):''}${coverage.live_vector_pending?' · '+coverage.live_vector_pending.toLocaleString('zh-CN')+' 条新原文待向量化':''}${coverage.vector_warning?' · '+$kb.core.esc(coverage.vector_warning):''}</p>`;
}

/* 证据复制：纯格式化函数不触 DOM，链接白名单与 core.js 的 safe() 同一口径。 */
const EVIDENCE_LINK_SAFE=/^(https?:|zotero:|obsidian:|\/)/i;
function evidenceTitle(r){return String(r.title||'').trim()||r.citekey||'未命名文献';}
function evidencePageLabel(r){return r.page_start?'p.'+r.page_start+(r.page_end&&r.page_end!==r.page_start?'–'+r.page_end:''):(r.page?'p.'+r.page:'');}
function evidenceMarkers(r){const marks=[];if(r.origin==='legacy_mixed')marks.push('历史混合笔记，可能含 AI 内容');if(r.latex_status==='legacy_unverified')marks.push('历史公式识别，尚未复核');if(r.ocr_provenance)marks.push((r.ocr_provenance.model||'OCR')+' 识别');return marks;}
function evidenceLinkMarkdown(r,resolveLink){const raw=typeof r.url==='string'?r.url:'';if(!raw)return '';const target=typeof resolveLink==='function'?resolveLink(raw):raw;if(!target||!EVIDENCE_LINK_SAFE.test(target))return '';return ' · [打开来源]('+target.replace(/\(/g,'%28').replace(/\)/g,'%29')+')';}
function evidenceEntryMarkdown(r,resolveLink){const parts=['**'+evidenceTitle(r)+'**',String($kb.core.sourceNames[r.kind]||r.kind)];const page=evidencePageLabel(r);if(page)parts.push(page);parts.push(...evidenceMarkers(r));const line='- '+parts.join(' · ')+evidenceLinkMarkdown(r,resolveLink);const full=String(r.text||r.snippet||'');return full?line+'\n'+full.split(/\r\n|\r|\n/).map(x=>'  > '+x).join('\n'):line;}
function evidenceToMarkdown(selectedIds,results,{resolveLink}={}){const chosen=new Set(selectedIds||[]);return (results||[]).filter(r=>chosen.has(r.id)).map(r=>evidenceEntryMarkdown(r,resolveLink)).join('\n');}
function resolveEvidenceLink(url){if(!/^\//.test(url))return url;const base=(typeof window!=='undefined'&&window.STATIC_GRAPH)?'http://127.0.0.1:8765':(typeof location!=='undefined'?location.origin:'');return base?base+url:'';}
function toggleEvidence(result,checked){if(checked)evidenceSelection.set(result.id,result);else evidenceSelection.delete(result.id);updateEvidenceToolbar();}
function clearEvidenceSelection(){evidenceSelection.clear();$kb.core.$$('#results [data-evidence-check]').forEach(el=>{el.checked=false;});updateEvidenceToolbar();}
function updateEvidenceToolbar(){const bar=$kb.core.$('#evidence-toolbar');if(!bar)return;bar.hidden=!lastResults.length;const count=evidenceSelection.size;const countEl=$kb.core.$('#evidence-count');if(countEl)countEl.textContent='已选 '+count+' 条证据';const copy=$kb.core.$('#evidence-copy'),clear=$kb.core.$('#evidence-clear');if(copy)copy.disabled=count===0;if(clear)clear.disabled=count===0;}
async function copyEvidenceMarkdown(){
  const ids=[...evidenceSelection.keys()];
  if(!ids.length){$kb.core.toast('请先选择要复制的证据');return;}
  const md=evidenceToMarkdown(ids,lastResults,{resolveLink:resolveEvidenceLink});
  let copied=false;
  try{await navigator.clipboard.writeText(md);copied=true;}catch(e){copied=false;}
  if(!copied){try{const field=document.createElement('textarea');field.value=md;field.setAttribute('readonly','');field.style.position='fixed';field.style.opacity='0';document.body.appendChild(field);field.select();copied=document.execCommand('copy');field.remove();}catch(e){copied=false;}}
  if(copied){$kb.core.toast('已复制 '+ids.length+' 条证据为 Markdown');return;}
  $kb.core.$('#dialog-content').innerHTML='<h2>剪贴板不可用，请手工复制</h2><textarea id="evidence-copy-text" rows="14" readonly style="width:100%"></textarea><p class="small-text muted">选择已保留；下面的文本与复制目标完全一致，全选后用 Ctrl+C 复制。</p>';
  const box=$kb.core.$('#evidence-copy-text');if(box){box.value=md;if(box.select)box.select();}
  const dlg=$kb.core.$('#evidence-dialog');if(dlg&&!dlg.open)dlg.showModal();
  $kb.core.toast('剪贴板不可用，已打开手工复制窗口');
}

function renderResults(data){
  evidenceSelection.clear();lastResults=data.results||[];
  const excerpt=r=>{const full=r.text||r.snippet||'',short=full.length>380?full.slice(0,380)+'…':full;return `${r.latex_status==='legacy_unverified'?'<span class="badge warn">历史公式识别，尚未复核</span>':''}${r.ocr_provenance?'<span class="badge">'+$kb.core.esc(r.ocr_provenance.model||'OCR')+' 识别</span>':''}<div class="excerpt">${$kb.core.esc(short)}</div>${full.length>380?`<details class="excerpt-more"><summary>展开完整证据</summary><div class="excerpt">${$kb.core.esc(full)}</div></details>`:''}`;};
  const results=lastResults;$kb.core.$('#results').innerHTML=(data.warning?`<div class="callout warn">${$kb.core.esc(data.warning)}</div>`:'')+retrievalSummary(data)+`<p class="small-text muted">${results.length} 条证据 · 来源保留文献与页码</p>`+paperHitsHTML(data.papers||[])+results.map((r,i)=>`<article class="result"><label class="evidence-pick"><input type="checkbox" data-evidence-check="${$kb.core.esc(r.id)}" aria-label="选择这条证据">选择</label><span class="badge ${r.kind==='paper_text'?'good':''}">${$kb.core.esc($kb.core.sourceNames[r.kind]||r.kind)}</span>${r.retrieval?.vector_rank?`<span class="badge good">${r.retrieval.keyword_rank?'关键词与语义命中':'语义命中'}</span>`:r.retrieval?.keyword_rank?'<span class="badge">关键词命中</span>':''}${r.origin==='legacy_mixed'?'<span class="badge warn">历史混合笔记，可能含 AI 内容</span>':''}${r.scope_context?'<span class="badge">补充方法脉络</span>':''}<div class="meta">${r.year||''} ${$kb.core.esc(r.section||'')} ${r.page_start?' · p.'+r.page_start+(r.page_end!==r.page_start?'–'+r.page_end:''):r.page?' · p.'+r.page:''}</div><h3>${$kb.references.citationBadge(r)}${$kb.core.esc(r.title)}</h3>${excerpt(r)}${r.latex?`<div class="excerpt mono">${$kb.core.esc(r.latex)}</div>`:''}<div class="result-footer"><a href="${$kb.core.safe(r.url)}">打开来源 ↗</a>${r.kind==='figure'||r.kind==='formula'?`<button class="link-button" data-image="${i}">查看原图</button>`:''}${r.paper_id?`<button class="button small" data-paper="${$kb.core.esc(r.paper_id)}">文献与研究关系 →</button>`:''}</div><div id="image-${i}"></div></article>`).join('')+(results.length?'':$kb.core.empty('没有找到匹配证据','可以更换中英文关键词、放宽方向或年份范围。'));
  $kb.core.$$('#results [data-evidence-check]').forEach(el=>{el.onchange=()=>{const result=results.find(x=>x.id===el.dataset.evidenceCheck);if(result)toggleEvidence(result,el.checked);};});
  $kb.core.$$('#results [data-paper]').forEach(el=>el.onclick=()=>$kb.detail.showPaper(el.dataset.paper));
  $kb.core.$$('#results [data-image]').forEach(el=>el.onclick=()=>{const i=+el.dataset.image;$kb.core.$('#image-'+i).innerHTML=`<img src="/api/asset?source_id=${encodeURIComponent(results[i].id)}" alt="${$kb.core.esc(results[i].title)} 原图">`;});
  updateEvidenceToolbar();
}
return {get searchGeneration(){return searchGeneration;},set searchGeneration(value){searchGeneration=value;},
get answerHTML(){return answerHTML;},set answerHTML(value){answerHTML=value;},
get comparisonHTML(){return comparisonHTML;},set comparisonHTML(value){comparisonHTML=value;},
get searchPage(){return searchPage;},set searchPage(value){searchPage=value;},
get paperHitsHTML(){return paperHitsHTML;},set paperHitsHTML(value){paperHitsHTML=value;},
get retrievalSummary(){return retrievalSummary;},set retrievalSummary(value){retrievalSummary=value;},
get renderResults(){return renderResults;},set renderResults(value){renderResults=value;},
get evidenceSelection(){return evidenceSelection;},
get evidenceToMarkdown(){return evidenceToMarkdown;},set evidenceToMarkdown(value){evidenceToMarkdown=value;},
get toggleEvidence(){return toggleEvidence;},set toggleEvidence(value){toggleEvidence=value;},
get clearEvidenceSelection(){return clearEvidenceSelection;},set clearEvidenceSelection(value){clearEvidenceSelection=value;},
get updateEvidenceToolbar(){return updateEvidenceToolbar;},set updateEvidenceToolbar(value){updateEvidenceToolbar=value;},
get copyEvidenceMarkdown(){return copyEvidenceMarkdown;},set copyEvidenceMarkdown(value){copyEvidenceMarkdown=value;},
get resolveEvidenceLink(){return resolveEvidenceLink;},set resolveEvidenceLink(value){resolveEvidenceLink=value;}};
});
