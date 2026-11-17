const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const context={document:{readyState:'loading',addEventListener(){}},console};vm.createContext(context);
vm.runInContext(require('./web-source.cjs').source()+'\nglobalThis.referenceTest={citationView,citationBadge,referenceListHTML,staticReferences,lineageDiagramHTML,layoutLineage};',context);
const view=context.referenceTest;
test('unknown is hollow and zero is a measured zero; every range boundary is explicit',()=>{
  for(const [n,level] of [[null,'unknown'],[0,'zero'],[1,'low'],[9,'low'],[10,'medium'],[99,'medium'],[100,'high'],[999,'high'],[1000,'very-high']])assert.equal(view.citationView({citation_count:n}).level,level);
  assert.equal(view.citationView({citation_count:'50'}).level,'unknown');assert.match(view.citationBadge({citation_count:0}),/被引 0 次/);
});
test('citation tooltip exposes source and escapes untrusted metadata',()=>{
  const html=view.citationBadge({citation_count:24,citation_source:'<img onerror=x>'},true);
  assert.match(html,/被引 24 次/);assert.match(html,/&lt;img/);assert.ok(!html.includes('<img'));
});
test('reference sorting keeps unknown below known zero and does not invent a paper link',()=>{
  const html=view.referenceListHTML({items:[{title:'Unknown',doi:'10.x/a"',raw:'<script>x</script>'},{title:'Zero',paper_id:'p',citation_count:0},{title:'High',paper_id:'q',citation_count:100}]},'','citations');
  assert.ok(html.indexOf('High')<html.indexOf('Zero'));assert.ok(html.indexOf('Zero')<html.indexOf('Unknown'));
  assert.ok(!html.includes('<script>'));assert.equal((html.match(/data-reference-paper=/g)||[]).length,2);
});
test('offline references use outgoing citation facts and keep citation count separate from graph degree',()=>{
  const data={nodes:[{id:'p',citation_count:100},{id:'r',title:'Ref',citation_count:0}],edges:[{src:'p',dst:'r',kind:'cites',status:'confirmed'},{src:'r',dst:'p',kind:'improves'}]};
  const result=view.staticReferences(data,data.nodes[0]);assert.equal(result.total,1);assert.equal(result.items[0].citation_count,0);assert.equal(result.citation.citation_count,100);assert.equal(result.job.status,'offline');
  data.references={p:{items:[{paper_id:'r',title:'Ref',citation_count:50}]}};
  assert.equal(view.staticReferences(data,data.nodes[0]).items[0].citation_count,0);
});
test('reference filter covers raw text and title circles also appear in method chain cards',()=>{
  const html=view.referenceListHTML({items:[{title:'One',raw:'Author DOI text',citation_count:12},{title:'Two'}]},'doi');
  assert.match(html,/One/);assert.ok(!html.includes('Two'));
  const chain={name:'Test',nodes:[{id:'a',title:'A',year:2020,citation_count:123}],edges:[]};
  const cards=view.lineageDiagramHTML(chain,view.layoutLineage(chain,700),'test');
  assert.match(cards,/citation-dot high/);
});
