/* graph：通过 ctx 访问其他模块；变量保留在本模块闭包内。 */
KB.define('graph',($kb)=>{'use strict';
let graphTimer;

function graphPage(){
  const s=$kb.core.state.status;
  $kb.core.$('#main').innerHTML=$kb.core.intro('','文献演化谱系','按方法大类查看全库文献、引用事实与研究关系，选择研究方向后方法列表同步更新。',`<div class="stats-inline"><div><b id="graph-library-count">${s.papers==null?'—':s.papers.toLocaleString()}</b><small>库内文献</small></div><div><b id="graph-external-count">${s.external==null?'—':s.external.toLocaleString()}</b><small>库外节点</small></div></div>`)+
  `<div class="view-tabs" role="group" aria-label="图谱视图"><button class="button" data-view="chains">研究链</button><button class="button" data-view="timeline">时间演化</button><button class="button" data-view="network">引用网络</button></div>`+
  `<div class="toolbar"><label><input id="graph-project" type="checkbox" ${$kb.core.state.graphQuery.project?'checked':''}>当前课题</label><select id="graph-direction" aria-label="研究方向">${$kb.core.directionOptions()}</select><input id="graph-search" type="search" placeholder="查找论文、作者关键词或 citekey" aria-label="查找图谱文献" value="${$kb.core.esc($kb.core.state.graphQuery.q)}"><label><input id="show-candidates" type="checkbox" ${$kb.core.state.graphQuery.candidates?'checked':''}>显示待核关系</label><span class="spacer"></span><button class="button small" id="zoom-out">−</button><button class="button small" id="zoom-fit">适合窗口</button><button class="button small" id="zoom-in">＋</button></div>
  <div id="direction-coverage"></div>
  <div id="stale-banner" class="stale-banner" hidden><span id="stale-text"></span><button class="button small" id="stale-refresh">刷新视图</button></div>
  <div id="lineage-panel"></div><div class="graph-card" id="network-panel">
  <div class="graph-top"><strong id="graph-count">正在整理网络…</strong><span id="graph-order">前作 → 后作</span><button type="button" class="legend-toggle" data-toggle="legend" aria-expanded="false" aria-controls="graph-legend">图例</button><div class="legend" id="graph-legend"></div></div>
  <div class="net-controls" id="net-controls"><label>着色<select id="net-color" aria-label="节点着色方式"><option value="direction">研究方向</option><option value="year">发表年份</option></select></label>
  <label>关系<select id="net-relation" aria-label="关系筛选"><option value="all">全部关系</option><option value="cites">仅引用</option><option value="semantic">仅已核方法关系</option></select></label>
  <label>标签<select id="net-labels" aria-label="节点标签密度"><option value="auto">自动</option><option value="all">全部</option><option value="off">关闭</option></select></label>
  <span class="spacer"></span><span class="net-zoom" id="net-zoom">100%</span><button class="button small" id="net-relayout">重新布局</button></div>
  <div class="graph-scroll" id="graph-canvas"><svg id="graph-svg" role="group" aria-label="文献引用与研究方法关系网络"></svg><div class="net-tools" role="group" aria-label="画布缩放"><button type="button" data-net-zoom="in" aria-label="放大" title="放大"><svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M3 8h10M8 3v10" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg></button><button type="button" data-net-zoom="out" aria-label="缩小" title="缩小"><svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M3 8h10" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg></button><button type="button" data-net-zoom="fit" aria-label="适应窗口" title="适应窗口"><svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M2 6V2h4M14 6V2h-4M2 10v4h4M14 10v4h-4" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg></button></div><div class="net-tip" id="net-tip" hidden></div></div>
  <div class="graph-footer"><span id="graph-hint">拖拽画布平移 · 滚轮或双指缩放 · 拖动节点可固定 · 双击空白适应窗口</span><span id="graph-legend-note">细线：引用事实　粗线：已核研究关系</span></div></div>
  <div class="load-row" id="network-pagination"><button class="button" id="graph-more" hidden>继续载入文献</button><span id="graph-page-info"></span></div>`;
  $kb.core.$('#direction-coverage').insertAdjacentHTML('afterend',$kb.references.citationLegend());
  $kb.core.$('#graph-direction').value=$kb.core.state.graphQuery.direction;
  $kb.core.$('#graph-direction').insertAdjacentHTML('afterend','<select id="graph-method" aria-label="方法大类"><option value="">全部方法大类</option></select>');
  $kb.core.$('#graph-method').onchange=e=>{$kb.core.state.graphQuery.method=e.target.value;loadGraph();};
  $kb.core.$('#graph-project').onchange=e=>{$kb.core.state.graphQuery.project=e.target.checked;$kb.core.state.graphQuery.method='';loadGraph();};
  $kb.core.$('#graph-direction').onchange=e=>{$kb.core.state.graphQuery.direction=e.target.value;$kb.core.state.graphQuery.method='';loadGraph();};
  $kb.core.$('#show-candidates').onchange=e=>{$kb.core.state.graphQuery.candidates=e.target.checked;loadGraph();};
  $kb.core.$('#graph-search').oninput=e=>{clearTimeout(graphTimer);graphTimer=setTimeout(()=>{$kb.core.state.graphQuery.q=e.target.value;loadGraph();},350);};
  $kb.core.$('#graph-more').onclick=()=>loadGraph(true);
  $kb.core.$('#zoom-in').onclick=()=>{if($kb.core.state.graphMode==='network'){netZoom(1.3);return;}$kb.core.state.timelineFit=false;$kb.core.state.zoom=Math.min(2,$kb.core.state.zoom*1.25);drawTimeline();};
  $kb.core.$('#zoom-out').onclick=()=>{if($kb.core.state.graphMode==='network'){netZoom(1/1.3);return;}$kb.core.state.timelineFit=false;$kb.core.state.zoom=Math.max(.35,$kb.core.state.zoom/1.25);drawTimeline();};
  $kb.core.$('#zoom-fit').onclick=()=>{if($kb.core.state.graphMode==='network'){netFit();return;}$kb.core.state.timelineFit=true;$kb.core.state.zoom=1;drawTimeline();};
  $kb.core.$('#net-color').onchange=e=>{NET.colorBy=e.target.value;drawNetwork();};
  $kb.core.$('#net-relation').onchange=e=>{NET.relation=e.target.value;drawNetwork();};
  $kb.core.$('#net-labels').onchange=e=>{NET.labels=e.target.value;netApplyLabels();};
  $kb.core.$('#net-relayout').onclick=()=>netRelayout();
  $kb.core.$$('[data-net-zoom]').forEach(b=>b.onclick=()=>{const a=b.dataset.netZoom;if(a==='in')netZoom(1.3);else if(a==='out')netZoom(1/1.3);else netFit();});
  $kb.core.$$('[data-view]').forEach(b=>b.onclick=()=>{clearTimeout(graphTimer);$kb.core.state.graphMode=b.dataset.view;loadGraph();});
  $kb.core.$('#direction-coverage').insertAdjacentHTML('beforebegin',$kb.feedback.verificationPanelHTML());$kb.status.renderMethodProgress();
  $kb.core.$('#stale-refresh').onclick=()=>{hideStaleBanner();reloadGraphKeepingPosition();};
  $kb.core.api('/api/overview').then(renderOverview).catch(()=>{});
  return loadGraph(false,'',true);
}

function renderOverview(data){
  if(!$kb.core.$('#direction-coverage'))return;
  const wasOpen=$kb.core.$('#direction-coverage details')?.open;
  $kb.core.$('#direction-coverage').innerHTML=`<details class="direction-coverage"><summary>全库 ${data.directions.length} 个研究方向 · 已核方法关系 ${data.confirmed} 条 · 待核关系 ${data.candidates} 条</summary><p class="small-text muted">各方向文献已经进入索引。方法关系需单独核实，向量计算进度不会自动增加已核关系。点击方向筛选；“时间演化”同时展示旧版引用路径，并明确标注待核。</p><div class="direction-grid">${data.directions.map(d=>`<button data-direction="${d.direction}"><strong>${$kb.core.esc(d.name)}</strong><span>${d.papers} 篇 · 已核 ${d.confirmed} · 待核 ${d.candidates}</span></button>`).join('')}</div></details>`;
  $kb.core.$$('#direction-coverage [data-direction]').forEach(b=>b.onclick=()=>{$kb.core.state.graphQuery.direction=b.dataset.direction;$kb.core.state.graphQuery.method='';$kb.core.$('#graph-direction').value=b.dataset.direction;loadGraph();});
  if(wasOpen)$kb.core.$('#direction-coverage details').open=true;
}

let methodOptionsGeneration=0;
async function refreshMethodOptions(){
  const select=$kb.core.$('#graph-method');if(!select)return;
  const ticket=++methodOptionsGeneration;
  const {direction,project,q}=$kb.core.state.graphQuery;
  select.disabled=true;
  try{
    const data=await $kb.core.api('/api/methods?'+new URLSearchParams({direction,project,q}));
    if(ticket!==methodOptionsGeneration||$kb.core.$('#graph-method')!==select)return;
    const selected=$kb.core.state.graphQuery.method||'';
    if(!data.items.some(item=>item.id===selected))$kb.core.state.graphQuery.method='';
    select.innerHTML='<option value="">全部方法大类</option>'+data.items.map(item=>`<option value="${$kb.core.esc(item.id)}">${$kb.core.esc(item.name)} · ${item.papers} 篇</option>`).join('');
    select.value=$kb.core.state.graphQuery.method||'';select.disabled=false;
  }catch(error){
    if(ticket!==methodOptionsGeneration||$kb.core.$('#graph-method')!==select)return;
    select.innerHTML='<option value="">方法列表暂不可用</option>';$kb.core.state.graphQuery.method='';
    $kb.feedback.showError(error.message);
  }
}

let graphGeneration=0;

async function loadGraph(append=false,pid='',restore=false){
  if(pid)$kb.core.state.graphMode='network';
  if($kb.core.state.page!=='graph'||!$kb.core.$('#lineage-panel')||!$kb.core.$('#network-panel')||!$kb.core.$('#show-candidates')||!$kb.core.$('#graph-count'))return;
  hideLineageTooltip();
  const generation=++graphGeneration,offset=append?$kb.core.state.nodes.length:0;
  if(!append&&$kb.core.$('#graph-method'))await refreshMethodOptions();
  if(generation!==graphGeneration||$kb.core.state.page!=='graph')return;
  $kb.status.renderMethodProgress();
  const chainMode=$kb.core.state.graphMode==='chains';
  const listMode=chainMode||$kb.core.state.graphMode==='timeline';
  $kb.core.$$('[data-view]').forEach(b=>{b.classList.toggle('primary',b.dataset.view===$kb.core.state.graphMode);b.setAttribute('aria-pressed',b.dataset.view===$kb.core.state.graphMode);});
  $kb.core.$('#lineage-panel').hidden=!listMode;$kb.core.$('#network-panel').hidden=listMode;$kb.core.$('#network-pagination').hidden=listMode;
  ['zoom-out','zoom-fit','zoom-in'].forEach(id=>$kb.core.$('#'+id).hidden=true);
  $kb.core.$('#net-controls').hidden=$kb.core.state.graphMode!=='network';
  document.body.classList.toggle('net-view',$kb.core.state.graphMode==='network');
  $kb.core.$('#show-candidates').closest('label').hidden=$kb.core.state.graphMode==='timeline';
  const svgEl=$kb.core.$('#graph-svg');
  if(svgEl)svgEl.setAttribute('aria-label',$kb.core.state.graphMode==='timeline'?'时间演化视图：按研究方向分组的已核方法链与引用路径':'文献引用与研究方法关系网络：节点可聚焦，回车打开详情');
  const note=$kb.core.$('#graph-legend-note');
  if(note)note.textContent=$kb.core.state.graphMode==='timeline'?'实线：已核方法关系　虚线：引用路径（方法含义待核）':'细线：引用事实　粗线：已核研究关系';
  if($kb.core.state.graphMode!=='network'&&$kb.core.$('#graph-legend'))$kb.core.$('#graph-legend').innerHTML='';
  $kb.core.$('#graph-order').textContent=$kb.core.state.graphMode==='timeline'?'等距分链 · 年份标在节点上':'节点大小 = 关系数量 · 箭头由前作指向后作';
  $kb.core.$('#zoom-fit').textContent=$kb.core.state.graphMode==='timeline'?'适合窗口':'适应窗口';
  if(chainMode){
    const key=JSON.stringify($kb.core.state.graphQuery),epoch=$kb.core.readCacheEpoch||0,saved=rememberedChains.get(key);
    if(restore&&saved&&saved.epoch===epoch&&saved.observedAt&&!saved.error&&!saved.stale&&!saved.loading){
      chainView=saved;chainView.generation=generation;
      const pending=[];for(const [id,branch] of chainView.details){if(branch.loading){branch.loading=false;if(branch.open)pending.push(id);}}
      renderChainSummaries(chainView);
      if(saved.observedAt<=Date.now()-15000)revalidateChains(chainView);
      for(const id of pending)loadChainBranch(chainView,id);
      return;
    }
    chainView={generation,epoch,query:{...$kb.core.state.graphQuery},items:[],details:new Map(),revision:'',total:0,hasMore:false,loading:false,error:''};
    rememberedChains.set(key,chainView);while(rememberedChains.size>4)rememberedChains.delete(rememberedChains.keys().next().value);
    $kb.core.state.nodes=[];$kb.core.state.edges=[];$kb.core.state.chainGroups=[];
    hideStaleBanner();
    await loadChainSummaries(chainView);
    return;
  }
  $kb.core.$('#graph-count').textContent='正在载入网络…';
  try{
    if($kb.core.state.graphMode==='timeline'){
      $kb.core.$('#lineage-panel').innerHTML=$kb.core.loading();
      $kb.core.$('#show-candidates').closest('label').hidden=true;
      const data=await $kb.core.api('/api/timeline?'+new URLSearchParams({...$kb.core.state.graphQuery,compact:true}));if(generation!==graphGeneration||$kb.core.state.page!=='graph')return;
      $kb.core.state.timeline=data;$kb.core.state.nodes=[...new Map(data.groups.flatMap(g=>g.paths.flatMap(c=>c.nodes)).map(p=>[p.id,p])).values()];$kb.core.state.edges=[...new Map(data.groups.flatMap(g=>g.paths.flatMap(c=>c.edges)).map(e=>[e.id,e])).values()];
      $kb.core.$('#lineage-panel').hidden=false;$kb.core.$('#network-panel').hidden=true;$kb.core.$('#network-pagination').hidden=true;renderMethodTimeline(data);return;
      $kb.core.$('#graph-count').textContent=`${data.groups.length} 个方向 · ${data.groups.reduce((n,g)=>n+g.paths.length,0)} 条已核链与引用路径`;
      $kb.core.$('#graph-hint').textContent='实线：已核方法关系；虚线：引用路径，尚待核实方法含义。年份跨度不挤压节点间距。';
      $kb.core.$('#graph-more').hidden=true;$kb.core.$('#graph-page-info').textContent='每个方向先显示 3 条路径，可逐方向展开。';drawTimeline();return;
    }
    const params=new URLSearchParams({...$kb.core.state.graphQuery,offset,limit:180,paper:pid});
    const g=await $kb.core.api('/api/graph?'+params);
    if(generation!==graphGeneration||$kb.core.state.page!=='graph')return;
    $kb.core.state.nodes=append?[...$kb.core.state.nodes,...g.nodes]:g.nodes;
    const rel=await $kb.core.api('/api/graph/edges',{ids:$kb.core.state.nodes.map(n=>n.id),candidates:$kb.core.state.graphQuery.candidates});
    if(generation!==graphGeneration||$kb.core.state.page!=='graph')return;
    $kb.core.state.edges=$kb.core.state.graphMode==='timeline'?rel.edges.filter(e=>e.kind!=='cites'):rel.edges;
    $kb.core.$('#graph-count').textContent=`已显示 ${$kb.core.state.nodes.length} / ${g.total.toLocaleString()} 个节点`;
    $kb.core.$('#graph-page-info').textContent=`当前关系 ${$kb.core.state.edges.length.toLocaleString()} 条 · 全量数据保留在本地`;
    $kb.core.$('#graph-more').hidden=!g.has_more||$kb.core.state.nodes.length>=1800;
    $kb.core.$('#graph-hint').textContent=pid?'当前文献的直接上下游邻域 · 拖动可固定节点':'拖拽画布平移 · 滚轮缩放 · 拖动节点固定 · 双击空白适应窗口';
    drawGraph();
  }catch(e){$kb.core.$('#graph-count').textContent='图谱暂时无法载入';$kb.feedback.showError(e.message,{title:'图谱暂时无法载入'});}
}

let chainView=null;
const rememberedChains=new Map();

function currentChains(view){
  return chainView===view&&view.generation===graphGeneration&&$kb.core.state.page==='graph'&&$kb.core.state.graphMode==='chains'&&Boolean($kb.core.$('#lineage-panel'));
}

async function revalidateChains(view){
  if(!currentChains(view)||view.revalidating)return;
  view.revalidating=true;
  try{
    const params=new URLSearchParams({...view.query,offset:0,limit:20,revision:''});
    const data=window.STATIC_GRAPH?offlineChainPage(view):await $kb.core.api('/api/chains/summary?'+params);
    if(!currentChains(view))return;
    if(data.revision!==view.revision){view.stale=true;showStaleBanner('研究链已更新，刷新视图后显示新结果。');}
    else{view.observedAt=Date.now();view.coverage=data.coverage;}
  }catch(error){if(currentChains(view))showStaleBanner('暂时无法检查更新，当前保留已载入的研究链。');}
  finally{view.revalidating=false;}
}

function offlineChainPage(view,id='',offset=0,limit=20,paperOffset=0){
  const data=window.STATIC_GRAPH;
  if(!view.offline){
    let branches=data.chains||[];
    if(data.taxonomy){
      const papers=$kb.core.methodScope(data,view.query.direction||''),byId=new Map(papers.map(p=>[p.id,p])),groups=new Map(),connected=new Set();
      const group=family=>{if(!groups.has(family))groups.set(family,{id:data.taxonomy.branches[family],family_id:family,name:data.taxonomy.families[family],nodes:new Map(),edges:[]});return groups.get(family);};
      for(const edge of data.edges){
        const src=byId.get(edge.src),dst=byId.get(edge.dst);if(!src||!dst||edge.status==='rejected')continue;
        const family=src.primary_method_family==='family:other'?dst.primary_method_family:src.primary_method_family;
        const branch=group(family);branch.edges.push(edge);branch.nodes.set(src.id,src);branch.nodes.set(dst.id,dst);connected.add(src.id);connected.add(dst.id);
      }
      for(const p of papers)if(!connected.has(p.id))group(p.primary_method_family).nodes.set(p.id,p);
      branches=[...groups.values()].map(b=>({...b,nodes:[...b.nodes.values()]}));
    }
    view.offline=branches.map((branch,i)=>{
      const edges=branch.edges.filter(edge=>edge.status!=='rejected'&&(view.query.candidates||['confirmed','citation'].includes(lineageRelationView(edge,branch.nodes).grade)));
      if(view.query.method?.startsWith('family:')&&branch.family_id&&branch.family_id!==view.query.method)return {...branch,nodes:[],edges:[]};
      const nodes=scopedGraphNodes(branch.nodes,edges,view.query),keep=new Set(nodes.map(node=>node.id));
      return {...branch,id:branch.id||'offline-'+i,nodes,edges:edges.filter(edge=>keep.has(edge.src)&&keep.has(edge.dst))};
    }).filter(branch=>branch.nodes.length);
  }
  if(id){
    const branch=view.offline.find(b=>b.id===id),edges=branch.edges.slice(offset,offset+limit),ids=new Set(edges.flatMap(edge=>[edge.src,edge.dst]));
    const connected=new Set(branch.edges.flatMap(edge=>[edge.src,edge.dst])),unlinked=branch.nodes.filter(p=>!connected.has(p.id));
    return {...branch,nodes:branch.nodes.filter(p=>ids.has(p.id)),edges,total:branch.edges.length,offset,limit,has_more:offset+limit<branch.edges.length,revision:'offline',
      unlinked_nodes:unlinked.slice(paperOffset,paperOffset+40),unlinked_total:unlinked.length,paper_offset:paperOffset,paper_has_more:paperOffset+40<unlinked.length};
  }
  const methodKinds=new Set(['inherits','improves','combines','transfers']);
  const items=view.offline.map(branch=>{
    const confirmed=branch.edges.filter(edge=>methodKinds.has(edge.kind)&&lineageRelationView(edge,branch.nodes).grade==='confirmed').length;
    const citations=branch.edges.filter(edge=>edge.kind==='cites').length,method_edges=branch.edges.filter(edge=>methodKinds.has(edge.kind)).length;
    const pending=branch.edges.filter(edge=>!['confirmed','citation'].includes(lineageRelationView(edge,branch.nodes).grade)).length;
    return {id:branch.id,name:branch.name,nodes:branch.nodes.length,edges:branch.edges.length,confirmed,fulltext:confirmed,pending,citations,method_edges,other_edges:branch.edges.length-citations-method_edges};
  }).sort((a,b)=>(b.confirmed*5+b.method_edges*2+b.edges)-(a.confirmed*5+a.method_edges*2+a.edges)||a.name.localeCompare(b.name));
  const ids=new Set(view.offline.flatMap(b=>b.nodes.map(p=>p.id))),connected=new Set(view.offline.flatMap(b=>b.edges.flatMap(e=>[e.src,e.dst])));
  const coverage={library_papers:data.nodes?.length||ids.size,papers:ids.size,connected:connected.size,unlinked:ids.size-connected.size};
  for(const key of ['edges','citations','method_edges','other_edges','confirmed','pending'])coverage[key]=items.reduce((sum,b)=>sum+b[key],0);
  return {items:items.slice(offset,offset+limit),coverage,total:items.length,offset,limit,has_more:offset+limit<items.length,revision:'offline'};
}

function chainRevision(view,data){
  if(view.revision&&view.revision!==data.revision){const error=Error('研究链已更新，请刷新视图后继续。');error.status=409;throw error;}
}

async function loadChainSummaries(view){
  if(!currentChains(view)||view.loading)return;
  view.loading=true;view.error='';renderChainSummaries(view);
  try{
    const params=new URLSearchParams({...view.query,offset:view.items.length,limit:20,revision:view.revision});
    const data=window.STATIC_GRAPH?offlineChainPage(view,'',view.items.length,20):await $kb.core.api('/api/chains/summary?'+params);
    if(!currentChains(view))return;
    chainRevision(view,data);view.revision=data.revision;
    view.items=[...new Map([...view.items,...data.items].map(item=>[item.id,item])).values()];
    view.total=data.total;view.hasMore=data.has_more;view.coverage=data.coverage;view.observedAt=Date.now();
  }catch(error){
    if(!currentChains(view))return;
    view.error=error.message;
    if(error.status===409){view.stale=true;showStaleBanner(error.message);}
  }finally{if(currentChains(view)){view.loading=false;renderChainSummaries(view);}}
}

async function loadChainBranch(view,id,papersOnly=false){
  if(!currentChains(view)||view.stale)return;
  const branch=view.details.get(id);
  if(!branch||branch.loading)return;
  branch.loading=true;branch.error='';renderChainSummaries(view);
  try{
    const params=new URLSearchParams({...view.query,offset:papersOnly?0:branch.offset,limit:40,paper_offset:branch.paperOffset||0,paper_limit:40,revision:view.revision});
    const data=window.STATIC_GRAPH?offlineChainPage(view,id,papersOnly?0:branch.offset,40,branch.paperOffset||0):await $kb.core.api('/api/chains/'+encodeURIComponent(id)+'?'+params);
    if(!currentChains(view))return;
    chainRevision(view,data);
    branch.nodes=[...new Map([...branch.nodes,...data.nodes].map(node=>[node.id,node])).values()];
    branch.edges=[...new Map([...branch.edges,...data.edges].map(edge=>[edge.id,edge])).values()];
    if(!papersOnly){branch.offset=data.offset+data.edges.length;branch.hasMore=data.has_more;}
    branch.unlinked=[...new Map([...(branch.unlinked||[]),...(data.unlinked_nodes||[])].map(node=>[node.id,node])).values()];
    branch.paperOffset=(data.paper_offset||0)+(data.unlinked_nodes||[]).length;branch.paperHasMore=Boolean(data.paper_has_more);branch.unlinkedTotal=data.unlinked_total||0;branch.loaded=true;
  }catch(error){
    if(!currentChains(view))return;
    branch.error=error.message;
    if(error.status===409){view.stale=true;showStaleBanner(error.message);}
  }finally{if(currentChains(view)){branch.loading=false;renderChainSummaries(view);}}
}

function renderChainSummaries(view){
  if(!currentChains(view))return;
  hideLineageTooltip();
  const groups=view.items.filter(item=>view.details.get(item.id)?.open).map(item=>({...item,...view.details.get(item.id)}));
  $kb.core.state.chainGroups=groups;
  $kb.core.state.nodes=[...new Map(groups.flatMap(c=>c.nodes).map(p=>[p.id,p])).values()];
  $kb.core.state.edges=[...new Map(groups.flatMap(c=>c.edges).map(e=>[e.id,e])).values()];
  const esc=$kb.core.esc;
  const coverage=view.coverage;
  $kb.core.$('#lineage-panel').innerHTML=(coverage?`<p class="lineage-summary">全库 ${coverage.library_papers.toLocaleString()} 篇 · 当前范围 ${coverage.papers.toLocaleString()} 篇 · ${coverage.edges.toLocaleString()} 条关系（引用 ${coverage.citations.toLocaleString()} · 方法 ${coverage.method_edges.toLocaleString()} · 其他 ${coverage.other_edges.toLocaleString()}）</p><p class="small-text muted">${coverage.unlinked.toLocaleString()} 篇尚未建立关系，列于各大类的文献列表。跨类引用保留两端文献，每条关系只统计一次。分类用于组织展示，不表示方法继承已获证实。</p>`:'')+`<p class="lineage-summary">已显示 ${view.items.length} / ${view.total} 个方法大类 · 展开后分页浏览全库关系</p>`+lineageLegendHTML()+view.items.map(item=>{
    const branch=view.details.get(item.id),open=Boolean(branch?.open);
    let body='';
    if(open){
      if(branch.loaded){
        body=`<p class="small-text muted">已载入 ${branch.edges.length} / ${item.edges} 条关系（当前页含完整端点；尚未加载的关系不表示不存在）</p><div class="lineage-canvas" id="chain-${esc(item.id)}"></div><div class="lineage-relations">`+branch.edges.map(edge=>{
          const rel=lineageRelationView(edge,branch.nodes);
          return `<button class="lineage-relation" data-evidence="${esc(edge.id)}" style="--relation-color:${rel.color}"><span class="badge">${esc(rel.name)}</span><span>${esc(rel.from)} → ${esc(rel.to)}</span><span>${esc(rel.change)}</span><span>${esc(rel.evidence)} · 查看证据</span></button>`;
        }).join('')+'</div>';
        if(!branch.edges.length)body+=$kb.core.empty('当前范围暂无关系','下方保留尚未建立关系的文献。');
        if(branch.unlinked?.length)body+=`<details><summary>尚未建立关系的文献 · 已载入 ${branch.unlinked.length} / ${branch.unlinkedTotal} 篇</summary><div class="lineage-relations">${branch.unlinked.map(p=>`<button class="lineage-relation" data-unlinked-paper="${esc(p.id)}">${esc(p.year||'年份待核')} · ${esc(p.title)}</button>`).join('')}</div></details>`;
      }
      if(branch.loading)body+=$kb.core.loading();
      if(branch.error)body+=`<p role="alert">${esc(branch.error)}</p>${view.stale?'':`<button class="button" data-chain-retry="${esc(item.id)}">重试加载分支</button>`}`;
      if(branch.loaded&&branch.paperHasMore&&!branch.loading&&!branch.error&&!view.stale)body+=`<button class="button" data-chain-papers="${esc(item.id)}">继续载入未建立关系的文献</button>`;
      if(branch.loaded&&branch.hasMore&&!branch.loading&&!branch.error&&!view.stale)body+=`<button class="button" data-chain-more="${esc(item.id)}">继续载入该分支关系</button>`;
    }
    return `<article class="lineage-group"><div class="lineage-heading"><h2>${esc(item.name)}</h2><span>${item.nodes} 篇 · 引用 ${item.citations||0} · 方法 ${item.method_edges??item.edges} · 其他 ${item.other_edges||0} · 方法已核 ${item.confirmed} · 待核 ${item.pending}</span><button class="button" data-chain-toggle="${esc(item.id)}" aria-expanded="${open}">${open?'收起':'展开分支'}</button></div>${body}</article>`;
  }).join('')+(view.loading?$kb.core.loading():'')+(view.error?`<p role="alert">${esc(view.error)}</p><button class="button" id="chains-retry">${view.stale?'刷新研究链':'重试加载摘要'}</button>`:'')+(!view.loading&&!view.error&&!view.items.length?$kb.core.empty('当前筛选下暂无研究链','可以调整筛选，或勾选“显示待核关系”查看分级线索。'):'')+(view.hasMore&&!view.loading&&!view.error?'<button class="button" id="chains-more">继续载入分支摘要</button>':'');
  $kb.core.$$('#lineage-panel [data-chain-toggle]').forEach(button=>button.onclick=()=>{
    const id=button.dataset.chainToggle;
    let branch=view.details.get(id);
    if(!branch){branch={open:false,nodes:[],edges:[],offset:0,loaded:false,loading:false,hasMore:false,error:''};view.details.set(id,branch);}
    branch.open=!branch.open;
    if(branch.open&&!branch.loaded&&!branch.loading&&!view.stale)return loadChainBranch(view,id);
    renderChainSummaries(view);
  });
  $kb.core.$$('#lineage-panel [data-chain-more]').forEach(button=>button.onclick=()=>loadChainBranch(view,button.dataset.chainMore));
  $kb.core.$$('#lineage-panel [data-chain-retry]').forEach(button=>button.onclick=()=>loadChainBranch(view,button.dataset.chainRetry));
  $kb.core.$$('#lineage-panel [data-chain-papers]').forEach(button=>button.onclick=()=>loadChainBranch(view,button.dataset.chainPapers,true));
  $kb.core.$$('#lineage-panel [data-unlinked-paper]').forEach(button=>button.onclick=()=>$kb.detail.showPaper(button.dataset.unlinkedPaper));
  const more=$kb.core.$('#chains-more');if(more)more.onclick=()=>loadChainSummaries(view);
  const retry=$kb.core.$('#chains-retry');if(retry)retry.onclick=()=>view.stale?loadGraph():loadChainSummaries(view);
  groups.forEach(branch=>{const host=$kb.core.$('#chain-'+branch.id);if(host)drawLineage(branch,host);});
  $kb.core.$$('#lineage-panel [data-evidence]').forEach(button=>button.onclick=()=>openRelation($kb.core.state.edges.find(edge=>edge.id===button.dataset.evidence)));
}

async function openRelation(edge){
  if(!edge)return;
  try{const full=Array.isArray(edge.evidence)?edge:await $kb.core.api('/api/relation?rid='+encodeURIComponent(edge.id));$kb.detail.showRelation(full);}
  catch(error){$kb.feedback.showError(error.message);}
}

function shortPaper(p){return p.citekey||`${p.year||''} ${p.title||''}`;}

function renderMethodTimeline(data){
  const families=(data.method_families||[]).map(c=>{const nodes=scopedGraphNodes(c.nodes,c.edges,$kb.core.state.graphQuery),ids=new Set(nodes.map(p=>p.id));return {...c,nodes:nodes.sort((a,b)=>(a.year||9999)-(b.year||9999)),edges:c.edges.filter(e=>ids.has(e.src)&&ids.has(e.dst))};}).filter(c=>c.edges.length);
  $kb.core.$('#lineage-panel').innerHTML=lineageLegendHTML()+families.map(c=>`<article class="lineage-group"><h2>${$kb.core.esc(c.name)}</h2><ol class="method-events">${c.nodes.map(p=>{const incoming=c.edges.filter(e=>e.src===p.id),type=incoming.some(e=>e.kind==='transfers')?'应用迁移':incoming.some(e=>e.kind==='combines')?'融合':incoming.some(e=>e.kind==='improves'&&e.evidence_grade==='fulltext')?'方法转折':incoming.some(e=>e.kind==='improves')?'转折线索':incoming.length?'方法推进':'已采集起点';return `<li><span class="event-year">${p.year||'待核'}</span><div><small>${type}</small><button class="link-button event-title" data-paper="${$kb.core.esc(p.id)}">${$kb.references.citationBadge(p)}${$kb.core.esc(p.title)}</button><p class="muted small-text">${$kb.core.esc(p.selection_reason||'')}</p>${incoming.map(e=>{const view=lineageRelationView(e,c.nodes);return `<button class="lineage-relation" data-evidence="${$kb.core.esc(e.id)}" style="--relation-color:${view.color}"><span>${$kb.core.esc(view.evidence)}</span><strong>${$kb.core.esc(view.from)} → ${$kb.core.esc(view.to)}</strong><span>前作问题：${$kb.core.esc(e.previous_problem||'尚缺具体说明')}</span><span>后作改变：${$kb.core.esc(view.change)}</span><span>新增条件与代价：${$kb.core.esc(e.conditions||'尚缺原文核对')}</span></button>`;}).join('')}</div></li>`;}).join('')}</ol></article>`).join('')+(families.length?'':$kb.core.empty('当前范围暂无可解释的方法链','引用路径和缺口仍保留，补全谱系任务可逐步添加分级关系。'))+`<details class="citation-paths"><summary>单独展开引用路径（只表示引用事实）</summary>${data.groups.map(g=>`<h3>${$kb.core.esc(g.name)}</h3>${g.paths.filter(p=>p.kind==='citation').map(c=>`<p>${c.nodes.map(p=>`<button class="link-button" data-paper="${$kb.core.esc(p.id)}">${p.year||'?'} ${$kb.core.esc(shortPaper(p))}</button>`).join(' → ')}</p>`).join('')}`).join('')}</details>`;
  $kb.core.state.nodes=[...new Map(families.flatMap(c=>c.nodes).map(p=>[p.id,p])).values()];$kb.core.state.edges=families.flatMap(c=>c.edges);
  $kb.core.$$('#lineage-panel [data-paper]').forEach(b=>b.onclick=()=>$kb.detail.showPaper(b.dataset.paper));$kb.core.$$('#lineage-panel [data-evidence]').forEach(b=>b.onclick=()=>openRelation($kb.core.state.edges.find(e=>e.id===b.dataset.evidence)));
}

const runNames={queued:'排队中',running:'处理中',stopping:'正在停止',interrupted:'已停止，可续跑',failed:'未完成，可续跑',partial:'完成部分，保留缺口',complete:'已完成'};

function runHTML(run){return `<article class="research-task"><div><strong>${$kb.core.esc({maintenance:'全文理解与笔记维护',lineage:'方法谱系补全',search:'证据检索',answer:'逐项核验问答',compare:'论文比较'}[run.kind]||run.kind)}</strong> <span class="badge">${$kb.core.esc(runNames[run.status]||run.status)}</span></div><p>${$kb.core.esc(run.input?.query||'当前课题优先，然后全库增量续跑')}</p><p class="small-text">已处理 ${run.processed??run.completed}/${run.total} 个单元 · 完整完成 ${run.completed} · ${$kb.core.esc(run.result?.role||'协调者')} ${$kb.core.esc(run.result?.message||'')}</p><progress max="${Math.max(1,run.total)}" value="${run.processed??run.completed}" aria-label="已处理任务单元"></progress><div class="toolbar"><button class="button small" data-view-task="${$kb.core.esc(run.id)}">在调度台查看任务</button>${run.result?.answer?`<button class="button small" data-run-result="${$kb.core.esc(run.id)}">查看结论与证据</button>`:''}</div>${run.error?`<details><summary>技术详情</summary><pre>${$kb.core.esc(run.error)}</pre></details>`:''}</article>`;}

async function pollRun(run,generation){
  while(['queued','running','stopping'].includes(run.status)){
    if(generation!==$kb.search.searchGeneration||$kb.core.state.page!=='search')return null;
    $kb.core.$('#answer-area').innerHTML=runHTML(run);bindRunControls();
    await new Promise(resolve=>setTimeout(resolve,1500));
    run=await $kb.core.api('/api/agent-runs/'+encodeURIComponent(run.id));
  }
  if(['failed','interrupted'].includes(run.status))throw Error(run.error||'任务已停止，可在研究调度台续跑');
  return run.result;
}

function bindRunControls(){
  $kb.core.$$('[data-view-task]').forEach(b=>b.onclick=()=>$kb.dashboard.taskDashboard.showTask(b.dataset.viewTask));
  $kb.core.$$('[data-run-result]').forEach(b=>b.onclick=async()=>{const run=await $kb.core.api('/api/agent-runs/'+encodeURIComponent(b.dataset.runResult));showAnswerDialog(run.result);});
}

let answerSerial=0;

function showAnswerDialog(data){$kb.core.$('#dialog-content').innerHTML=$kb.search.answerHTML(data);if(!$kb.core.$('#evidence-dialog').open)$kb.core.$('#evidence-dialog').showModal();}

async function refreshResearchTasks(){return $kb.dashboard.taskDashboard?.refresh();}

async function researchPage(){
  if(!$kb.dashboard.taskDashboard)throw Error('研究调度台资源未载入，请刷新页面后重试');
  return $kb.dashboard.taskDashboard.open();
}

function scopedGraphNodes(nodes,edges,query){
  const matches=p=>(!query.project||p.project)&&(!query.direction||(p.directions||[]).includes(query.direction))&&(!query.method||(p.methods||[]).includes(query.method))&&(!query.q||(p.title+' '+(p.citekey||'')+' '+(p.authors||'')).toLowerCase().includes(query.q.toLowerCase()));
  const anchors=new Set(nodes.filter(matches).map(p=>p.id));
  const keep=new Set(anchors),reasons=new Map([...anchors].map(id=>[id,'符合当前筛选']));
  const semantic=edges.filter(e=>['inherits','improves','combines','transfers'].includes(e.kind)&&e.status!=='rejected');
  let prior=-1;while(prior!==keep.size){prior=keep.size;for(const e of semantic)if(keep.has(e.src)||keep.has(e.dst))for(const id of [e.src,e.dst]){if(!keep.has(id))reasons.set(id,'保留方法链的前驱、后继或桥接节点');keep.add(id);}}
  for(const e of edges)if(e.kind==='cites'&&(anchors.has(e.src)||anchors.has(e.dst)))for(const id of [e.src,e.dst]){keep.add(id);if(!reasons.has(id))reasons.set(id,'当前筛选文献的直接引用邻居');}
  return nodes.filter(p=>keep.has(p.id)).map(p=>({...p,selection_reason:reasons.get(p.id)}));
}

function renderLineage(items){
  hideLineageTooltip();
  const query=$kb.core.state.graphQuery;
  const match=p=>(!query.project||p.project)&&(!query.direction||(p.directions||[]).includes(query.direction))&&(!query.q||(p.title+' '+p.citekey+' '+(p.authors||'')).toLowerCase().includes(query.q.toLowerCase()));
  let hidden=0;
  const groups=items.map(c=>{
    const selected=scopedGraphNodes(c.nodes,c.edges,query),keep=new Set(selected.map(p=>p.id));
    hidden+=c.nodes.length-keep.size;
    if(keep.size<2)return null;
    const nodes=selected;
    const edges=c.edges.filter(e=>keep.has(e.src)&&keep.has(e.dst));
    if(!edges.length)return null;
    return {...c,nodes,edges};
  }).filter(Boolean);
  $kb.core.state.chainGroups=groups;$kb.core.state.nodes=[...new Map(groups.flatMap(c=>c.nodes).map(p=>[p.id,p])).values()];$kb.core.state.edges=groups.flatMap(c=>c.edges);
  $kb.core.$('#lineage-panel').innerHTML=`<p class="lineage-summary">${groups.length} 个研究分支 · ${$kb.core.state.nodes.length} 篇文献 · ${$kb.core.state.edges.length} 条分级关系 <span class="muted">${hidden?'已按筛选隐藏 '+hidden+' 篇 · ':''}同时展示原文已核、摘要支持和待核线索</span></p>`+lineageLegendHTML()+groups.map((c,i)=>`<article class="lineage-group"><div class="lineage-heading"><h2>${$kb.core.esc(c.name)}</h2><span class="muted">${c.nodes.length} 篇 · ${c.edges.length} 条关系</span></div><div class="lineage-canvas" id="lineage-${i}"></div><div class="lineage-relations">${c.edges.map(e=>{const view=lineageRelationView(e,c.nodes);return `<button class="lineage-relation" style="--relation-color:${view.color}" data-evidence="${$kb.core.esc(e.id)}"><span class="badge lineage-relation-badge">${$kb.core.esc(view.name)}</span><span class="relation-path">${$kb.core.esc(view.from)} <b>→</b> ${$kb.core.esc(view.to)}</span><span class="relation-description">${$kb.core.esc(view.change)}</span><span class="relation-evidence">${$kb.core.esc(view.evidence)} · 查看证据 ↗</span></button>`;}).join('')}</div></article>`).join('')+(groups.length?'':$kb.core.empty('当前筛选下还没有已核研究链','可以取消筛选，或在引用网络中查看文献并核对候选关系。',{label:'查看引用网络',act:'to-graph'}));
  groups.forEach((c,i)=>drawLineage(c,$kb.core.$('#lineage-'+i)));
  $kb.core.$$('#lineage-panel [data-evidence]').forEach(b=>b.onclick=()=>openRelation($kb.core.state.edges.find(e=>e.id===b.dataset.evidence)));
}

function lineageYear(p){const year=Number(p.year);return Number.isInteger(year)&&year>0?year:null;}

function lineageRelationView(edge,nodes){
  const byId=new Map(nodes.map(p=>[p.id,p])),older=byId.get(edge.dst),later=byId.get(edge.src);
  const grade=edge.evidence_stale?'title':edge.evidence_grade?(edge.evidence_grade==='fulltext'?'confirmed':edge.evidence_grade):edge.level==='title'||edge.level==='title_only'?'title':edge.level==='abstract'?'abstract':edge.status==='confirmed'?'confirmed':'candidate';
  const label=edge.evidence_stale?'证据待重核':edge.evidence_label|| (grade==='title'?'题目线索，待核':grade==='abstract'?'摘要支持，待原文核验':grade==='confirmed'?(edge.manual?'原文已核 · 人工确认':edge.level==='fulltext'?'原文已核':'已确认'):'关系待核');
  const pages=[...new Set([...(edge.evidence_pages||[]),...(edge.evidence||[]).map(e=>e.page||e.page_start).filter(Boolean)])];
  const conflict=Boolean(older&&later&&lineageYear(older)&&lineageYear(later)&&lineageYear(older)>lineageYear(later));
  return {name:$kb.core.relationNames[edge.kind]||'研究关系',color:REL_COLORS[edge.kind]||'#8a8f98',grade,
    from:older?shortPaper(older):'前作待核',to:later?shortPaper(later):'后作待核',
    change:String(edge.change||edge.explanation||'具体变化待补充'),conditions:String(edge.conditions||''),
    evidence:label+(pages.length?' · 原文 p.'+pages.join('、'):''),conflict};
}

function lineageLegendHTML(){
  return `<div class="lineage-legend" aria-label="研究链图例">${['inherits','improves','combines','transfers','cites','compares','similar'].map(kind=>`<span style="--relation-color:${REL_COLORS[kind]}"><i aria-hidden="true"></i>${$kb.core.esc($kb.core.relationNames[kind])}</span>`).join('')}<small>实线：已核方法或引用事实 · 虚线：摘要支持 · 点线：待核线索；引用不代表方法继承 · 悬停箭头查看具体变化</small></div>`;
}

function layoutLineage(chain,availableWidth){
  // 年份决定列；分支决定行。同年内部才使用拓扑层级，绝不据此移动到其他年份。
  const nodes=[...chain.nodes].sort((a,b)=>(lineageYear(a)||Infinity)-(lineageYear(b)||Infinity)||String(a.id).localeCompare(String(b.id)));
  const byId=new Map(nodes.map(p=>[p.id,p])),edges=chain.edges.filter(e=>byId.has(e.src)&&byId.has(e.dst));
  const out=new Map(nodes.map(p=>[p.id,[]])),incoming=new Map(nodes.map(p=>[p.id,[]]));
  for(const edge of edges){out.get(edge.dst).push(edge.src);incoming.get(edge.src).push(edge.dst);}
  const years=[...new Set(nodes.map(lineageYear))],columns=[],columnById=new Map(),warnings=[];
  for(const year of years){
    const peers=nodes.filter(p=>lineageYear(p)===year),ids=new Set(peers.map(p=>p.id));
    const ranks=new Map(peers.map(p=>[p.id,0])),degrees=new Map(peers.map(p=>[p.id,incoming.get(p.id).filter(id=>ids.has(id)).length]));
    const queue=peers.filter(p=>!degrees.get(p.id)).map(p=>p.id);let cursor=0;
    while(cursor<queue.length){const id=queue[cursor++];for(const next of out.get(id).filter(x=>ids.has(x))){ranks.set(next,Math.max(ranks.get(next),ranks.get(id)+1));degrees.set(next,degrees.get(next)-1);if(!degrees.get(next))queue.push(next);}}
    const unresolved=peers.filter(p=>degrees.get(p.id)>0);
    if(unresolved.length){
      warnings.push('存在同年或年份待核的循环关系，已保留节点与原始关系，请核对方向。');
      let rank=Math.max(0,...ranks.values())+1;unresolved.forEach(p=>ranks.set(p.id,rank++));
    }
    for(let rank=0;rank<=Math.max(0,...ranks.values());rank++){
      const group=peers.filter(p=>ranks.get(p.id)===rank);if(!group.length)continue;
      const column=columns.length;columns.push({year,rank,label:(year||'年份待核')+(rank?' · 同年 '+(rank+1):'')});
      group.forEach(p=>columnById.set(p.id,column));
    }
  }
  nodes.sort((a,b)=>columnById.get(a.id)-columnById.get(b.id)||String(a.id).localeCompare(String(b.id)));
  const lanes=new Map(),occupied=new Set();let nextLane=0;
  function visit(id,preferred){
    if(lanes.has(id))return;
    const column=columnById.get(id);let lane=preferred;
    while(occupied.has(column+':'+lane))lane=Math.max(lane+1,nextLane);
    lanes.set(id,lane);occupied.add(column+':'+lane);nextLane=Math.max(nextLane,lane+1);
    const children=[...new Set(out.get(id))].filter(x=>columnById.get(x)>column).sort((a,b)=>columnById.get(a)-columnById.get(b)||a.localeCompare(b));
    children.forEach((child,i)=>visit(child,i?nextLane:lane));
  }
  nodes.filter(p=>!incoming.get(p.id).some(id=>columnById.get(id)<columnById.get(p.id))).forEach(p=>visit(p.id,nextLane));
  nodes.forEach(p=>visit(p.id,nextLane));
  const conflicts=edges.filter(e=>lineageRelationView(e,nodes).conflict);
  if(conflicts.length)warnings.push(`${conflicts.length} 条关系的前作年份晚于后作，已保留原始年份与箭头，请核对。`);
  const vertical=availableWidth<780,boxW=vertical?Math.min(340,Math.max(150,availableWidth-84)):260,boxH=140,gap=95,positions=new Map();
  nodes.forEach((p,i)=>positions.set(p.id,{column:columnById.get(p.id),lane:lanes.get(p.id),x:vertical?64:18+columnById.get(p.id)*(boxW+gap),y:vertical?54+i*204:62+lanes.get(p.id)*204}));
  const width=vertical?Math.max(availableWidth,boxW+82):Math.max(availableWidth,36+columns.length*(boxW+gap)-gap);
  const height=nodes.length?(vertical?28+nodes.length*204:62+(Math.max(...lanes.values())+1)*204-40):80;
  return {nodes,edges,columns,positions,out,incoming,vertical,boxW,boxH,gap,width,height,warnings};
}

function lineageEdgePath(layout,edge,index){
  const a=layout.positions.get(edge.dst),b=layout.positions.get(edge.src),w=layout.boxW,h=layout.boxH;
  if(layout.vertical){
    if(b.y-a.y===204&&a.lane===b.lane)return `M${a.x+w/2},${a.y+h} C${a.x+w/2},${a.y+h+20} ${b.x+w/2},${b.y-20} ${b.x+w/2},${b.y}`;
    const side=16+index%4*9,y1=a.y+h/2,y2=b.y+h/2;
    return `M${a.x},${y1} L${side+10},${y1} Q${side},${y1} ${side},${y1+(y2>y1?10:-10)} L${side},${y2+(y2>y1?-10:10)} Q${side},${y2} ${side+10},${y2} L${b.x},${y2}`;
  }
  const x1=a.x+w,y1=a.y+h/2,x2=b.x,y2=b.y+h/2;
  if(b.column>a.column){
    const blocked=layout.nodes.some(p=>{const pos=layout.positions.get(p.id);return pos.lane===a.lane&&pos.x>x1&&pos.x<x2;});
    if(a.lane===b.lane&&!blocked)return `M${x1},${y1} C${x1+40},${y1} ${x2-40},${y2} ${x2},${y2}`;
    const turnX=x1+30,targetRowClear=!layout.nodes.some(p=>{
      if(p.id===edge.src||p.id===edge.dst)return false;
      const pos=layout.positions.get(p.id);
      return pos.x<x2&&pos.x+w>turnX&&y2>pos.y-6&&y2<pos.y+h+6;
    });
    if(a.lane!==b.lane&&targetRowClear){
      // 目标行没有遮挡时，一次换行后水平直达，避免在目标卡片前再次转弯。
      const sign=y2>y1?1:-1;
      return `M${x1},${y1} L${turnX-8},${y1} Q${turnX},${y1} ${turnX},${y1+sign*8} L${turnX},${y2-sign*8} Q${turnX},${y2} ${turnX+8},${y2} L${x2},${y2}`;
    }
    // 长分支先离开源卡片，再沿行间通道前进，避免穿过主线上的中间论文。
    const gutter=a.y+h+24+index%3*5;
    return `M${x1},${y1} L${x1+22},${y1} Q${x1+30},${y1} ${x1+30},${y1+8} L${x1+30},${gutter-8} Q${x1+30},${gutter} ${x1+38},${gutter} L${x2-30},${gutter} L${x2-30},${y2} L${x2},${y2}`;
  }
  // 时间冲突和循环关系走卡片上方，方向保持数据中的前作→后作。
  const top=43-index%3*5;
  return `M${a.x},${y1} L${a.x-12},${y1} L${a.x-12},${top} L${b.x+w+15},${top} L${b.x+w+15},${y2} L${b.x+w},${y2}`;
}

let lineageDiagramSerial=0;

let lineageTooltipTimer;

let lineageTooltipTrigger=null;

function lineageDiagramHTML(chain,layout,prefix){
  const {nodes,edges,positions,boxW,boxH,width,height,vertical}=layout;
  const kinds=[...new Set(edges.map(e=>e.kind))],markers=new Map(kinds.map((kind,i)=>[kind,prefix+'-arrow-'+i]));
  const defs=kinds.map(kind=>`<marker id="${markers.get(kind)}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 L10 5 L0 10z" fill="${REL_COLORS[kind]||'#8a8f98'}"/></marker>`).join('');
  const paths=edges.map((edge,i)=>{const view=lineageRelationView(edge,nodes),path=lineageEdgePath(layout,edge,i);return `<g class="lineage-edge ${view.grade}" style="--relation-color:${view.color}" data-lineage-edge="${$kb.core.esc(edge.id)}" tabindex="0" role="button" aria-label="${$kb.core.esc(view.name+'：'+view.from+' → '+view.to+'。'+view.change+(view.conflict?'。年份方向待核':''))}"><path class="lineage-hit" d="${path}"/><path class="lineage-arrow" d="${path}" marker-end="url(#${markers.get(edge.kind)})" aria-hidden="true"/></g>`;}).join('');
  const axis=vertical?nodes.map(p=>{const pos=positions.get(p.id);return `<text x="${pos.x}" y="${pos.y-14}" class="lineage-year">${$kb.core.esc(layout.columns[pos.column].label)}</text>`;}).join(''):layout.columns.map((column,i)=>{const x=18+i*(boxW+layout.gap)+boxW/2;return `<line x1="${x}" y1="37" x2="${x}" y2="${height-10}" class="lineage-year-guide"/><text x="${x}" y="25" text-anchor="middle" class="lineage-year">${$kb.core.esc(column.label)}</text>`;}).join('');
  const cards=nodes.map(p=>{const pos=positions.get(p.id);return `<button class="lineage-paper" style="left:${pos.x}px;top:${pos.y}px;width:${boxW}px;height:${boxH}px" data-paper="${$kb.core.esc(p.id)}" aria-label="${$kb.core.esc((lineageYear(p)||'年份待核')+' · '+p.title)}"><span>${lineageYear(p)||'年份待核'} · ${$kb.core.esc(p.citekey||'')}${new Set(layout.out.get(p.id)).size>1?' · 分支':''}${new Set(layout.incoming.get(p.id)).size>1?' · 融合':''}</span><strong>${$kb.references.citationBadge(p)}${$kb.core.esc(p.title)}</strong></button>`;}).join('');
  return layout.warnings.map(w=>`<p class="lineage-warning" role="note">${$kb.core.esc(w)}</p>`).join('')+`<div class="lineage-diagram" style="width:${width}px;height:${height}px"><svg width="${width}" height="${height}" role="group" aria-label="${$kb.core.esc(chain.name||'研究链')}：${vertical?'按年份向下':'所有分支共用年份列'}，箭头可聚焦查看证据"><defs>${defs}</defs><g aria-hidden="true">${axis}</g>${paths}</svg>${cards}</div>`;
}

function hideLineageTooltip(){
  clearTimeout(lineageTooltipTimer);
  const tip=$kb.core.$('#lineage-tooltip');if(tip)tip.hidden=true;
  if(lineageTooltipTrigger){lineageTooltipTrigger.removeAttribute('aria-describedby');lineageTooltipTrigger.classList.remove('active');}
  $kb.core.$$('.lineage-paper.related').forEach(p=>p.classList.remove('related'));
  lineageTooltipTrigger=null;
}

function positionLineageTooltip(tip,point){
  const rect=tip.getBoundingClientRect(),margin=12;
  const x=point.x+16+rect.width>window.innerWidth?point.x-rect.width-16:point.x+16;
  const y=point.y+16+rect.height>window.innerHeight?point.y-rect.height-16:point.y+16;
  tip.style.left=Math.max(margin,Math.min(x,window.innerWidth-rect.width-margin))+'px';
  tip.style.top=Math.max(margin,Math.min(y,window.innerHeight-rect.height-margin))+'px';
}

function showLineageTooltip(trigger,html,event,related=[]){
  clearTimeout(lineageTooltipTimer);
  let tip=$kb.core.$('#lineage-tooltip');
  if(!tip){
    tip=document.createElement('div');tip.id='lineage-tooltip';tip.className='lineage-tooltip';tip.setAttribute('role','tooltip');tip.hidden=true;document.body.append(tip);
    tip.onpointerenter=()=>clearTimeout(lineageTooltipTimer);tip.onpointerleave=()=>scheduleLineageTooltipHide();
    window.addEventListener('scroll',event=>{
      if(tip.contains(event.target))return;
      // 浏览器会先聚焦箭头再滚动到可见区域，此时浮层应跟随焦点而非消失。
      if(lineageTooltipTrigger===document.activeElement&&!tip.hidden){
        const rect=lineageTooltipTrigger.getBoundingClientRect();
        positionLineageTooltip(tip,{x:Math.max(12,Math.min(innerWidth-24,rect.left+rect.width/2)),y:Math.max(12,Math.min(innerHeight-24,rect.top+rect.height/2))});
      }else hideLineageTooltip();
    },true);window.addEventListener('resize',hideLineageTooltip);
    document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!tip.hidden){hideLineageTooltip();e.stopPropagation();e.preventDefault();}},true);
  }
  if(lineageTooltipTrigger!==trigger||tip.hidden){
    hideLineageTooltip();lineageTooltipTrigger=trigger;trigger.setAttribute('aria-describedby',tip.id);trigger.classList.add('active');
    tip.innerHTML=html;tip.hidden=false;
    const diagram=trigger.closest('.lineage-diagram');diagram?.querySelectorAll('[data-paper]').forEach(p=>p.classList.toggle('related',related.includes(p.dataset.paper)));
  }
  const rect=trigger.getBoundingClientRect(),point=event&&Number.isFinite(event.clientX)?{x:event.clientX,y:event.clientY}:{x:Math.max(12,Math.min(window.innerWidth-24,rect.left+rect.width/2)),y:Math.max(12,Math.min(window.innerHeight-24,rect.top+rect.height/2))};
  positionLineageTooltip(tip,point);
}

function scheduleLineageTooltipHide(){
  clearTimeout(lineageTooltipTimer);
  lineageTooltipTimer=setTimeout(()=>{if(document.activeElement!==lineageTooltipTrigger)hideLineageTooltip();},150);
}

function lineageTooltipHTML(view){
  return `<strong style="color:${view.color}">${$kb.core.esc(view.name)}</strong><div class="lineage-tooltip-path">${$kb.core.esc(view.from)} → ${$kb.core.esc(view.to)}</div><p>${$kb.core.esc(view.change)}</p>${view.conditions?`<p><b>适用条件：</b>${$kb.core.esc(view.conditions)}</p>`:''}<small>${$kb.core.esc(view.evidence)}</small>${view.conflict?'<p class="lineage-tooltip-warning">前作年份晚于后作，关系方向待核。</p>':''}<small>点击箭头或按 Enter 查看原文证据</small>`;
}

function drawLineage(chain,host){
  if(lineageTooltipTrigger&&host.contains(lineageTooltipTrigger))hideLineageTooltip();
  const layout=layoutLineage(chain,host.clientWidth);host.innerHTML=lineageDiagramHTML(chain,layout,'lineage-'+(++lineageDiagramSerial));
  host.querySelectorAll('[data-lineage-edge]').forEach(el=>{
    const edge=chain.edges.find(e=>e.id===el.dataset.lineageEdge),view=lineageRelationView(edge,chain.nodes);
    const show=e=>showLineageTooltip(el,lineageTooltipHTML(view),e,[edge.src,edge.dst]);
    el.onpointerenter=show;el.onpointermove=show;el.onpointerleave=scheduleLineageTooltipHide;el.onfocus=()=>show();el.onblur=hideLineageTooltip;
    const open=()=>{hideLineageTooltip();openRelation(edge);};el.onclick=open;el.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();open();}};
  });
  host.querySelectorAll('[data-paper]').forEach(el=>{
    const paper=chain.nodes.find(p=>p.id===el.dataset.paper),show=e=>showLineageTooltip(el,`<strong>${$kb.core.esc(paper.title)}</strong><small>${lineageYear(paper)||'年份待核'} · ${$kb.core.esc(paper.citekey||'')}</small>`,e);
    el.onpointerenter=show;el.onpointerleave=scheduleLineageTooltipHide;el.onfocus=()=>show();el.onblur=hideLineageTooltip;
    el.onclick=()=>{hideLineageTooltip();$kb.detail.showPaper(el.dataset.paper);};
  });
}

function getTheme(p){const t=p.title||'';return p.theme||(/rrt|sampling|probabilistic/i.test(t)?'采样与最优规划':/current|flow|energy|海流/i.test(t)?'海流与能耗':/coverage|inspection|viewpoint|覆盖|巡检/i.test(t)?'覆盖与巡检':/reconfig|modular|snake|变构|构型/i.test(t)?'变构与协同':/perception|sonar|slam|sensor/i.test(t)?'感知与定位':/safe|collision|dynamic|kinematic|risk/i.test(t)?'安全与动力学约束':'其他研究');}

function drawGraph(){
  const svg=$kb.core.$('#graph-svg');if(!svg)return;
  if($kb.core.state.graphMode==='timeline'){drawTimeline();return;}
  if($kb.core.state.graphMode==='chains')return;
  drawNetwork();
}

const DIR_COLORS={'01':'#b8465e','02':'#0f6f8f','03':'#c08a2a','04':'#2f7d4f','05':'#7a52cc','06':'#c9438c','07':'#0f9b9b','08':'#3f5f9e','09':'#a1666f','00':'#8a8f98','90':'#9aa0a6'};

const REL_COLORS={cites:'#b9c3b8',inherits:'#2f7d4f',improves:'#0f6f8f',combines:'#7a52cc',transfers:'#c08a2a',compares:'#c9438c',similar:'#8a8f98'};

const NET={nodes:[],links:[],adj:new Map(),view:{x:0,y:0,k:1},alpha:0,raf:0,drag:null,pan:null,pinned:new Map(),hoverId:null,selId:null,labels:'auto',relation:'all',colorBy:'direction',sig:'',w:0,h:0,fitPending:false,moved:false,pointers:new Map(),pinch:0};

function netDir(p){return p.primary_direction||(p.directions||[])[0]||'90';}

function netRadius(deg){return Math.min(17,5.2+Math.sqrt(deg)*1.5);}

function netColor(n){
  if(NET.colorBy==='year'){const y=Number(n.ref.year)||2000,t=Math.max(0,Math.min(1,(y-2000)/26));return `hsl(${Math.round(206-168*t)} 42% ${Math.round(52-5*t)}%)`;}
  return DIR_COLORS[netDir(n.ref)]||'#7c8b9a';
}

function netLabelText(n){const t=n.ref.title||n.ref.citekey||'题名待补';return t.length>34?t.slice(0,33)+'…':t;}

function netModel(){
  const filt=NET.relation,edges=$kb.core.state.edges.filter(e=>filt==='all'||(filt==='cites'?e.kind==='cites':e.kind!=='cites'&&e.evidence_grade==='fulltext'));
  const deg=new Map();edges.forEach(e=>{deg.set(e.src,(deg.get(e.src)||0)+1);deg.set(e.dst,(deg.get(e.dst)||0)+1);});
  const prev=new Map(NET.nodes.map(n=>[n.id,n])),fresh=[];
  const nodes=$kb.core.state.nodes.map(p=>{
    const d=deg.get(p.id)||0,old=prev.get(p.id);
    if(old){old.ref=p;old.deg=d;old.r=netRadius(d);return old;}
    const n={id:p.id,ref:p,deg:d,r:netRadius(d),x:0,y:0,vx:0,vy:0,el:null};fresh.push(n);return n;
  });
  const byId=new Map(nodes.map(n=>[n.id,n])),links=[];
  edges.forEach(e=>{const a=byId.get(e.dst),b=byId.get(e.src);if(!a||!b||a===b)return;links.push({ref:e,a,b,kind:e.kind==='cites'?'cites':'semantic',dist:e.kind==='cites'?88:68});});
  const adj=new Map(nodes.map(n=>[n.id,new Set()]));links.forEach(l=>{adj.get(l.a.id)?.add(l.b.id);adj.get(l.b.id)?.add(l.a.id);});
  return {nodes,links,adj,fresh};
}

function netSeed(nodes,fresh){
  if(!fresh.length)return false;
  if(fresh.length===nodes.length){
    const ids=[...new Set(nodes.map(n=>netDir(n.ref)))],idx=new Map(ids.map((d,i)=>[d,i])),total=Math.max(1,ids.length),R=Math.max(120,36*Math.sqrt(nodes.length));
    fresh.forEach((n,i)=>{const gi=idx.get(netDir(n.ref))||0,a=gi/total*Math.PI*2+(i%9-4)*.05+Math.random()*.12,rr=R*(.8+Math.random()*.4);n.x=Math.cos(a)*rr*1.45;n.y=Math.sin(a)*rr*.9;});
  }else fresh.forEach(n=>{n.x=(Math.random()-.5)*220;n.y=(Math.random()-.5)*220;});
  return true;
}

function netSignature(m){let s=m.nodes.length+':'+m.links.length+':';for(const n of m.nodes)s+=n.id+'|';for(const l of m.links)s+=l.ref.id+'|';let h=5381;for(let i=0;i<s.length;i++)h=(Math.imul(h,33)^s.charCodeAt(i))>>>0;return h.toString(36)+'.'+s.length;}

function netBuild(svg,canvas){
  const w=canvas.clientWidth||880,h=canvas.clientHeight||560;
  NET.w=w;NET.h=h;svg.setAttribute('viewBox',`0 0 ${w} ${h}`);svg.setAttribute('width',w);svg.setAttribute('height',h);
  if(!svg.querySelector('#net-viewport')){
    svg.innerHTML='<defs><marker id="net-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10z" fill="#8b9a8c"/></marker></defs><g id="net-viewport"><g id="net-links"></g><g id="net-nodes"></g></g>';
    svg.dataset.bound='1';
  }
  const gl=svg.querySelector('#net-links'),gn=svg.querySelector('#net-nodes');
  gl.innerHTML=NET.links.map(l=>`<path class="gedge ${l.kind}${l.ref.status==='candidate'?' candidate':''}" data-edge="${$kb.core.esc(l.ref.id)}" style="stroke-dasharray:${l.ref.evidence_grade==='title'?'2 5':l.ref.evidence_grade==='abstract'?'8 5':'none'}" stroke="${REL_COLORS[l.ref.kind]||'#b9c3b8'}"></path>`).join('');
  gn.innerHTML=NET.nodes.map((n,i)=>{const c=netColor(n),metric=$kb.references.citationView(n.ref),lib=n.ref.kind==='library',right=i%2===0;return `<g class="gnode${lib?'':' ext'}" data-node="${$kb.core.esc(n.id)}" tabindex="0" role="button" aria-label="${$kb.core.esc((n.ref.title||n.ref.citekey||'题名待补')+' · '+(n.ref.year||'年份待核')+' · '+($kb.core.dirs[netDir(n.ref)]||'未归类')+' · '+n.deg+' 条关系 · '+metric.tip)}"><circle class="halo" r="${(n.r+5).toFixed(1)}" fill="none" stroke="${c}"/><circle class="dot" r="${n.r.toFixed(1)}" fill="${lib?c:'#fbfaf4'}" stroke="${c}"/><circle class="citation-node-dot" cx="${(right?n.r+9:-n.r-9).toFixed(1)}" r="3.5" fill="${metric.level==='unknown'?'none':metric.color}" stroke="${metric.color}"><title>${$kb.core.esc(metric.tip)}</title></circle><text x="${(right?n.r+17:-n.r-17).toFixed(1)}" text-anchor="${right?'start':'end'}" dy=".32em">${$kb.core.esc(netLabelText(n))}</text></g>`;}).join('');
  NET.nodes.forEach((n,i)=>n.el=gn.children[i]);NET.links.forEach((l,i)=>l.el=gl.children[i]);
  netBind(svg,canvas);netLegend();
}

function netPinchDist(){const p=[...NET.pointers.values()];return p.length<2?0:Math.hypot(p[0].x-p[1].x,p[0].y-p[1].y);}

function netBind(svg,canvas){
  if(svg.dataset.bound==='1')return;svg.dataset.bound='1';
  const at=e=>{const r=svg.getBoundingClientRect();return {x:e.clientX-r.left,y:e.clientY-r.top};};
  const world=p=>({x:(p.x-NET.view.x)/NET.view.k,y:(p.y-NET.view.y)/NET.view.k});
  const find=t=>{const el=t.closest?.('.gnode');return el?NET.nodes.find(n=>n.el===el):null;};
  svg.addEventListener('wheel',e=>{
    if($kb.core.state.graphMode!=='network')return;e.preventDefault();
    const p=at(e),w=world(p),k=Math.max(.18,Math.min(2.8,NET.view.k*Math.exp(-e.deltaY*.0016)));
    NET.view.k=k;NET.view.x=p.x-w.x*k;NET.view.y=p.y-w.y*k;netApplyView();
  },{passive:false});
  svg.addEventListener('pointerdown',e=>{
    if($kb.core.state.graphMode!=='network')return;
    NET.pointers.set(e.pointerId,{x:e.clientX,y:e.clientY});
    if(NET.pointers.size===2){NET.drag=null;NET.pan=null;NET.pinch=netPinchDist();return;}
    if(NET.pointers.size>2)return;
    const n=find(e.target);
    svg.setPointerCapture?.(e.pointerId);
    if(n){NET.drag={node:n,moved:false};NET.moved=false;NET.pinned.delete(n.id);NET.alpha=Math.max(NET.alpha,.35);netSimStart();}
    else{NET.pan={x:NET.view.x,y:NET.view.y,start:at(e)};canvas.classList.add('dragging');}
  });
  svg.addEventListener('pointermove',e=>{
    if($kb.core.state.graphMode!=='network')return;
    if(NET.pointers.has(e.pointerId))NET.pointers.set(e.pointerId,{x:e.clientX,y:e.clientY});
    if(NET.pointers.size===2&&NET.pinch){
      const d=netPinchDist(),p=at(e);
      if(d>0){const k=Math.max(.18,Math.min(2.8,NET.view.k*d/NET.pinch)),w=world(p);NET.view.k=k;NET.view.x=p.x-w.x*k;NET.view.y=p.y-w.y*k;netApplyView();}
      NET.pinch=d;return;
    }
    const p=at(e);
    if(NET.drag){const w=world(p);NET.drag.node.x=w.x;NET.drag.node.y=w.y;NET.drag.node.vx=0;NET.drag.node.vy=0;NET.drag.moved=true;NET.moved=true;netPaint();return;}
    if(NET.pan){NET.view.x=NET.pan.x+(p.x-NET.pan.start.x);NET.view.y=NET.pan.y+(p.y-NET.pan.start.y);netApplyView();return;}
    const n=find(e.target);
    if((n?n.id:null)!==NET.hoverId){NET.hoverId=n?n.id:null;netHighlight(NET.hoverId||NET.selId);}
    netTip(n,e,canvas);
  });
  const end=e=>{
    NET.pointers.delete(e.pointerId);
    if(NET.pointers.size<2)NET.pinch=0;
    if(NET.drag){if(NET.drag.moved)NET.pinned.set(NET.drag.node.id,{x:NET.drag.node.x,y:NET.drag.node.y});NET.drag=null;}
    NET.pan=null;canvas.classList.remove('dragging');
  };
  svg.addEventListener('pointerup',end);svg.addEventListener('pointercancel',end);
  svg.addEventListener('pointerleave',()=>{if(!NET.drag){NET.hoverId=null;netHighlight(NET.selId);netTip(null);}});
  svg.addEventListener('click',e=>{
    if($kb.core.state.graphMode!=='network')return;
    if(NET.moved){NET.moved=false;return;}
    const n=find(e.target);if(n)$kb.detail.showPaper(n.id);
  });
  svg.addEventListener('dblclick',e=>{if(!find(e.target))netFit();});
  svg.addEventListener('keydown',e=>{
    const el=e.target.closest?.('.gnode');if(!el)return;
    if(e.key==='Enter'||e.key===' '){e.preventDefault();$kb.detail.showPaper(el.dataset.node);}
  });
}

function netTip(n,e,canvas){
  const tip=document.getElementById('net-tip');if(!tip)return;
  if(!n){tip.hidden=true;tip.classList.remove('on');return;}
  const r=canvas.getBoundingClientRect();
  tip.hidden=false;tip.classList.add('on');
  tip.innerHTML=`<strong>${$kb.core.esc(n.ref.title||n.ref.citekey||'题名待补')}</strong><span>${$kb.core.esc(n.ref.year||'年份待核')} · ${$kb.core.esc($kb.core.dirs[netDir(n.ref)]||'未归类')} · ${n.deg} 条关系</span><span>${$kb.core.esc($kb.references.citationView(n.ref).tip)}</span>`;
  const x=e.clientX-r.left,y=e.clientY-r.top;
  tip.style.left=Math.min(Math.max(8,r.width-248),Math.max(8,x+14))+'px';
  tip.style.top=Math.min(Math.max(8,r.height-72),Math.max(8,y+16))+'px';
}

function netLegend(){
  const host=$kb.core.$('#graph-legend');if(!host)return;
  if(NET.colorBy==='year'){host.innerHTML='<span><i style="background:#2b6c9b"></i>较早</span><span><i style="background:#c98a1e"></i>较新</span><span><i class="ring"></i>库外</span>';return;}
  const counts=new Map();NET.nodes.forEach(n=>{const d=netDir(n.ref);counts.set(d,(counts.get(d)||0)+1);});
  const top=[...counts.entries()].sort((a,b)=>b[1]-a[1]).slice(0,4);
  host.innerHTML=top.map(([d,c])=>`<span><i style="background:${DIR_COLORS[d]||'#7c8b9a'}"></i>${$kb.core.esc($kb.core.dirs[d]||'未归类')} <b>${c}</b></span>`).join('')+'<span><i class="ring"></i>库外</span>';
}

function netApplyView(){
  const v=NET.view,g=document.getElementById('net-viewport');
  if(g)g.setAttribute('transform',`translate(${v.x.toFixed(2)} ${v.y.toFixed(2)}) scale(${v.k.toFixed(4)})`);
  const l=document.getElementById('net-zoom');if(l)l.textContent=Math.round(v.k*100)+'%';
}

function netFit(){
  const canvas=$kb.core.$('#graph-canvas');if(!canvas||!NET.nodes.length)return;
  const w=canvas.clientWidth,h=canvas.clientHeight,pad=34;
  let x0=Infinity,y0=Infinity,x1=-Infinity,y1=-Infinity;
  NET.nodes.forEach(n=>{x0=Math.min(x0,n.x-n.r);y0=Math.min(y0,n.y-n.r);x1=Math.max(x1,n.x+n.r);y1=Math.max(y1,n.y+n.r);});
  if(!isFinite(x0))return;
  const bw=Math.max(1,x1-x0),bh=Math.max(1,y1-y0),k=Math.max(.2,Math.min(1.9,Math.min((w-pad*2)/bw,(h-pad*2)/bh)));
  NET.view.k=k;NET.view.x=w/2-(x0+x1)/2*k;NET.view.y=h/2-(y0+y1)/2*k;netApplyView();
}

function netZoom(f){
  const canvas=$kb.core.$('#graph-canvas');if(!canvas)return;
  const w=canvas.clientWidth/2,h=canvas.clientHeight/2,cx=(w-NET.view.x)/NET.view.k,cy=(h-NET.view.y)/NET.view.k;
  const k=Math.max(.18,Math.min(2.8,NET.view.k*f));
  NET.view.k=k;NET.view.x=w-cx*k;NET.view.y=h-cy*k;netApplyView();
}

function netApplyLabels(){
  const mode=NET.labels;let big=new Set();
  if(mode==='auto'){const arr=NET.nodes.slice().sort((a,b)=>b.deg-a.deg).slice(0,Math.min(10,Math.max(6,Math.round(NET.nodes.length*.035))));big=new Set(arr.map(n=>n.id));}
  NET.nodes.forEach(n=>{if(!n.el)return;const on=mode==='all'||(mode==='auto'&&(big.has(n.id)||n.r>=13.5));n.el.classList.toggle('label',on);});
}

function netHighlight(id){
  const has=id!=null,near=has?(NET.adj.get(id)||new Set()):null;
  NET.nodes.forEach(n=>{if(!n.el)return;const hot=has&&(n.id===id||near.has(n.id));n.el.classList.toggle('hot',hot);n.el.classList.toggle('dim',has&&!hot);n.el.classList.toggle('sel',NET.selId===n.id);});
  NET.links.forEach(l=>{if(!l.el)return;const hot=has&&(l.a.id===id||l.b.id===id);l.el.classList.toggle('hot',hot);l.el.classList.toggle('dim',has&&!hot);});
}

function netSelect(id){NET.selId=id;netHighlight(id||NET.hoverId);}

function netPaint(){
  for(const l of NET.links){const a=l.a,b=l.b,el=l.el;if(!el)continue;const dx=b.x-a.x,dy=b.y-a.y,d=Math.hypot(dx,dy)||1,ux=dx/d,uy=dy/d;el.setAttribute('d',`M${(a.x+ux*a.r).toFixed(1)} ${(a.y+uy*a.r).toFixed(1)} L${(b.x-ux*(b.r+2)).toFixed(1)} ${(b.y-uy*(b.r+2)).toFixed(1)}`);}
  for(const n of NET.nodes){if(n.el)n.el.setAttribute('transform',`translate(${n.x.toFixed(1)} ${n.y.toFixed(1)})`);}
}

function netStep(){
  const ns=NET.nodes,alpha=NET.alpha,rep=3200;
  for(let i=0;i<ns.length;i++){const a=ns[i];for(let j=i+1;j<ns.length;j++){const b=ns[j];let dx=b.x-a.x,dy=b.y-a.y,d2=dx*dx+dy*dy;if(d2>160000)continue;if(d2<1)d2=1;const d=Math.sqrt(d2),f=rep*alpha/d2,fx=dx/d*f*1.32,fy=dy/d*f;a.vx-=fx;a.vy-=fy;b.vx+=fx;b.vy+=fy;}}
  for(const l of NET.links){const a=l.a,b=l.b;let dx=b.x-a.x,dy=b.y-a.y;const d=Math.hypot(dx,dy)||.01;const f=(d-l.dist)/d*.55*alpha*(l.kind==='cites'?1:1.9),fx=dx*f,fy=dy*f;a.vx+=fx;a.vy+=fy;b.vx-=fx;b.vy-=fy;}
  const g=.012*alpha;
  for(const a of ns){
    if(NET.drag&&NET.drag.node===a){a.vx=0;a.vy=0;continue;}
    if(NET.pinned.has(a.id)){const p=NET.pinned.get(a.id);a.x=p.x;a.y=p.y;a.vx=0;a.vy=0;continue;}
    a.vx-=a.x*g*.72;a.vy-=a.y*g*1.75;
    a.vx=Math.max(-45,Math.min(45,a.vx));a.vy=Math.max(-45,Math.min(45,a.vy));
    a.x+=a.vx*.6;a.y+=a.vy*.6;a.vx*=.44;a.vy*=.44;
  }
  NET.alpha=alpha*.972;
  if(NET.alpha<.004)NET.alpha=0;
}

function netWarm(steps){NET.alpha=1;for(let i=0;i<steps;i++)netStep();NET.alpha=0;}

function netSimStart(){if(!NET.raf)NET.raf=requestAnimationFrame(netFrame);}

function netFrame(){
  NET.raf=0;
  if($kb.core.state.page!=='graph'||$kb.core.state.graphMode!=='network')return;
  if(NET.alpha<=0&&!NET.drag)return;
  netStep();netPaint();
  if(NET.fitPending&&NET.alpha<.55){NET.fitPending=false;netFit();}
  if(NET.alpha>0||NET.drag)NET.raf=requestAnimationFrame(netFrame);
}

function netRelayout(){
  NET.pinned.clear();
  NET.nodes.forEach(n=>{n.vx=0;n.vy=0;n.x=0;n.y=0;});
  netSeed(NET.nodes,NET.nodes.slice());
  NET.alpha=1;NET.fitPending=true;netSimStart();
}

function netCenterOn(pt){NET.view.x=NET.w/2-pt.x*NET.view.k;NET.view.y=NET.h/2-pt.y*NET.view.k;netApplyView();}

function netResize(){
  const canvas=$kb.core.$('#graph-canvas'),svg=$kb.core.$('#graph-svg');if(!canvas||!svg||!NET.nodes.length)return;
  const center=(NET.w&&NET.h)?{x:(NET.w/2-NET.view.x)/NET.view.k,y:(NET.h/2-NET.view.y)/NET.view.k}:null;
  netSizeCanvas();
  const w=canvas.clientWidth,h=canvas.clientHeight;if(!w||!h)return;
  svg.setAttribute('viewBox',`0 0 ${w} ${h}`);svg.setAttribute('width',w);svg.setAttribute('height',h);
  NET.w=w;NET.h=h;
  if(center)netCenterOn(center);else netFit();
}

function netSizeCanvas(){
  const canvas=$kb.core.$('#graph-canvas');if(!canvas)return;
  const rect=canvas.getBoundingClientRect(),avail=window.innerHeight-rect.top-64;
  if(avail>280)canvas.style.height=Math.min(940,Math.round(avail))+'px';
}

function drawNetwork(opts={}){
  const canvas=$kb.core.$('#graph-canvas'),svg=$kb.core.$('#graph-svg');if(!canvas||!svg)return;
  canvas.classList.add('net-mode');netSizeCanvas();
  if(!$kb.core.state.nodes.length){svg.innerHTML='<text x="28" y="52" fill="#7c896e" font-size="13">没有符合当前范围的文献，可清除筛选后重试。</text>';return;}
  const model=netModel(),sig=netSignature(model),changed=sig!==NET.sig;
  const w0=NET.w,h0=NET.h,keep=Boolean(opts.keepView);
  const center=(w0&&h0)?{x:(w0/2-NET.view.x)/NET.view.k,y:(h0/2-NET.view.y)/NET.view.k}:null;
  NET.nodes=model.nodes;NET.links=model.links;NET.adj=model.adj;
  const seeded=netSeed(model.nodes,model.fresh);
  netBuild(svg,canvas);
  const resized=Boolean(w0&&(w0!==NET.w||h0!==NET.h));
  NET.sig=sig;
  if(seeded||changed)NET.alpha=1;
  if(NET.selId&&!NET.nodes.some(n=>n.id===NET.selId))NET.selId=null;
  if(!keep&&(changed||seeded)){netWarm(180);netFit();}
  else if(center)netCenterOn(center);
  else netFit();
  netApplyLabels();netHighlight(NET.selId);
  netPaint();netApplyView();netSimStart();
}

function drawTimeline(){
  const svg=$kb.core.$('#graph-svg');if(!svg||!$kb.core.state.timeline)return;
  const tlCanvas=$kb.core.$('#graph-canvas');if(tlCanvas){tlCanvas.classList.remove('net-mode');tlCanvas.style.height='';}
  const colors={'01':'#c2455a','02':'#0f6f8f','03':'#c98a1e','04':'#2f7d4f','05':'#7a52cc','06':'#c9438c','07':'#0f9b9b','08':'#3f5f9e','09':'#a1666f','00':'#8a8f98','90':'#9aa0a6'};
  $kb.core.state.expandedDirections??=new Set();const baseWidth=Math.max($kb.core.$('.graph-scroll').clientWidth,640);let width=baseWidth,y=8,body='';
  for(const g of $kb.core.state.timeline.groups){
    const color=colors[g.direction]||'#0f6f8f';body+=`<circle cx="22" cy="${y+15}" r="5" fill="${color}"/><text x="36" y="${y+20}" class="timeline-heading">${$kb.core.esc(g.name)}<tspan class="timeline-count">　${g.papers} 篇 · 已核 ${g.confirmed} · 待核 ${g.candidates}</tspan></text>`;y+=42;
    const expanded=$kb.core.state.expandedDirections.has(g.direction),paths=expanded?g.paths:g.paths.slice(0,3);
    for(const c of paths){const step=$kb.core.state.timelineFit?Math.max(48,Math.min(148,(baseWidth-72)/Math.max(1,c.nodes.length-1))):Math.max(80,180*$kb.core.state.zoom),rowWidth=72+(c.nodes.length-1)*step;width=Math.max(width,rowWidth);const x0=36+Math.max(0,(baseWidth-72-(c.nodes.length-1)*step)/2),positions=new Map(c.nodes.map((p,i)=>[p.id,{x:x0+i*step,y:y+38}]));
      body+=`<text x="18" y="${y+7}" class="timeline-row-label ${c.kind==='method'?'verified':''}">${c.kind==='method'?'已核方法链':'引用路径 · 方法关系待核'}</text>`;
      for(const e of c.edges){const a=positions.get(e.dst),b=positions.get(e.src);if(!a||!b)continue;body+=`<path class="timeline-link ${e.kind==='cites'?'citation':''}" data-timeline-edge="${$kb.core.esc(e.id)}" tabindex="0" role="button" aria-label="${$kb.core.esc($kb.core.relationNames[e.kind])}：查看证据" d="M${a.x},${a.y} L${b.x},${b.y}" stroke="${color}" marker-end="url(#timeline-arrow)"><title>${$kb.core.esc($kb.core.relationNames[e.kind])} · ${$kb.core.esc(e.explanation||'引用事实，方法关系待核')}</title></path>`;}
      for(const p of c.nodes){const pos=positions.get(p.id),w=Math.min(step-12,160);body+=`<g class="timeline-node" data-node="${$kb.core.esc(p.id)}" role="button" tabindex="0" aria-label="${$kb.core.esc(p.title)}"><title>${$kb.core.esc(p.title)}</title><circle cx="${pos.x}" cy="${pos.y}" r="${p.kind==='library'?5.5:4.5}" fill="${p.kind==='library'?color:'#fff'}" stroke="${color}"/><text class="timeline-year" x="${pos.x}" y="${pos.y-12}" text-anchor="middle">${p.year||'—'}</text><foreignObject x="${pos.x-w/2}" y="${pos.y+12}" width="${w}" height="59"><div xmlns="http://www.w3.org/1999/xhtml" class="timeline-paper-label">${$kb.references.citationBadge(p)}${$kb.core.esc(p.title)}</div></foreignObject></g>`;}
      y+=126;
    }
    if(!paths.length){body+=`<text class="timeline-empty" x="22" y="${y+15}">当前筛选暂无已核链；文献仍可检索，方法关系正在核实。</text>`;y+=42;}
    if(g.paths.length>3){body+=`<text class="timeline-more" role="button" tabindex="0" data-more-direction="${g.direction}" x="22" y="${y+8}">${expanded?'收起路径':'展开其余 '+(g.paths.length-3)+' 条路径'} ↓</text>`;y+=30;}
    body+=`<line x1="16" y1="${y}" x2="${width-15}" y2="${y}" stroke="#e6e2da"/>`;y+=23;
  }
  svg.setAttribute('width',width);svg.setAttribute('height',y);svg.innerHTML=`<defs><marker id="timeline-arrow" viewBox="0 0 10 10" refX="16" refY="5" markerWidth="5" markerHeight="5" orient="auto"><path d="M0 0 L10 5 L0 10z" fill="#778896"/></marker></defs>${body}`;
  svg.querySelectorAll('[data-node]').forEach(b=>{b.onclick=()=>$kb.detail.showPaper(b.dataset.node);b.onkeydown=e=>{if(e.key==='Enter')b.onclick();};});
  svg.querySelectorAll('[data-more-direction]').forEach(b=>{b.onclick=()=>{const d=b.dataset.moreDirection;if($kb.core.state.expandedDirections.has(d))$kb.core.state.expandedDirections.delete(d);else $kb.core.state.expandedDirections.add(d);drawTimeline();};b.onkeydown=e=>{if(e.key==='Enter')b.onclick();};});
  svg.querySelectorAll('[data-timeline-edge]').forEach(b=>{const open=async()=>{try{openRelation(await $kb.core.api('/api/relation?rid='+encodeURIComponent(b.dataset.timelineEdge)));}catch(e){$kb.feedback.showError(e.message);}};b.onclick=open;b.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();open();}};});
}

function patchRelationEverywhere(r){
  const put=arr=>{if(!Array.isArray(arr))return;const i=arr.findIndex(x=>x&&x.id===r.id);if(i>=0)arr[i]={...arr[i],...r};};
  put($kb.core.state.edges);
  ($kb.core.state.chainGroups||[]).forEach(c=>put(c.edges));
  (($kb.core.state.timeline&&$kb.core.state.timeline.groups)||[]).forEach(g=>(g.paths||[]).forEach(path=>put(path.edges)));
}

async function refreshGraphEdges(){
  if($kb.core.state.page!=='graph'||$kb.core.state.graphMode!=='network'||!$kb.core.state.nodes.length)return;
  try{
    const rel=await $kb.core.api('/api/graph/edges',{ids:$kb.core.state.nodes.map(n=>n.id),candidates:$kb.core.state.graphQuery.candidates});
    if($kb.core.state.page!=='graph'||$kb.core.state.graphMode!=='network')return;
    $kb.core.state.edges=rel.edges;
    drawNetwork({keepView:true});
  }catch(e){$kb.feedback.showError(e.message,{title:'关系更新未同步'});}
}

async function afterRelationSaved(r){
  if(r.status==='rejected')$kb.core.state.edges=$kb.core.state.edges.filter(e=>e.id!==r.id);
  else patchRelationEverywhere(r);
  if($kb.core.state.page==='graph'&&$kb.core.state.graphMode==='network')await refreshGraphEdges();
  else if($kb.core.state.page==='graph')await reloadGraphKeepingPosition();
  if($kb.core.state.detail)await $kb.detail.showPaper($kb.core.state.detail,{keepScroll:true});
  await $kb.status.refreshStatus();
}

function busyUser(){
  if(document.querySelector('dialog[open]'))return true;
  if(NET.drag||NET.pan)return true;
  const a=document.activeElement;
  return Boolean(a&&(a.tagName==='INPUT'||a.tagName==='TEXTAREA'||a.tagName==='SELECT'||a.isContentEditable));
}

function showStaleBanner(text){
  const b=$kb.core.$('#stale-banner');if(!b)return;
  const t=$kb.core.$('#stale-text');if(t)t.textContent=text||'核验进度已更新，可刷新当前视图查看新结果。';
  b.hidden=false;
}

function hideStaleBanner(){const b=$kb.core.$('#stale-banner');if(b)b.hidden=true;}

async function reloadGraphKeepingPosition(){
  $kb.core.clearReadCache();
  const y=window.scrollY,focus=document.activeElement?.dataset?.node||'';
  const canvases=$kb.core.$$('.lineage-canvas').map(el=>({id:el.id,x:el.scrollLeft,y:el.scrollTop}));
  await loadGraph();
  canvases.forEach(saved=>{const el=document.getElementById(saved.id);if(el){el.scrollLeft=saved.x;el.scrollTop=saved.y;}});
  window.scrollTo(0,y);
  if(focus){const el=document.querySelector('.gnode[data-node="'+focus.replace(/"/g,'')+'"]');if(el&&el.focus)el.focus();}
}

async function reloadChainsKeepingPosition(){
  $kb.core.clearReadCache();
  const y=window.scrollY;
  await $kb.topics.chainsPage();
  if($kb.core.state.page==='chains')window.scrollTo(0,y);
}
return {get graphTimer(){return graphTimer;},set graphTimer(value){graphTimer=value;},
get graphPage(){return graphPage;},set graphPage(value){graphPage=value;},
get renderOverview(){return renderOverview;},set renderOverview(value){renderOverview=value;},
get graphGeneration(){return graphGeneration;},set graphGeneration(value){graphGeneration=value;},
get loadGraph(){return loadGraph;},set loadGraph(value){loadGraph=value;},
get shortPaper(){return shortPaper;},set shortPaper(value){shortPaper=value;},
get renderMethodTimeline(){return renderMethodTimeline;},set renderMethodTimeline(value){renderMethodTimeline=value;},
get runNames(){return runNames;},
get runHTML(){return runHTML;},set runHTML(value){runHTML=value;},
get pollRun(){return pollRun;},set pollRun(value){pollRun=value;},
get bindRunControls(){return bindRunControls;},set bindRunControls(value){bindRunControls=value;},
get answerSerial(){return answerSerial;},set answerSerial(value){answerSerial=value;},
get showAnswerDialog(){return showAnswerDialog;},set showAnswerDialog(value){showAnswerDialog=value;},
get refreshResearchTasks(){return refreshResearchTasks;},set refreshResearchTasks(value){refreshResearchTasks=value;},
get researchPage(){return researchPage;},set researchPage(value){researchPage=value;},
get scopedGraphNodes(){return scopedGraphNodes;},set scopedGraphNodes(value){scopedGraphNodes=value;},
get renderLineage(){return renderLineage;},set renderLineage(value){renderLineage=value;},
get lineageYear(){return lineageYear;},set lineageYear(value){lineageYear=value;},
get lineageRelationView(){return lineageRelationView;},set lineageRelationView(value){lineageRelationView=value;},
get lineageLegendHTML(){return lineageLegendHTML;},set lineageLegendHTML(value){lineageLegendHTML=value;},
get layoutLineage(){return layoutLineage;},set layoutLineage(value){layoutLineage=value;},
get lineageEdgePath(){return lineageEdgePath;},set lineageEdgePath(value){lineageEdgePath=value;},
get lineageDiagramSerial(){return lineageDiagramSerial;},set lineageDiagramSerial(value){lineageDiagramSerial=value;},
get lineageTooltipTimer(){return lineageTooltipTimer;},set lineageTooltipTimer(value){lineageTooltipTimer=value;},
get lineageTooltipTrigger(){return lineageTooltipTrigger;},set lineageTooltipTrigger(value){lineageTooltipTrigger=value;},
get lineageDiagramHTML(){return lineageDiagramHTML;},set lineageDiagramHTML(value){lineageDiagramHTML=value;},
get hideLineageTooltip(){return hideLineageTooltip;},set hideLineageTooltip(value){hideLineageTooltip=value;},
get positionLineageTooltip(){return positionLineageTooltip;},set positionLineageTooltip(value){positionLineageTooltip=value;},
get showLineageTooltip(){return showLineageTooltip;},set showLineageTooltip(value){showLineageTooltip=value;},
get scheduleLineageTooltipHide(){return scheduleLineageTooltipHide;},set scheduleLineageTooltipHide(value){scheduleLineageTooltipHide=value;},
get lineageTooltipHTML(){return lineageTooltipHTML;},set lineageTooltipHTML(value){lineageTooltipHTML=value;},
get drawLineage(){return drawLineage;},set drawLineage(value){drawLineage=value;},
get getTheme(){return getTheme;},set getTheme(value){getTheme=value;},
get drawGraph(){return drawGraph;},set drawGraph(value){drawGraph=value;},
get DIR_COLORS(){return DIR_COLORS;},
get REL_COLORS(){return REL_COLORS;},
get NET(){return NET;},
get netDir(){return netDir;},set netDir(value){netDir=value;},
get netRadius(){return netRadius;},set netRadius(value){netRadius=value;},
get netColor(){return netColor;},set netColor(value){netColor=value;},
get netLabelText(){return netLabelText;},set netLabelText(value){netLabelText=value;},
get netModel(){return netModel;},set netModel(value){netModel=value;},
get netSeed(){return netSeed;},set netSeed(value){netSeed=value;},
get netSignature(){return netSignature;},set netSignature(value){netSignature=value;},
get netBuild(){return netBuild;},set netBuild(value){netBuild=value;},
get netPinchDist(){return netPinchDist;},set netPinchDist(value){netPinchDist=value;},
get netBind(){return netBind;},set netBind(value){netBind=value;},
get netTip(){return netTip;},set netTip(value){netTip=value;},
get netLegend(){return netLegend;},set netLegend(value){netLegend=value;},
get netApplyView(){return netApplyView;},set netApplyView(value){netApplyView=value;},
get netFit(){return netFit;},set netFit(value){netFit=value;},
get netZoom(){return netZoom;},set netZoom(value){netZoom=value;},
get netApplyLabels(){return netApplyLabels;},set netApplyLabels(value){netApplyLabels=value;},
get netHighlight(){return netHighlight;},set netHighlight(value){netHighlight=value;},
get netSelect(){return netSelect;},set netSelect(value){netSelect=value;},
get netPaint(){return netPaint;},set netPaint(value){netPaint=value;},
get netStep(){return netStep;},set netStep(value){netStep=value;},
get netWarm(){return netWarm;},set netWarm(value){netWarm=value;},
get netSimStart(){return netSimStart;},set netSimStart(value){netSimStart=value;},
get netFrame(){return netFrame;},set netFrame(value){netFrame=value;},
get netRelayout(){return netRelayout;},set netRelayout(value){netRelayout=value;},
get netCenterOn(){return netCenterOn;},set netCenterOn(value){netCenterOn=value;},
get netResize(){return netResize;},set netResize(value){netResize=value;},
get netSizeCanvas(){return netSizeCanvas;},set netSizeCanvas(value){netSizeCanvas=value;},
get drawNetwork(){return drawNetwork;},set drawNetwork(value){drawNetwork=value;},
get drawTimeline(){return drawTimeline;},set drawTimeline(value){drawTimeline=value;},
get patchRelationEverywhere(){return patchRelationEverywhere;},set patchRelationEverywhere(value){patchRelationEverywhere=value;},
get refreshGraphEdges(){return refreshGraphEdges;},set refreshGraphEdges(value){refreshGraphEdges=value;},
get afterRelationSaved(){return afterRelationSaved;},set afterRelationSaved(value){afterRelationSaved=value;},
get busyUser(){return busyUser;},set busyUser(value){busyUser=value;},
get showStaleBanner(){return showStaleBanner;},set showStaleBanner(value){showStaleBanner=value;},
get hideStaleBanner(){return hideStaleBanner;},set hideStaleBanner(value){hideStaleBanner=value;},
get reloadGraphKeepingPosition(){return reloadGraphKeepingPosition;},set reloadGraphKeepingPosition(value){reloadGraphKeepingPosition=value;},
get reloadChainsKeepingPosition(){return reloadChainsKeepingPosition;},set reloadChainsKeepingPosition(value){reloadChainsKeepingPosition=value;}};
});
