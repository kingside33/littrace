/* core：通过 ctx 访问其他模块；变量保留在本模块闭包内。 */
KB.define('core',($kb)=>{'use strict';
const $=s=>document.querySelector(s);

const $$=s=>[...document.querySelectorAll(s)];

const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

const safe=x=>/^(https?:|zotero:|obsidian:|\/)/i.test(x||'')?esc(x):'#';

const titleNames={research:'研究调度台',graph:'研究演化图谱',search:'证据检索与问答',chains:'专题总结与研究缺口',approvals:'全文审批',status:'索引与更新',settings:'模型与费用设置'};

const relationNames={parallel:'并行研究',contradicts:'结论冲突',cites:'引用',inherits:'方法继承',improves:'具体改进',combines:'方法融合',transfers:'应用迁移',compares:'对比',similar:'相似'};

const sourceNames={paper_text:'论文原文',metadata:'元数据与摘要',figure:'图表与图内文字',formula:'公式与上下文',zotero_note:'Zotero 笔记',zotero_annotation:'Zotero 批注',obsidian_note:'Obsidian 笔记',ai_analysis:'AI 分析',external_abstract:'库外元数据与摘要'};

const dirs={'01':'研究方向一','02':'研究方向二','03':'研究方向三','04':'研究方向四','05':'研究方向五','06':'方法与理论','07':'仿真与实验','08':'数据与感知','09':'其他与交叉','00':'背景与综述','90':'待分类'};

const state={page:'graph',token:'',status:{},nodes:[],edges:[],graphMode:'chains',timelineFit:true,graphQuery:{project:false,direction:'',q:'',candidates:true},detail:null,zoom:1};

let toastTimer;

function toast(message){$('#toast').textContent=message;$('#toast').style.display='block';clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').style.display='none',6500);}

function fmtDate(x){return x?new Date(x).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}):'尚未发布';}

function loading(){return '<div class="loading"><i class="spinner"></i>正在读取本地证据…</div>';}

function empty(t,d,action){
  const btn=action&&action.label?`<button class="button" data-empty-act="${esc(action.act)}">${esc(action.label)}</button>`:'';
  return `<div class="empty"><b>${esc(t)}</b>${esc(d)}${btn}</div>`;
}

function directionOptions(){return '<option value="">全部研究方向</option>'+Object.entries(dirs).map(([k,v])=>`<option value="${k}">${esc(v)}</option>`).join('');}

function metaLink(p,page){if(p.attachment_key)return `zotero://open-pdf/library/items/${p.attachment_key}${page?'?page='+page:''}`;if(p.zotero_key)return 'zotero://select/library/items/'+p.zotero_key;return (window.STATIC_GRAPH?'http://127.0.0.1:8765':'')+'/api/file?paper='+encodeURIComponent(p.id)+(page?'#page='+page:'');}

const readCache=new Map(),readPending=new Map();
let readCacheEpoch=0;
function clearReadCache(){readCacheEpoch++;readCache.clear();readPending.clear();}
async function api(path,body){
  if(window.STATIC_GRAPH)return staticAPI(path,body);
  if(body)clearReadCache();
  const url=new URL(path,'http://local'),name=url.pathname;
  const cacheable=!body&&(['/api/methods','/api/overview','/api/chains/summary'].includes(name)||name.startsWith('/api/chains/branch-')||
    (['/api/chains','/api/timeline'].includes(name)&&url.searchParams.get('compact')==='true'));
  const epoch=readCacheEpoch;
  if(cacheable){
    const saved=readCache.get(path);
    if(saved&&saved.expires>Date.now()){readCache.delete(path);readCache.set(path,saved);return saved.data;}
    if(readPending.has(path))return readPending.get(path);
  }
  const request=(async()=>{
    const response=await fetch(path,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json','X-KB-Token':state.token}:{},body:body?JSON.stringify(body):undefined});
    const data=await response.json();if(!response.ok){const error=Error(typeof data.detail==='string'?data.detail:'请求未完成');error.status=response.status;throw error;}
    if(cacheable&&epoch===readCacheEpoch){readCache.set(path,{data,expires:Date.now()+15000});while(readCache.size>24)readCache.delete(readCache.keys().next().value);}
    return data;
  })();
  if(cacheable)readPending.set(path,request);
  try{return await request;}finally{if(readPending.get(path)===request)readPending.delete(path);}
}

function methodScope(data,direction=''){
  if(!data.taxonomy)return data.nodes;
  const order=Object.keys(data.taxonomy.families),allowed=new Set(data.taxonomy.directions[direction]||order);
  return data.nodes.map(p=>{
    const families=(p.method_families||[]).filter(mid=>allowed.has(mid));
    if(!families.length)families.push('family:other');
    const primary=[...families].sort((a,b)=>(p.method_family_scores?.[b]||0)-(p.method_family_scores?.[a]||0)||order.indexOf(a)-order.indexOf(b))[0];
    return {...p,method_families:families,primary_method_family:primary,methods:[...(p.methods||[]).filter(mid=>!mid.startsWith('family:')),...families]};
  });
}

function staticAPI(path,body){
  const data=window.STATIC_GRAPH,u=new URL(path,'http://offline.local');
  if(path==='/api/session')return {token:''};
  if(u.pathname==='/api/status')return {...data.status,status_detail:true};if(path==='/api/models')return {items:[]};
  if(u.pathname==='/api/graph'){
    let pool=methodScope(data,u.searchParams.get('direction')||''),edges=data.edges.filter(e=>u.searchParams.get('candidates')==='true'||e.status==='confirmed');
    const pid=u.searchParams.get('paper'),project=u.searchParams.get('project')==='true',direction=u.searchParams.get('direction'),q=u.searchParams.get('q');
    if(pid){const ids=new Set([pid,...edges.filter(e=>e.src===pid||e.dst===pid).flatMap(e=>[e.src,e.dst])]);pool=pool.filter(p=>ids.has(p.id));}
    const method=u.searchParams.get('method')||'';
    if(project||direction||q||method)pool=$kb.graph.scopedGraphNodes(pool,edges,{project,direction,q,method});
    const semantic=new Set(edges.filter(e=>e.kind!=='cites'&&e.status==='confirmed').flatMap(e=>[e.src,e.dst]));
    const core=p=>['A','B'].some(x=>(p.tagfields?.priority||[]).includes(x))||['Core-A','Core-B'].some(x=>(p.tagfields?.reading_tier||[]).includes(x));
    pool=[...pool].sort((a,b)=>(a.id!==pid)-(b.id!==pid)||(!semantic.has(a.id))-(!semantic.has(b.id))||(!core(a))-(!core(b))||(a.kind!=='library')-(b.kind!=='library')||(b.year||0)-(a.year||0)||(a.title<b.title?-1:a.title>b.title?1:0));
    const offset=+(u.searchParams.get('offset')||0),limit=+(u.searchParams.get('limit')||180),page=pool.slice(offset,offset+limit),ids=new Set(page.map(n=>n.id));
    return {nodes:page,edges:edges.filter(e=>ids.has(e.src)&&ids.has(e.dst)),total:pool.length,has_more:offset+limit<pool.length};
  }
  if(u.pathname==='/api/graph/edges'){const ids=new Set(body.ids);return {edges:data.edges.filter(e=>ids.has(e.src)&&ids.has(e.dst)&&(body.candidates||e.status==='confirmed'))};}
  if(u.pathname==='/api/paper'){const id=u.searchParams.get('paper'),paper=data.nodes.find(p=>p.id===id);return {paper,relations:data.edges.filter(e=>e.src===id||e.dst===id),approvals:[],references:$kb.references.staticReferences(data,paper)};}
  if(u.pathname==='/api/references')return $kb.references.staticReferences(data,data.nodes.find(p=>p.id===u.searchParams.get('paper')));
  if(u.pathname==='/api/chains')return {items:data.chains};
  if(u.pathname==='/api/overview')return data.overview;
  if(u.pathname==='/api/methods'){
    const direction=u.searchParams.get('direction'),project=u.searchParams.get('project')==='true',q=(u.searchParams.get('q')||'').toLowerCase();
    const pool=methodScope(data,direction||'').filter(p=>(!direction||(p.directions||[]).includes(direction))&&(!project||p.project)&&(!q||(p.title+' '+(p.citekey||'')+' '+(p.authors||'')).toLowerCase().includes(q)));
    return {items:(data.methods||[]).map(m=>({...m,papers:pool.filter(p=>(p.method_families||p.methods||[]).includes(m.id)).length})).filter(m=>m.papers)};
  }
  if(u.pathname==='/api/relation')return data.edges.find(e=>e.id===u.searchParams.get('rid'));
  if(u.pathname==='/api/timeline'){const d=u.searchParams.get('direction'),q=u.searchParams.get('q')?.toLowerCase(),project=u.searchParams.get('project')==='true';return {...data.timeline,groups:data.timeline.groups.filter(g=>!d||g.direction===d).map(g=>({...g,paths:g.paths.filter(c=>(!project||c.nodes.some(p=>p.project))&&(!q||c.nodes.some(p=>(p.title+' '+p.citekey).toLowerCase().includes(q))))})),overview:data.overview};}
  if(path==='/api/opportunities')return data.opportunities;
  throw Error('这是离线图谱快照。检索、审批和设置请打开 http://127.0.0.1:8765/');
}

function intro(eyebrow,title,sub,metrics=''){return `<div class="intro"><div><div class="eyebrow">${eyebrow}</div><h1>${title}</h1><div class="subtitle">${sub}</div>${sub?'<button type="button" class="subtitle-toggle" data-toggle="subtitle" aria-expanded="false">展开说明</button>':''}</div>${metrics}</div>`;}
return {get clearReadCache(){return clearReadCache;},get readCacheEpoch(){return readCacheEpoch;},get methodScope(){return methodScope;},get $(){return $;},
get $$(){return $$;},
get esc(){return esc;},
get safe(){return safe;},
get titleNames(){return titleNames;},
get relationNames(){return relationNames;},
get sourceNames(){return sourceNames;},
get dirs(){return dirs;},
get state(){return state;},
get toastTimer(){return toastTimer;},set toastTimer(value){toastTimer=value;},
get toast(){return toast;},set toast(value){toast=value;},
get fmtDate(){return fmtDate;},set fmtDate(value){fmtDate=value;},
get loading(){return loading;},set loading(value){loading=value;},
get empty(){return empty;},set empty(value){empty=value;},
get directionOptions(){return directionOptions;},set directionOptions(value){directionOptions=value;},
get metaLink(){return metaLink;},set metaLink(value){metaLink=value;},
get api(){return api;},set api(value){api=value;},
get staticAPI(){return staticAPI;},set staticAPI(value){staticAPI=value;},
get intro(){return intro;},set intro(value){intro=value;},
iconURL(name){return (globalThis.KB_ICONS||{})[name]||'/icons/'+name+'.svg';}};
});
