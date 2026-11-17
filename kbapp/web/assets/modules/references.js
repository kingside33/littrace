/* references：通过 ctx 访问其他模块；变量保留在本模块闭包内。 */
KB.define('references',($kb)=>{'use strict';
function citationView(p={}){
  let count=p.citation_count;if(!Number.isInteger(count)||count<0)count=p.cited_by;
  const known=Number.isInteger(count)&&count>=0;
  const level=!known?'unknown':count===0?'zero':count<10?'low':count<100?'medium':count<1000?'high':'very-high';
  const intensity=known?Math.min(1,Math.log1p(count)/Math.log1p(1000)):0;
  const color='#'+[201,216,216].map((light,i)=>Math.round(light+([0,68,69][i]-light)*intensity).toString(16).padStart(2,'0')).join('');
  const label=known?`被引 ${count.toLocaleString('zh-CN')} 次`:'被引量未知';
  const source=p.citation_source||(known&&p.openalex_id?'OpenAlex 缓存':'');
  return {count:known?count:null,level,color,label,tip:label+(source?' · '+source:'')+(p.citation_updated?' · '+$kb.core.fmtDate(p.citation_updated):'')};
}

function citationBadge(p,showCount=false){const c=citationView(p);return `<span class="citation-badge" title="${$kb.core.esc(c.tip)}"><span class="citation-dot ${c.level}" role="img" aria-label="${$kb.core.esc(c.tip)}" style="--citation-color:${c.color}"></span>${showCount?`<span class="citation-number">${$kb.core.esc(c.label)}</span>`:''}</span>`;}

function citationLegend(){return `<details class="citation-legend"><summary>被引量圆圈图例</summary><div>${[[null,'未知'],[0,'0'],[1,'1'],[10,'10'],[100,'100'],[1000,'≥1,000']].map(([n,label])=>`<span>${citationBadge({citation_count:n})}${label}</span>`).join('')}</div><p class="small-text muted">浅灰绿 → 深青绿：被引量越多，颜色越深；1,000次及以上使用最深色。未知为空心圈。悬停查看次数、来源与更新时间。</p></details>`;}

function referenceListHTML(data,query='',sort='original',limit=20){
  let rows=(data.items||[]).filter(r=>[r.title,r.raw,r.doi,(r.authors||[]).toString()].join(' ').toLowerCase().includes(query.toLowerCase()));
  if(sort==='citations')rows.sort((a,b)=>(citationView(b).count??-1)-(citationView(a).count??-1));
  if(sort==='year')rows.sort((a,b)=>(b.year||0)-(a.year||0));
  const total=rows.length;
  return `<ol class="reference-list">${rows.slice(0,limit).map((r,i)=>`<li><div class="reference-number">${r.ref_index?'['+$kb.core.esc(r.ref_index)+']':i+1}</div><div class="reference-body"><div class="reference-title">${citationBadge(r)}${r.paper_id?`<button class="link-button" data-reference-paper="${$kb.core.esc(r.paper_id)}">${$kb.core.esc(r.title||r.raw||'题名待补全')}</button>`:`<span>${$kb.core.esc(r.title||r.raw||'题名待补全')}</span>`}</div>${r.association_status==='manual_rejected'?'<span class="badge warn">人工已拒绝关联</span>':''}<p class="small-text muted">${r.year||'年份待补'} · ${r.kind==='library'?'已在 Zotero 库':'库外参考文献'} · ${$kb.core.esc(r.origin||'已有引用记录')}</p><p class="reference-metric small-text">${$kb.core.esc(citationView(r).label)}${r.citation_source?' · '+$kb.core.esc(r.citation_source):''}${r.citation_updated?' · '+$kb.core.fmtDate(r.citation_updated):''}</p>${r.raw?`<details><summary>原始参考条目</summary><p>${$kb.core.esc(r.raw)}</p></details>`:''}${r.doi?`<a class="small-text" target="_blank" rel="noopener" href="https://doi.org/${$kb.core.esc(encodeURI(r.doi))}">DOI：${$kb.core.esc(r.doi)} ↗</a>`:''}</div></li>`).join('')}</ol>`+(total>limit?`<button class="button small" id="references-more">继续显示其余 ${total-limit} 条</button>`:'')+(total?'':'<p class="muted">当前没有匹配的参考文献。</p>');
}

function staticReferences(data,paper){
  const saved=data.references?.[paper.id];
  let items=saved?.items||data.edges.filter(e=>e.src===paper.id&&e.kind==='cites'&&e.status!=='rejected').map(e=>{const p=data.nodes.find(n=>n.id===e.dst);return p?{...p,paper_id:p.id,origin:'离线引用缓存'}:null;}).filter(Boolean);
  items=items.map(row=>{const p=data.nodes.find(n=>n.id===row.paper_id);return p?{...row,citation_count:citationView(p).count,citation_source:p.citation_source||(p.openalex_id?'OpenAlex 缓存':''),citation_updated:p.citation_updated}:row;});
  return {...saved,items,paper_id:paper.id,total:items.length,citation:paper,job:{status:'offline'},sources:saved?.sources||['离线引用缓存'],order_note:'离线快照仅展示已采集记录，自动刷新需打开本地服务'};
}

function mountReferences(data,id,g){
  const host=$kb.core.$('#paper-references');if(!host||g!==$kb.detail.paperGeneration)return;
  clearTimeout($kb.detail.referenceTimer);
  const query=$kb.core.$('#reference-query')?.value||'',sort=$kb.core.$('#reference-sort')?.value||'original',limit=+(host.dataset.limit||20);
  const focus=document.activeElement?.id,selection=focus==='reference-query'?document.activeElement.selectionStart:null;
  const job=data.job||{},busy=['queued','running'].includes(job.status),status={queued:'已排队，自动补全参考文献与被引量',running:'正在获取参考文献与被引量',complete:'自动更新完成',partial:'已保存可用结果，仍有缺口',deferred:'暂未完成，已有缓存继续保留',offline:'离线参考文献快照',cached:'已有引用缓存'}[job.status]||'参考文献缓存';
  host.innerHTML=`<div class="reference-heading"><h3>参考文献 <span class="muted">${data.total??data.items?.length??0}</span></h3>${!window.STATIC_GRAPH?`<button class="button small" id="refresh-references" ${busy?'disabled':''}>刷新</button>`:''}</div><p class="small-text reference-status" role="status">${$kb.core.esc(status)}${data.stale?' · 缓存待更新':''}</p><p class="small-text muted">${$kb.core.esc((data.sources||[]).join(' / '))}${data.updated?' · '+$kb.core.fmtDate(data.updated):''} · ${$kb.core.esc(data.order_note||'已有引用记录，列表可能不完整')}</p><div class="reference-tools"><input type="search" id="reference-query" aria-label="筛选参考文献" placeholder="筛选题名、作者或 DOI" value="${$kb.core.esc(query)}"><select id="reference-sort" aria-label="参考文献排序"><option value="original">来源顺序</option><option value="citations">被引量优先</option><option value="year">年份从新到旧</option></select></div>${citationLegend()}<div id="reference-items">${referenceListHTML(data,query,sort,limit)}</div>${(data.gaps||[]).length||job.error?`<details class="reference-gaps"><summary>采集缺口与来源状态</summary>${[...new Set([...(data.gaps||[]),job.error].filter(Boolean))].map(x=>`<p class="small-text">${$kb.core.esc(x)}</p>`).join('')}</details>`:''}`;
  const citation=$kb.core.$('#paper-citation');if(citation&&data.citation)citation.innerHTML=citationBadge(data.citation,true);
  const titleBadge=$kb.core.$('#detail h2>.citation-badge');if(titleBadge&&data.citation)titleBadge.outerHTML=citationBadge(data.citation);
  const node=$kb.core.state.nodes.find(p=>p.id===id);if(node&&data.citation)Object.assign(node,data.citation);
  $kb.core.$('#reference-sort').value=sort;
  function bind(){host.querySelectorAll('[data-reference-paper]').forEach(b=>b.onclick=()=>$kb.detail.showPaper(b.dataset.referencePaper,{parent:id}));const more=$kb.core.$('#references-more');if(more)more.onclick=()=>{host.dataset.limit=+(host.dataset.limit||20)+20;draw();};}
  function draw(){const q=$kb.core.$('#reference-query').value,s=$kb.core.$('#reference-sort').value;$kb.core.$('#reference-items').innerHTML=referenceListHTML(data,q,s,+(host.dataset.limit||20));bind();}
  $kb.core.$('#reference-query').oninput=()=>{host.dataset.limit=20;draw();};$kb.core.$('#reference-sort').onchange=()=>{host.dataset.limit=20;draw();};bind();
  if($kb.core.$('#refresh-references'))$kb.core.$('#refresh-references').onclick=async()=>{try{const result=await $kb.core.api('/api/references/refresh',{paper_id:id});if(result.notice)$kb.core.toast(result.notice);mountReferences(result,id,g);}catch(e){$kb.feedback.showError(e.message);}};
  if(focus==='reference-query'){$kb.core.$('#reference-query').focus();$kb.core.$('#reference-query').setSelectionRange(selection,selection);}
  if(busy&&!window.STATIC_GRAPH)$kb.detail.referenceTimer=setTimeout(async()=>{if(g!==$kb.detail.paperGeneration||$kb.core.state.detail!==id)return;try{mountReferences(await $kb.core.api('/api/references?paper='+encodeURIComponent(id)),id,g);}catch(e){if(g===$kb.detail.paperGeneration&&$kb.core.$('#paper-references .reference-status'))$kb.core.$('#paper-references .reference-status').textContent='连接中断，已完成的参考文献缓存保留';}},1800);
}
return {get citationView(){return citationView;},set citationView(value){citationView=value;},
get citationBadge(){return citationBadge;},set citationBadge(value){citationBadge=value;},
get citationLegend(){return citationLegend;},set citationLegend(value){citationLegend=value;},
get referenceListHTML(){return referenceListHTML;},set referenceListHTML(value){referenceListHTML=value;},
get staticReferences(){return staticReferences;},set staticReferences(value){staticReferences=value;},
get mountReferences(){return mountReferences;},set mountReferences(value){mountReferences=value;}};
});
