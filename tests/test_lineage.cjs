const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=require('./web-source.cjs').source();
const context={document:{readyState:'loading',addEventListener(){}},console};
vm.createContext(context);
vm.runInContext(source+'\nglobalThis.lineageTest={layoutLineage,lineageRelationView,lineageDiagramHTML,lineageTooltipHTML,lineageEdgePath,scopedGraphNodes,answerHTML,staticAPI};',context);
const view=context.lineageTest;
const paper=(id,year)=>({id,year,citekey:id,title:id});
const edge=(from,to,kind='inherits')=>({id:from+'-'+to,src:to,dst:from,kind,status:'confirmed',level:'fulltext',evidence:[]});
test('scope keeps external ancestors and bridge nodes and explains inclusion',()=>{
  const nodes=[{id:'p',project:'project',directions:['02'],title:'P'},{id:'a',title:'A'},{id:'b',title:'B'},{id:'other',title:'Other'}];
  const selected=view.scopedGraphNodes(nodes,[edge('a','p'),edge('b','a')],{project:true,direction:'02'});
  assert.deepEqual(Array.from(selected,p=>p.id),['p','a','b']);
  assert.match(selected.find(p=>p.id==='b').selection_reason,/桥接/);
});
test('explicit evidence grade overrides a legacy fulltext status and claim links preserve literal source text',()=>{
  assert.equal(view.lineageRelationView({...edge('a','b'),evidence_grade:'title'},[paper('a',2000),paper('b',2001)]).grade,'title');
  const html=view.answerHTML({answer:'结论 [1] <script>x</script>',citations:[{title:'Source',quote:'<img onerror=x>',evidence_level:'abstract',url:'zotero://source'}]});
  assert.match(html,/data-claim-ref="answer-\d+-1"/);assert.match(html,/摘要支持/);assert.ok(!html.includes('<script>'));assert.ok(!html.includes('<img'));
});
test('offline graph uses the same ancestor and bridge filtering rules',()=>{
  context.URL=URL;context.window={STATIC_GRAPH:{nodes:[{id:'p',title:'P',project:'p',kind:'library',year:2020},{id:'a',title:'A',kind:'external',year:2010},{id:'b',title:'B',kind:'external',year:2000},{id:'other',title:'Other',kind:'external'}],edges:[edge('a','p'),edge('b','a')]}};
  const graph=view.staticAPI('/api/graph?project=true&limit=100');
  assert.deepEqual(Array.from(graph.nodes,p=>p.id),['p','a','b']);assert.equal(graph.edges.length,2);
  context.window.STATIC_GRAPH.timeline={groups:[],method_families:[{name:'Sampling',nodes:[],edges:[]}]};context.window.STATIC_GRAPH.overview={};
  assert.equal(view.staticAPI('/api/timeline?project=true').method_families.length,1);
});
function example(){return {name:'采样与最优规划',nodes:[paper('RRT',1998),paper('RRTstar',2011),paper('Informed',2014),paper('BIT',2015),paper('VK',2026)],edges:[edge('RRT','RRTstar'),edge('RRTstar','Informed','improves'),edge('Informed','BIT'),edge('RRTstar','VK','improves')]};}
function noOverlap(layout){
  const positions=[...layout.positions.values()];
  for(let i=0;i<positions.length;i++)for(let j=i+1;j<positions.length;j++){
    const a=positions[i],b=positions[j];
    assert.ok(a.x+layout.boxW<=b.x||b.x+layout.boxW<=a.x||a.y+layout.boxH<=b.y||b.y+layout.boxH<=a.y,'cards overlap');
  }
}
test('all branches share chronological columns; 2026 stays to the right of 2015',()=>{
  const chain=example(),before=JSON.stringify(chain),layout=view.layoutLineage(chain,1480),p=layout.positions;
  assert.ok(p.get('VK').x>p.get('BIT').x);
  assert.ok(p.get('VK').y>p.get('BIT').y);
  assert.equal(p.get('RRTstar').y,p.get('BIT').y);
  assert.equal(JSON.stringify(layout.columns.map(c=>c.year)),'[1998,2011,2014,2015,2026]');
  assert.equal(layout.edges.find(e=>e.src==='VK').dst,'RRTstar');
  assert.equal(JSON.stringify(chain),before);noOverlap(layout);
});
test('same-year dependencies get subcolumns, independent roots and a merger keep every node',()=>{
  const chain={nodes:[paper('A',2020),paper('B',2020),paper('root',2019),paper('other',2020),paper('merge',2021)],edges:[edge('root','A'),edge('A','B'),edge('B','merge'),edge('other','merge','combines')]};
  const layout=view.layoutLineage(chain,1400);
  assert.ok(layout.positions.get('B').x>layout.positions.get('A').x);
  assert.equal(layout.nodes.length,5);assert.equal(layout.edges.length,4);noOverlap(layout);
});
test('missing years, reverse years and cycles retain their data with warnings',()=>{
  const chain={nodes:[paper('late',2026),paper('early',2020),paper('unknown',null),paper('same1',2021),paper('same2',2021)],edges:[edge('late','early'),edge('early','unknown'),edge('same1','same2'),edge('same2','same1')]};
  const layout=view.layoutLineage(chain,1200);
  assert.equal(layout.nodes.length,5);assert.equal(layout.edges.length,4);
  assert.ok(layout.positions.get('unknown').x>layout.positions.get('late').x);
  assert.ok(layout.warnings.some(w=>w.includes('前作年份晚于后作')));
  assert.ok(layout.warnings.some(w=>w.includes('循环关系')));noOverlap(layout);
});
test('narrow layouts progress down the page in chronological order with side branches',()=>{
  const layout=view.layoutLineage(example(),360),p=layout.positions;
  assert.equal(layout.vertical,true);assert.ok(p.get('VK').y>p.get('BIT').y);
  const branch=layout.edges.find(e=>e.src==='VK'),route=view.lineageEdgePath(layout,branch,3);
  assert.ok(route.startsWith('M'+p.get('RRTstar').x+','));assert.ok(route.endsWith('L'+p.get('VK').x+','+(p.get('VK').y+layout.boxH/2)));
  assert.ok(layout.width<=360);noOverlap(layout);
});
test('a clear branch row has one level change and a long horizontal approach to its target',()=>{
  const layout=view.layoutLineage(example(),1480),route=view.lineageEdgePath(layout,layout.edges[3],3),source=layout.positions.get('RRTstar'),target=layout.positions.get('VK');
  const endpoints=[...route.matchAll(/[MLQC]([\d., -]+)/g)].map(m=>m[1].trim().split(/[ ,]+/).map(Number).slice(-2));
  const beforeTarget=endpoints.at(-2),end=endpoints.at(-1);
  assert.equal((route.match(/Q/g)||[]).length,2);
  assert.equal(beforeTarget[1],target.y+layout.boxH/2);
  assert.ok(beforeTarget[0]<(source.x+layout.boxW+target.x)/2,'branch should already be on the target row before its final approach');
  assert.deepEqual(end,[target.x,target.y+layout.boxH/2]);assert.ok(!route.includes('NaN'));
});
test('a branch still uses the gutter when another card blocks the target row',()=>{
  const layout=view.layoutLineage(example(),1480),source=layout.positions.get('RRTstar'),target=layout.positions.get('VK');
  layout.nodes.push(paper('blocker',2020));layout.positions.set('blocker',{x:(source.x+layout.boxW+target.x-layout.boxW)/2,y:target.y,lane:target.lane,column:3});
  const route=view.lineageEdgePath(layout,layout.edges[3],3);
  assert.ok(route.includes(','+(source.y+layout.boxH+24)));
  assert.ok(route.endsWith('L'+(target.x-30)+','+(target.y+layout.boxH/2)+' L'+target.x+','+(target.y+layout.boxH/2)));
});
test('relationship details prefer the saved change and preserve conditions and page evidence',()=>{
  const chain=example(),relation={...chain.edges[1],change:'只在可改进当前解的区域采样',explanation:'旧解释',conditions:'获得初始解之后',evidence:[{page:2}]};
  const detail=view.lineageRelationView(relation,chain.nodes),html=view.lineageTooltipHTML(detail);
  assert.equal(detail.color,'#0f6f8f');assert.equal(detail.change,relation.change);
  assert.ok(html.includes(relation.conditions));assert.ok(html.includes('p.2'));assert.ok(!html.includes('旧解释'));
  assert.equal(view.lineageRelationView({...relation,change:'',explanation:''},chain.nodes).change,'具体变化待补充');
});
test('tooltips escape source text and evidence grades never imply title evidence is full text',()=>{
  const chain=example(),relation={...chain.edges[1],change:'<img src=x onerror=alert(1)>',level:'title_only',status:'candidate'};
  const detail=view.lineageRelationView(relation,chain.nodes),html=view.lineageTooltipHTML(detail);
  assert.ok(html.includes('&lt;img'));assert.ok(!html.includes('<img'));assert.ok(html.includes('题目线索'));assert.ok(!html.includes('原文已核'));
});
test('multiple diagrams have distinct marker references, matching kind colors and keyboard entry points',()=>{
  const chain=example();chain.edges.push(edge('BIT','VK','combines'),edge('RRT','VK','transfers'));
  const layout=view.layoutLineage(chain,1480),first=view.lineageDiagramHTML(chain,layout,'first'),second=view.lineageDiagramHTML(chain,layout,'second');
  const ids=html=>[...html.matchAll(/<marker id="([^"]+)"/g)].map(m=>m[1]);
  assert.ok(ids(first).every(id=>!ids(second).includes(id)));
  for(const color of ['#2f7d4f','#0f6f8f','#7a52cc','#c08a2a'])assert.ok(first.includes('fill="'+color+'"'));
  assert.equal((first.match(/data-lineage-edge=/g)||[]).length,chain.edges.length);
  assert.equal((first.match(/tabindex="0" role="button"/g)||[]).length,chain.edges.length);
});
