const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../kbapp/web/assets/modules/graph.js'),'utf8');

// Minimal DOM adapter; requests are deferred to exercise real loadGraph and click handlers.
function harness(window={}){
  const registry=new Map(),elements=new Set();let clock=Date.now();
  class Element{
    constructor(attrs={},parent=null){this.attrs=attrs;this.id=attrs.id;this.dataset={};this.children=[];this.parent=parent;this.hidden=false;this.disabled=false;this.style={};this.clientWidth=1100;
      this.classList={toggle(){},add(){},remove(){}};
      for(const [key,value] of Object.entries(attrs))if(key.startsWith('data-'))this.dataset[key.slice(5).replace(/-([a-z])/g,(_,c)=>c.toUpperCase())]=value;
      if(this.id)registry.set('#'+this.id,this);elements.add(this);
    }
    set innerHTML(html){
      const remove=e=>{e.children.forEach(remove);elements.delete(e);if(e.id)registry.delete('#'+e.id);};this.children.forEach(remove);this.children=[];this.html=html;
      for(const match of html.matchAll(/<(?:button|div|select|article|span|label)\b([^>]*)>/g)){
        const attrs=Object.fromEntries([...match[1].matchAll(/([\w-]+)="([^"]*)"/g)].map(m=>[m[1],m[2]]));
        this.children.push(new Element(attrs,this));
      }
    }
    get innerHTML(){return this.html||'';}
    setAttribute(k,v){this.attrs[k]=String(v);}
    removeAttribute(k){delete this.attrs[k];}
    closest(){return this.label||(this.label=new Element());}
    contains(e){return this.children.includes(e);}
    querySelectorAll(selector){return select(selector,this.children);}
  }
  const select=(selector,pool=elements)=>{
    const match=selector.match(/\[([\w-]+)(?:="([^"]*)")?\]$/);
    if(!match)return [];
    return [...pool].filter(e=>Object.hasOwn(e.attrs,match[1])&&(match[2]===undefined||e.attrs[match[1]]===match[2]));
  };
  for(const id of ['lineage-panel','network-panel','show-candidates','graph-count','network-pagination','zoom-out','zoom-fit','zoom-in','net-controls','graph-svg','graph-legend-note','graph-legend','graph-order','graph-more','graph-page-info','graph-hint','stale-banner','stale-text'])new Element({id});
  const requests=[],errors=[],opened=[];
  const core={state:{page:'graph',graphMode:'chains',graphQuery:{direction:'02',project:true,q:'Anchor',method:'m',candidates:false},nodes:[],edges:[]},
    $:selector=>registry.get(selector)||null,$$:selector=>select(selector),
    esc:value=>String(value??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;'),
    loading:()=>'<p>正在载入</p>',empty:(title,sub)=>`<p>${title}</p><p>${sub}</p>`,relationNames:{inherits:'继承',improves:'改进'},
    api:url=>new Promise((resolve,reject)=>requests.push({url,resolve,reject}))};
  const ctx={core,status:{renderMethodProgress(){}},feedback:{showError:message=>errors.push(message)},detail:{showPaper:id=>opened.push(id),showRelation:e=>opened.push(e.id)},references:{citationBadge:()=>''}};
  let graph;
  vm.runInNewContext(source,{KB:{define:(name,factory)=>graph=factory(ctx)},URLSearchParams,console,setTimeout,clearTimeout,Date:class extends Date{static now(){return clock;}},document:{body:{classList:{toggle(){}}}},window});
  return {graph,core,requests,errors,opened,advance:ms=>clock+=ms,enableMethods:()=>new Element({id:'graph-method'}),get:id=>registry.get('#'+id),find:(key,value)=>[...elements].find(e=>e.dataset[key]===value)};
}
const summary=(id='branch-a',extra={})=>({items:[{id,name:'Sampling',nodes:3,edges:2,fulltext:1,confirmed:1,pending:1,updated:'2026',value:6}],total:1,offset:0,limit:20,has_more:false,sort:'value',revision:'r1',...extra});
const detail=(offset=0,extra={})=>({id:'branch-a',name:'Sampling',nodes:[{id:'a',title:'Ancestor',year:2000},{id:offset?'c':'b',title:offset?'Continuation':'Anchor',year:2020}],edges:[{id:'e'+offset,src:offset?'c':'b',dst:'a',kind:'inherits',status:'confirmed',level:'fulltext',evidence_grade:offset?'title':'fulltext',evidence_label:offset?'题目线索，待核':'原文已核 · 模型复核',evidence:[]}],total:2,offset,limit:1,has_more:!offset,revision:'r1',...extra});
const tick=()=>new Promise(resolve=>setImmediate(resolve));
async function start(h,data=summary()){const pending=h.graph.loadGraph();h.requests[0].resolve(data);await pending;}

test('initial chains request is filtered summary only, with visible candidate control',async()=>{
  const h=harness();await start(h);
  const url=new URL(h.requests[0].url,'http://test');assert.equal(url.pathname,'/api/chains/summary');
  for(const [key,value] of Object.entries(h.core.state.graphQuery))assert.equal(url.searchParams.get(key),String(value));
  assert.equal(h.requests.length,1);assert.equal(h.core.state.nodes.length,0);assert.equal(h.core.state.edges.length,0);
  assert.equal(h.get('show-candidates').closest().hidden,false);assert.ok(h.find('chainToggle','branch-a'));
});

test('branch opens on demand, paginates, deduplicates nodes and keeps explicit evidence grades',async()=>{
  const h=harness();await start(h);const toggle=h.find('chainToggle','branch-a');assert.ok(toggle,'summary needs a working expansion control');
  toggle.onclick();assert.equal(h.requests.length,2);assert.match(h.requests[1].url,/\/api\/chains\/branch-a\?/);
  const params=new URL(h.requests[1].url,'http://test').searchParams;assert.equal(params.get('revision'),'r1');assert.equal(params.get('method'),'m');
  h.requests[1].resolve(detail());await tick();assert.equal(h.core.state.edges.length,1);
  const more=h.find('chainMore','branch-a');assert.ok(more);more.onclick();
  assert.equal(new URL(h.requests[2].url,'http://test').searchParams.get('offset'),'1');
  h.requests[2].resolve(detail(1));await tick();assert.deepEqual(Array.from(h.core.state.edges,e=>e.id),['e0','e1']);assert.equal(h.core.state.nodes.length,3);
  h.find('chainToggle','branch-a').onclick();h.find('chainToggle','branch-a').onclick();assert.equal(h.requests.length,3,'reopen should reuse loaded pages');
  assert.equal(h.graph.lineageRelationView(detail(1).edges[0],detail(1).nodes).grade,'title');
  h.find('evidence','e1').onclick();assert.deepEqual(h.opened,['e1']);
});

test('summary paging sends revision and preserves earlier summaries',async()=>{
  const h=harness();await start(h,summary('branch-a',{total:2,limit:1,has_more:true}));
  assert.ok(h.get('chains-more'),'summary needs a page control');h.get('chains-more').onclick();
  const params=new URL(h.requests[1].url,'http://test').searchParams;assert.equal(params.get('offset'),'1');assert.equal(params.get('revision'),'r1');
  h.requests[1].resolve(summary('branch-b',{total:2,offset:1,limit:1}));await tick();assert.ok(h.find('chainToggle','branch-a'));assert.ok(h.find('chainToggle','branch-b'));
});

test('stale summary success and errors cannot overwrite the latest filter result',async()=>{
  const h=harness(),first=h.graph.loadGraph();h.core.state.graphQuery.q='new';const second=h.graph.loadGraph();
  h.requests[1].resolve(summary('fresh'));await second;h.requests[0].resolve(summary('stale'));await first;
  assert.ok(h.find('chainToggle','fresh'));assert.equal(h.find('chainToggle','stale'),undefined);
  const old=h.graph.loadGraph();h.core.state.graphQuery.q='newer';const newer=h.graph.loadGraph();h.requests[3].resolve(summary('latest'));await newer;
  h.requests[2].reject(Error('old failure'));await old;assert.equal(h.errors.length,0);assert.ok(h.find('chainToggle','latest'));
});

test('late detail response is ignored after filter change and errors offer retry',async()=>{
  const h=harness();await start(h);assert.ok(h.find('chainToggle','branch-a'));h.find('chainToggle','branch-a').onclick();
  h.core.state.graphQuery.q='new';const fresh=h.graph.loadGraph();h.requests[2].resolve(summary('new'));await fresh;
  h.requests[1].resolve(detail());await tick();assert.equal(h.core.state.edges.length,0);assert.ok(h.find('chainToggle','new'));
  h.find('chainToggle','new').onclick();h.requests[3].reject(Error('offline'));await tick();assert.ok(h.find('chainRetry','new'));h.find('chainRetry','new').onclick();assert.equal(h.requests.length,5);
});

test('revision mismatch never merges detail and exposes stale refresh',async()=>{
  const h=harness();await start(h);assert.ok(h.find('chainToggle','branch-a'));h.find('chainToggle','branch-a').onclick();
  h.requests[1].resolve(detail(0,{revision:'r2'}));await tick();assert.equal(h.core.state.edges.length,0);assert.equal(h.get('stale-banner').hidden,false);
});

test('stale/manual evidence cannot be rendered as an already verified method',()=>{
  const h=harness(),nodes=detail().nodes;
  const view=h.graph.lineageRelationView({...detail(1).edges[0],manual:true,evidence_label:'题目线索，待核'},nodes);
  assert.match(view.evidence,/待核/);assert.equal(view.grade,'title');
  const stale=h.graph.lineageRelationView({...detail().edges[0],evidence_stale:true},nodes);
  assert.notEqual(stale.grade,'confirmed');
});

test('old offline snapshot renders lazy summaries without unsupported API calls',async()=>{
  const data=detail();data.nodes[1]={...data.nodes[1],directions:['02'],project:true,methods:['m']};
  const h=harness({STATIC_GRAPH:{chains:[{name:'Sampling',nodes:data.nodes,edges:data.edges}]}});
  // Static exports already embed all data; this exercises the compatibility adapter, not HTTP.
  h.core.api=async()=>{throw Error('old snapshot has no summary endpoint');};
  await h.graph.loadGraph();
  const toggle=h.find('chainToggle','offline-0');assert.ok(toggle);assert.equal(h.core.state.nodes.length,0);
  await toggle.onclick();assert.equal(h.core.state.edges.length,1);assert.equal(h.core.state.nodes.length,2);
});

test('empty summary stays empty without requesting details',async()=>{
  const h=harness();await start(h,summary('unused',{items:[],total:0}));assert.equal(h.requests.length,1);assert.equal(h.core.state.nodes.length,0);
  assert.match(h.get('lineage-panel').innerHTML,/暂无|没有/);assert.equal(h.find('chainToggle','unused'),undefined);
});

test('direction changes reload broad methods, clear obsolete choice and ignore stale response',async()=>{
  const h=harness(),select=h.enableMethods();
  h.core.state.graphQuery.method='family:sampling';
  const first=h.graph.loadGraph();
  assert.equal(new URL(h.requests[0].url,'http://test').pathname,'/api/methods');
  assert.equal(new URL(h.requests[0].url,'http://test').searchParams.get('direction'),'02');
  h.core.state.graphQuery.direction='08';const second=h.graph.loadGraph();
  h.requests[1].resolve({items:[{id:'family:perception',name:'感知、定位与信号处理',papers:8}]});await tick();
  assert.equal(h.core.state.graphQuery.method,'');assert.match(select.innerHTML,/感知/);
  assert.equal(new URL(h.requests[2].url,'http://test').searchParams.get('method'),'');
  h.requests[0].resolve({items:[{id:'family:sampling',name:'基于采样',papers:20}]});await first;
  assert.ok(!select.innerHTML.includes('基于采样'));
  h.requests[2].resolve(summary());await second;assert.equal(select.disabled,false);
});

test('unlinked papers are visible and independently paginated without dropping relation pages',async()=>{
  const h=harness();await start(h);h.find('chainToggle','branch-a').onclick();
  h.requests[1].resolve(detail(0,{unlinked_nodes:[{id:'u1',title:'Unlinked paper'}],unlinked_total:2,paper_offset:0,paper_has_more:true}));await tick();
  assert.ok(h.find('unlinkedPaper','u1'));h.find('unlinkedPaper','u1').onclick();assert.equal(h.opened[0],'u1');
  h.find('chainPapers','branch-a').onclick();
  assert.equal(new URL(h.requests[2].url,'http://test').searchParams.get('paper_offset'),'1');
  h.requests[2].resolve(detail(0,{unlinked_nodes:[{id:'u2',title:'Another unlinked paper'}],unlinked_total:2,paper_offset:1,paper_has_more:false}));await tick();
  assert.ok(h.find('unlinkedPaper','u2'));assert.equal(h.core.state.edges.length,1);
  assert.ok(h.find('chainMore','branch-a'),'loading unlinked papers must preserve the relation cursor');
});

test('new offline snapshot groups every reference and unlinked paper into broad families',async()=>{
  const family='family:sampling',data={nodes:[
    {id:'a',title:'RRT planning',directions:['02'],method_families:[family],methods:[family],method_family_scores:{[family]:4}},
    {id:'b',title:'External reference',directions:[],method_families:['family:other'],methods:['family:other']},
    {id:'c',title:'Unlinked RRT',directions:['02'],method_families:[family],methods:[family],method_family_scores:{[family]:4}},
  ],edges:[{id:'cite',src:'a',dst:'b',kind:'cites',status:'confirmed',level:'citation',evidence_grade:'citation'}],
    taxonomy:{families:{[family]:'基于采样','family:other':'其他研究与待分类'},branches:{[family]:'sampling','family:other':'other'},directions:{'02':[family,'family:other']}}};
  const h=harness({STATIC_GRAPH:data});
  let core;
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../kbapp/web/assets/modules/core.js'),'utf8'),{KB:{define:(name,factory)=>core=factory({})},document:{}});
  h.core.methodScope=core.methodScope;h.core.state.graphQuery={direction:'02',project:false,q:'',method:'',candidates:true};
  await h.graph.loadGraph();
  assert.match(h.get('lineage-panel').innerHTML,/全库 3 篇/);assert.match(h.get('lineage-panel').innerHTML,/引用 1/);
  await h.find('chainToggle','sampling').onclick();
  assert.deepEqual(Array.from(h.core.state.edges,e=>e.id),['cite']);assert.ok(h.find('unlinkedPaper','c'));
  assert.match(h.get('lineage-panel').innerHTML,/方法已核 0/);
});

test('returning to the same graph restores expanded branch pages until data invalidation',async()=>{
  const h=harness();await start(h);h.find('chainToggle','branch-a').onclick();h.requests[1].resolve(detail());await tick();
  h.core.state.page='research';h.core.state.page='graph';await h.graph.loadGraph(false,'',true);
  assert.equal(h.requests.length,2);assert.equal(h.core.state.edges.length,1);assert.ok(h.find('chainMore','branch-a'));
  h.core.readCacheEpoch=1;const refreshed=h.graph.loadGraph();assert.equal(h.requests.length,3);
  h.requests[2].resolve(summary());await refreshed;assert.equal(h.core.state.edges.length,0);
});

test('expired page-return cache keeps expanded data visible while revision is revalidated',async()=>{
  const h=harness();await start(h);h.find('chainToggle','branch-a').onclick();h.requests[1].resolve(detail());await tick();
  h.advance(16000);h.core.state.page='research';h.core.state.page='graph';await h.graph.loadGraph(false,'',true);
  assert.equal(h.core.state.edges.length,1);assert.ok(h.find('chainMore','branch-a'));assert.equal(h.requests.length,3);
  h.requests[2].resolve(summary());await tick();assert.equal(h.core.state.edges.length,1);
});

test('page-return restarts a detail request ignored while its page was hidden',async()=>{
  const h=harness();await start(h);h.find('chainToggle','branch-a').onclick();h.core.state.page='research';
  h.requests[1].resolve(detail());await tick();h.core.state.page='graph';await h.graph.loadGraph(false,'',true);
  assert.equal(h.requests.length,3);h.requests[2].resolve(detail());await tick();assert.equal(h.core.state.edges.length,1);
});
