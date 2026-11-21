const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=require('./web-source.cjs').source();

function setup(responses){
  const elements=new Map(),requests=[];
  const element=key=>{if(!elements.has(key))elements.set(key,{innerHTML:'',value:'',checked:false,disabled:false,hidden:false,open:false,style:{},textContent:'',insertAdjacentHTML(){},querySelector(){return null;},showModal(){this.open=true;},close(){this.open=false;},select(){},focus(){}});return elements.get(key);};
  const context={console,URL,URLSearchParams,setTimeout:callback=>{callback();return 1;},clearTimeout(){},
    document:{readyState:'loading',addEventListener(){},querySelector:element,querySelectorAll:()=>[],createElement:()=>({value:'',setAttribute(){},style:{},select(){},remove(){}}),body:{appendChild(){},removeChild(){}}},
    mockAPI:async path=>{requests.push(new URL(path,'http://local'));return responses.shift();}};
  vm.createContext(context);
  vm.runInContext(source+"\napi=mockAPI;state.page='search';state.status.cloud_enabled=true;globalThis.view={searchPage,retrievalSummary,renderResults,answerHTML,verificationHTML,comparisonHTML,evidenceSelection,evidenceToMarkdown,toggleEvidence,clearEvidenceSelection,updateEvidenceToolbar,copyEvidenceMarkdown,resolveEvidenceLink};",context);
  return {context,elements,requests,view:context.view,element};
}

test('semantic search waits for vector completion and never requests a keyword preview',async()=>{
  const app=setup([{vector_status:'queued',mode:'bm25',results:[]},{vector_status:'ready',mode:'bm25+vector(rrf)',results:[]}]);
  app.view.searchPage();app.element('#query').value='narrow passages';
  app.element('#search-form').onsubmit({preventDefault(){}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(app.requests.length,2);
  assert.ok(app.requests.every(url=>url.searchParams.get('vectors')==='true'&&url.searchParams.get('wait_vectors')==='true'));
  assert.equal(app.requests[1].searchParams.get('resume_model'),'false');
  assert.match(app.element('#results').innerHTML,/语义向量 \+ 关键词融合/);
  assert.doesNotMatch(app.element('#results').innerHTML,/正在排队/);
});

test('a superseded search cannot overwrite the current question results or the evidence selection',async()=>{
  const app=setup([]);let resolve;
  app.context.mockAPI=()=>new Promise(done=>{resolve=done;});
  vm.runInContext('api=mockAPI;',app.context);app.view.searchPage();app.element('#query').value='first';
  app.element('#search-form').onsubmit({preventDefault(){}});
  const kept={id:'keep1',kind:'paper_text',title:'Kept evidence',text:'kept text',page_start:1,page_end:1};
  app.view.renderResults({results:[kept]});
  app.view.toggleEvidence(kept,true);
  vm.runInContext('searchGeneration++;',app.context);
  app.element('#results').innerHTML='current question';
  resolve({mode:'bm25',results:[{id:'stale1',kind:'paper_text',title:'Stale',text:'stale text',page_start:9,page_end:9}]});await new Promise(done=>setImmediate(done));
  assert.equal(app.element('#results').innerHTML,'current question');
  assert.equal(app.view.evidenceSelection.size,1);
  assert.ok(app.view.evidenceSelection.has('keep1')&&!app.view.evidenceSelection.has('stale1'));
  assert.match(app.element('#evidence-count').textContent,/已选 1 条证据/);
});

test('a late library browse response arriving after a newer search touches neither results nor the selection',async()=>{
  const app=setup([]);const browse=[];
  app.context.mockAPI=path=>{
    if(path.startsWith('/api/library'))return new Promise(done=>browse.push(done));
    if(path.startsWith('/api/search'))return Promise.resolve({mode:'bm25',results:[{id:'new1',kind:'paper_text',title:'Fresh search hit',text:'fresh text',page_start:1,page_end:1}]});
    throw Error('unexpected request: '+path);};
  vm.runInContext('api=mockAPI;',app.context);
  app.view.searchPage();app.element('#query').value='shared query';
  app.element('#browse-library').onclick();
  app.element('#search-form').onsubmit({preventDefault(){}});
  await new Promise(done=>setImmediate(done));
  assert.match(app.element('#results').innerHTML,/data-evidence-check="new1"/);
  app.view.toggleEvidence({id:'new1',kind:'paper_text',title:'Fresh search hit',text:'fresh text',page_start:1,page_end:1},true);
  assert.match(app.element('#evidence-count').textContent,/已选 1 条证据/);
  browse[0]({items:[{id:'stale1',title:'Stale browse hit',abstract:'stale abstract'}],total:1});
  await new Promise(done=>setImmediate(done));
  assert.match(app.element('#results').innerHTML,/data-evidence-check="new1"/);
  assert.doesNotMatch(app.element('#results').innerHTML,/Stale browse hit/);
  assert.equal(app.view.evidenceSelection.size,1);
  assert.ok(app.view.evidenceSelection.has('new1')&&!app.view.evidenceSelection.has('stale1'));
  assert.match(app.element('#evidence-count').textContent,/已选 1 条证据/);
});

test('a late older library page response cannot override the newer page request',async()=>{
  const app=setup([]);const pages=[];
  app.context.mockAPI=path=>{if(path.startsWith('/api/library'))return new Promise(done=>pages.push(done));throw Error('unexpected request: '+path);};
  vm.runInContext('api=mockAPI;',app.context);
  app.view.searchPage();app.element('#query').value='paged query';
  app.element('#browse-library').onclick();
  await new Promise(done=>setImmediate(done));
  pages.shift()({items:[{id:'p1',title:'Page one hit',abstract:'a'}],total:150});
  await new Promise(done=>setImmediate(done));
  assert.match(app.element('#results').innerHTML,/Page one hit/);
  app.view.toggleEvidence({id:'p1',kind:'metadata',title:'Page one hit'},true);
  app.element('#library-next').onclick();
  app.element('#library-prev').onclick();
  await new Promise(done=>setImmediate(done));
  pages.shift()({items:[{id:'p2',title:'Page two hit',abstract:'b'}],total:150});
  await new Promise(done=>setImmediate(done));
  assert.match(app.element('#results').innerHTML,/Page one hit/);
  assert.doesNotMatch(app.element('#results').innerHTML,/Page two hit/);
  assert.equal(app.view.evidenceSelection.size,1);
  assert.ok(app.view.evidenceSelection.has('p1'));
  pages.shift()({items:[{id:'p1',title:'Page one hit',abstract:'a'}],total:150});
  await new Promise(done=>setImmediate(done));
  assert.match(app.element('#results').innerHTML,/Page one hit/);
});

test('actual retrieval mode and pending coverage are distinct in results and answers',()=>{
  const app=setup([]),data={mode:'two-stage-hybrid+rerank',vector_status:'cached',coverage:{vectors:47439,published_sources:47439,live_vector_pending:5389},results:[{id:'s',kind:'paper_text',title:'Semantic source',text:'Evidence',retrieval:{vector_rank:1,keyword_rank:null}}]};
  app.view.renderResults(data);const html=app.element('#results').innerHTML;
  assert.match(html,/语义向量 \+ 关键词融合/);assert.match(html,/语义命中/);
  assert.match(html,/47,439 \/ 47,439/);assert.match(html,/5,389 条新原文待向量化/);
  assert.match(app.view.answerHTML({answer:'Answer',search:data}),/语义向量 \+ 关键词融合/);
  assert.match(app.view.retrievalSummary({mode:'two-stage-bm25+rerank'}),/关键词检索/);
  assert.doesNotMatch(app.view.retrievalSummary({mode:'two-stage-bm25+rerank'}),/关键词融合/);
  assert.match(app.view.retrievalSummary({mode:'two-stage-mixed+rerank'}),/部分查询/);
});


test('concurrent refresh triggers share one status request',async()=>{
  const app=setup([]);let resolve,calls=0;
  app.context.mockAPI=()=>{calls++;return new Promise(done=>{resolve=done;});};
  for(const name of ['renderIndexProgress','renderIndexDetails','renderJobRows','monitorTaskErrors'])app.context[name]=()=>{};
  vm.runInContext('api=mockAPI;renderMethodProgress=()=>{};renderCostMetric=()=>{};',app.context);
  const first=vm.runInContext('refreshStatus()',app.context),second=vm.runInContext('refreshStatus()',app.context);
  assert.equal(calls,1);resolve({});await Promise.all([first,second]);
  const third=vm.runInContext('refreshStatus()',app.context);assert.equal(calls,2);resolve({});await third;
});


test('vector indexing sends only the vectors action and deduplicates clicks while starting',async()=>{
  const app=setup([]),sent=[];let resolve;
  app.context.window={};
  app.context.mockAPI=(path,body)=>{sent.push({path,body});return new Promise(done=>{resolve=done;});};
  vm.runInContext("api=mockAPI;state.status.index_actions={vectors:true};",app.context);
  const first=vm.runInContext("taskDashboard.submit('vectors')",app.context),second=vm.runInContext("taskDashboard.submit('vectors')",app.context);
  assert.equal(sent.length,1);assert.equal(sent[0].path,'/api/jobs');assert.equal(sent[0].body.action,'vectors');
  resolve({state:'started'});await Promise.all([first,second]);
  vm.runInContext("state.status.indexing={state:'running',kind:'vectors'}",app.context);
  await assert.rejects(()=>vm.runInContext("taskDashboard.submit('vectors')",app.context));assert.equal(sent.length,1);
});

test('a failed dashboard submission releases its guard for retry',async()=>{
  const app=setup([]),errors=[];
  app.context.window={};app.context.mockAPI=async()=>{throw Error('queue busy');};app.context.capture=message=>errors.push(message);
  vm.runInContext('api=mockAPI;state.status.index_actions={vectors:true};',app.context);
  await assert.rejects(()=>vm.runInContext("taskDashboard.submit('vectors')",app.context),/queue busy/);
  app.context.mockAPI=async()=>({state:'started'});vm.runInContext('api=mockAPI;',app.context);
  assert.equal((await vm.runInContext("taskDashboard.submit('vectors')",app.context)).state,'started');
});

test('verification trace separates local uncertainty, cloud support and evidence grade',()=>{
  const {view}=setup([]);
  const html=view.verificationHTML({label:'云端复核通过',confidence:'medium',local_state:'insufficient',cloud_state:'supported',local:{reason:'上下文不足'},cloud:{reason:'补证支持'},supplement_rounds:1,supplement_reasons:['<unsafe>'],rule_version:'v2'});
  assert.match(html,/中置信度/);assert.match(html,/本地：证据不足/);assert.match(html,/云端：支持/);
  assert.match(html,/&lt;unsafe&gt;/);assert.match(html,/证据等级仍以原文／摘要标记为准/);
});
test('comparison shows explicit noncomparable conditions alongside all four fields',()=>{
  const {view}=setup([]);
  const html=view.comparisonHTML({comparison:[{title:'paper',method:[],assumptions:[],metrics:[],limitations:[]}],comparison_warnings:['实验条件不同，不可直接比较']},'c');
  for(const text of ['方法','假设','指标','局限','不可直接比较'])assert.ok(html.includes(text));
});

test('copying multiple selections lists every paper in current result order',()=>{
  const {view}=setup([]);
  const results=[
    {id:'a1',kind:'paper_text',title:'Paper One',text:'Alpha evidence',page_start:3,page_end:5,url:'zotero://open-pdf/library/items/KEY1?page=3'},
    {id:'b1',kind:'zotero_note',title:'Paper Two',text:'Beta evidence',page:7,url:'zotero://select/library/items/KEY2'}];
  const md=view.evidenceToMarkdown(['b1','a1'],results,{resolveLink:x=>x});
  assert.ok(md.indexOf('Paper One')>=0&&md.indexOf('Paper One')<md.indexOf('Paper Two'));
  assert.match(md,/论文原文/);assert.match(md,/Zotero 笔记/);
  assert.match(md,/p\.3–5/);assert.match(md,/· p\.7\b/);
  assert.match(md,/zotero:\/\/open-pdf\/library\/items\/KEY1\?page=3/);
  assert.match(md,/> Alpha evidence/);assert.match(md,/> Beta evidence/);
});

test('page ranges and single pages are labelled distinctly',()=>{
  const {view}=setup([]);
  const md=view.evidenceToMarkdown(['r1','r2','r3'],[
    {id:'r1',kind:'paper_text',title:'Range',text:'x',page_start:12,page_end:14},
    {id:'r2',kind:'paper_text',title:'Single',text:'x',page_start:4,page_end:4},
    {id:'r3',kind:'paper_text',title:'PageOnly',text:'x',page:9}],{resolveLink:x=>x});
  assert.match(md,/p\.12–14/);
  assert.match(md,/· p\.4\b/);assert.doesNotMatch(md,/p\.4–/);
  assert.match(md,/· p\.9\b/);assert.doesNotMatch(md,/p\.9–/);
});

test('two passages of one paper stay separate entries in result order',()=>{
  const {view}=setup([]);
  const results=[
    {id:'c1',paper_id:'P',kind:'paper_text',title:'Same Paper',text:'Section A',page_start:2,page_end:2},
    {id:'c2',paper_id:'P',kind:'paper_text',title:'Same Paper',text:'Section B',page_start:6,page_end:6}];
  const md=view.evidenceToMarkdown(['c2','c1'],results,{resolveLink:x=>x});
  assert.ok(md.indexOf('Section A')>=0&&md.indexOf('Section A')<md.indexOf('Section B'));
  assert.equal(md.split('**Same Paper**').length,3);
});

test('missing page numbers leave no placeholder',()=>{
  const {view}=setup([]);
  const md=view.evidenceToMarkdown(['n1'],[{id:'n1',kind:'metadata',title:'No Page',text:'abstract text'}],{resolveLink:x=>x});
  assert.doesNotMatch(md,/p\./);
  assert.match(md,/元数据与摘要/);
});

test('special markdown characters survive verbatim in the excerpt and title',()=>{
  const {view}=setup([]);
  const text=['Bold *star* and _under_ score','> already a quote','Back `tick` and [bracket] link'].join('\n');
  const md=view.evidenceToMarkdown(['m1'],[{id:'m1',kind:'paper_text',title:'Tricky [chars] *here*',text}],{resolveLink:x=>x});
  for(const token of ['*star*','_under_','> already a quote','`tick`','[bracket] link','Tricky [chars] *here*'])assert.ok(md.includes(token),'verbatim miss: '+token);
  assert.match(md,/^  > > already a quote$/m);
});

test('provenance markers survive into the copied markdown',()=>{
  const {view}=setup([]);
  const results=[
    {id:'l1',kind:'zotero_note',origin:'legacy_mixed',title:'Legacy Note',text:'old note'},
    {id:'l2',kind:'formula',latex_status:'legacy_unverified',title:'Legacy Formula',text:'E=mc^2'},
    {id:'l3',kind:'paper_text',ocr_provenance:{model:'GLM-OCR',pages:[3]},title:'OCR Page',text:'scanned'}];
  const md=view.evidenceToMarkdown(['l1','l2','l3'],results,{resolveLink:x=>x});
  assert.match(md,/历史混合笔记，可能含 AI 内容/);
  assert.match(md,/历史公式识别，尚未复核/);
  assert.match(md,/GLM-OCR 识别/);
  assert.match(md,/Zotero 笔记/);assert.match(md,/公式与上下文/);
});

test('links are whitelisted, absolutized and parenthesized destinations are encoded',()=>{
  const {view}=setup([]);
  const results=[
    {id:'u1',kind:'paper_text',title:'Evil',text:'x',url:'javascript:alert(1)'},
    {id:'u2',kind:'paper_text',title:'Rel',text:'x',url:'/api/file?paper=P%2Fx#page=2'},
    {id:'u3',kind:'paper_text',title:'Zot',text:'x',url:'zotero://open-pdf/library/items/KEY?page=3'},
    {id:'u4',kind:'external_abstract',title:'Doi',text:'x',url:'https://doi.org/10.1000/(weird)'}];
  const md=view.evidenceToMarkdown(['u1','u2','u3','u4'],results,{resolveLink:url=>url.startsWith('/')?'http://127.0.0.1:8765'+url:url});
  const evilLine=md.split('\n').find(line=>line.includes('**Evil**'));
  assert.ok(evilLine&&!evilLine.includes('打开来源'));
  assert.ok(!md.includes('javascript:'));
  assert.match(md,/\[打开来源\]\(http:\/\/127\.0\.0\.1:8765\/api\/file\?paper=P%2Fx#page=2\)/);
  assert.match(md,/\[打开来源\]\(zotero:\/\/open-pdf\/library\/items\/KEY\?page=3\)/);
  assert.match(md,/\[打开来源\]\(https:\/\/doi\.org\/10\.1000\/%28weird%29\)/);
});

test('the production link resolver absolutizes relative service links only',()=>{
  const app=setup([]);
  app.context.location={origin:'http://localhost:8765'};
  assert.equal(vm.runInContext('resolveEvidenceLink("/api/file?paper=x#page=2")',app.context),'http://localhost:8765/api/file?paper=x#page=2');
  assert.equal(vm.runInContext('resolveEvidenceLink("zotero://select/library/items/K")',app.context),'zotero://select/library/items/K');
  app.context.window={STATIC_GRAPH:true};
  assert.equal(vm.runInContext('resolveEvidenceLink("/api/file?paper=x")',app.context),'http://127.0.0.1:8765/api/file?paper=x');
});

test('copy button and counters follow the selection lifecycle',()=>{
  const app=setup([]);
  app.view.searchPage();
  assert.match(app.element('#main').innerHTML,/id="evidence-toolbar"/);
  const results=[{id:'z1',kind:'paper_text',title:'Zed',text:'zed evidence',page_start:1,page_end:1}];
  app.view.renderResults({results});
  assert.match(app.element('#results').innerHTML,/type="checkbox" data-evidence-check="z1"/);
  assert.equal(app.element('#evidence-toolbar').hidden,false);
  assert.equal(app.element('#evidence-copy').disabled,true);
  assert.equal(app.element('#evidence-clear').disabled,true);
  assert.match(app.element('#evidence-count').textContent,/已选 0 条证据/);
  app.view.toggleEvidence(results[0],true);
  assert.equal(app.element('#evidence-copy').disabled,false);
  assert.equal(app.element('#evidence-clear').disabled,false);
  assert.match(app.element('#evidence-count').textContent,/已选 1 条证据/);
  app.view.clearEvidenceSelection();
  assert.equal(app.element('#evidence-copy').disabled,true);
  assert.match(app.element('#evidence-count').textContent,/已选 0 条证据/);
  app.view.renderResults({results:[]});
  assert.equal(app.element('#evidence-toolbar').hidden,true);
});

test('a successful new search display clears the previous selection',()=>{
  const app=setup([]);
  app.view.searchPage();
  app.view.renderResults({results:[{id:'old1',kind:'paper_text',title:'Old',text:'old text',page_start:1,page_end:1}]});
  app.view.toggleEvidence({id:'old1'},true);
  assert.match(app.element('#evidence-count').textContent,/已选 1 条证据/);
  app.view.renderResults({results:[{id:'new1',kind:'paper_text',title:'New',text:'new text',page_start:2,page_end:2}]});
  assert.equal(app.view.evidenceSelection.size,0);
  assert.match(app.element('#evidence-count').textContent,/已选 0 条证据/);
  assert.equal(app.element('#evidence-copy').disabled,true);
  assert.match(app.element('#results').innerHTML,/data-evidence-check="new1"/);
  assert.doesNotMatch(app.element('#results').innerHTML,/data-evidence-check="old1"/);
});

test('a successful clipboard write confirms with visible feedback and keeps the dialog closed',async()=>{
  const app=setup([]),written=[];
  app.context.navigator={clipboard:{writeText:async text=>{written.push(text);}}};
  const results=[{id:'ok1',kind:'paper_text',title:'Ok Paper',text:'Ok evidence',page_start:1,page_end:1}];
  app.view.renderResults({results});
  app.view.toggleEvidence(results[0],true);
  await vm.runInContext('copyEvidenceMarkdown()',app.context);
  assert.equal(written.length,1);
  assert.match(written[0],/- \*\*Ok Paper\*\* · 论文原文 · p\.1/);
  assert.match(app.element('#toast').textContent,/已复制 1 条证据/);
  assert.equal(app.element('#evidence-dialog').open,false);
});

test('clipboard refusal keeps the selection and shows the same text for manual copy',async()=>{
  const app=setup([]),attempted=[];
  app.context.navigator={clipboard:{writeText:async text=>{attempted.push(text);throw Error('denied');}}};
  const results=[{id:'d1',kind:'paper_text',title:'Dialog Paper',text:'Dialog evidence',page_start:2,page_end:2,url:'/api/file?paper=D#page=2'}];
  app.view.renderResults({results});
  app.view.toggleEvidence(results[0],true);
  await vm.runInContext('copyEvidenceMarkdown()',app.context);
  assert.equal(app.view.evidenceSelection.size,1);
  assert.match(app.element('#toast').textContent,/手工复制/);
  assert.equal(app.element('#evidence-dialog').open,true);
  assert.equal(app.element('#evidence-copy-text').value,attempted[0]);
  assert.match(attempted[0],/Dialog Paper/);assert.match(attempted[0],/> Dialog evidence/);
});

test('the hidden-textarea execCommand fallback copies without opening the manual dialog',async()=>{
  const app=setup([]),fields=[],commands=[];
  app.context.document.createElement=()=>{const field={value:'',setAttribute(){},style:{},select(){},remove(){field.removed=true;}};fields.push(field);return field;};
  app.context.document.execCommand=command=>{commands.push(command);return true;};
  const results=[{id:'m1',kind:'paper_text',title:'Exec Paper',text:'Exec evidence',page_start:1,page_end:1}];
  app.view.renderResults({results});
  app.view.toggleEvidence(results[0],true);
  await vm.runInContext('copyEvidenceMarkdown()',app.context);
  assert.deepEqual(commands,['copy']);
  assert.equal(fields.length,1);
  assert.equal(fields[0].removed,true);
  assert.match(fields[0].value,/- \*\*Exec Paper\*\* · 论文原文 · p\.1/);
  assert.match(fields[0].value,/> Exec evidence/);
  assert.match(app.element('#toast').textContent,/已复制 1 条证据/);
  assert.equal(app.element('#evidence-dialog').open,false);
  assert.equal(app.element('#dialog-content').innerHTML,'');
  assert.equal(app.view.evidenceSelection.size,1);
});
