// 设置页「上游追溯层数」控件：渲染、初始值映射与提交载荷映射。
// 经 web-source.cjs 在 VM 里驱动真实 settings.js，读写都锚定 GET/POST /api/settings。
const test=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const source=require('./web-source.cjs').source();

function makeEl(key,record){
  return {key,innerHTML:'',textContent:'',value:'',checked:false,disabled:false,type:'text',name:'',placeholder:'',style:{},onchange:null,onsubmit:null,
    closest(){return makeEl(key+'@field',record);},
    querySelector(sel){return makeEl(key+'>'+sel,record);},
    insertAdjacentHTML(where,html){record.inserts.push({key,where,html});}};
}

function setup(cfg,inputs){
  const record={inserts:[]};
  const els=new Map();
  const el=sel=>{const key='$'+sel;if(!els.has(key))els.set(key,makeEl(key,record));return els.get(key);};
  const lists={
    '#settings-form input[name]':inputs,
    '#settings-form input[type=password]':inputs.filter(i=>i.type==='password'),
    'input[type=password]':inputs.filter(i=>i.type==='password'),
  };
  const seen={posts:[],gets:[],errors:[],toasts:[],current:{...cfg}};
  const context={
    // core 的 $/$$ 是闭包函数，导出对象上只有 getter 没有 setter——覆盖要落在
    // 它们的唯一实现路径 document.querySelector/querySelectorAll 上。
    document:{
      readyState:'complete',
      addEventListener(){},
      querySelector:sel=>el(sel),
      querySelectorAll:sel=>lists[sel]||[],
    },
    console,
    seen,
    domFactory:sel=>el(sel),
    domList:sel=>lists[sel]||[],
    domRecord:record,
  };
  vm.createContext(context);
  vm.runInContext(source+`
    api=async(path,body)=>{
      if(body){seen.posts.push(JSON.parse(JSON.stringify(body)));seen.current=Object.assign({},seen.current,body);return Object.assign({},seen.current);}
      seen.gets.push(path);return Object.assign({},seen.current);
    };
    showError=(message,options)=>seen.errors.push({message,options});
    toast=message=>seen.toasts.push(message);
    refreshStatus=async()=>{};
  `,context);
  const page=async()=>{
    context.KB.context.core.state.page='settings';
    await context.KB.context.settings.settingsPage();
  };
  return {
    ...seen,record,els,el,page,
    html:()=>el('#main').innerHTML,
    set:(sel,value)=>{el(sel).value=value;},
    submit:async()=>{await el('#settings-form').onsubmit({preventDefault(){}});},
  };
}

const formInputs=[
  {type:'password',name:'api_key',value:''},
  {type:'number',name:'external_requests_per_run',value:'100'},
];

test('depth control renders in the citation section with five options and the A/B/C example',async()=>{
  const app=setup({},formInputs);
  await app.page();
  const html=app.html();
  assert.match(html,/上游追溯层数/);
  for(const option of ['<option value="1">1 层</option>','<option value="2">2 层</option>','<option value="3">3 层</option>','<option value="custom">自定义正整数</option>','<option value="unlimited">不限层数</option>'])assert.ok(html.includes(option),option);
  assert.match(html,/A 是种子，A 引用的 B 是第 1 层，B 引用的 C 是第 2 层/);
  assert.match(html,/每轮外部请求预算仍然生效/);
  // 帮助文案要同时交代该预算的两类消费者：上游追溯按层数取数；普通更新
  // 固定上下游各两层（network.py 的 depth<2 硬编码未动），也在同一预算内。
  assert.match(html,/上游追溯按层数取数/);
  assert.match(html,/普通更新固定上下游各两层/);
  assert.match(html,/未完成部分下次续跑/);
});

test('default and saved values map onto the selector; stored non-preset depth shows the custom input',async()=>{
  // 新装服务 GET /api/settings 总会带上 DEFAULTS 的 network_depth=2。
  const empty=setup({network_depth:2,network_depth_unlimited:false},formInputs);await empty.page();
  assert.equal(empty.el('#setting-network-depth-mode').value,'2');
  assert.equal(String(empty.el('#setting-network-depth').value),'2');
  assert.equal(empty.el('#setting-network-depth').style.display,'none');

  const unlimited=setup({network_depth:2,network_depth_unlimited:true},formInputs);await unlimited.page();
  assert.equal(unlimited.el('#setting-network-depth-mode').value,'unlimited');

  const preset=setup({network_depth:3,network_depth_unlimited:false},formInputs);await preset.page();
  assert.equal(preset.el('#setting-network-depth-mode').value,'3');

  const custom=setup({network_depth:7,network_depth_unlimited:false},formInputs);await custom.page();
  assert.equal(custom.el('#setting-network-depth-mode').value,'custom');
  assert.equal(String(custom.el('#setting-network-depth').value),'7');
  assert.equal(custom.el('#setting-network-depth').style.display,'');
});

test('unlimited submits only the flag and leaves the depth carrier untouched',async()=>{
  const app=setup({network_depth:2,network_depth_unlimited:false},formInputs);
  await app.page();
  app.set('#setting-network-depth-mode','unlimited');
  await app.submit();
  assert.equal(app.posts.length,1);
  assert.equal(app.posts[0].network_depth_unlimited,true);
  assert.ok(!('network_depth' in app.posts[0]),'depth carrier must not be rewritten');
  assert.equal(app.posts[0].api_key,null);
  assert.equal(app.posts[0].external_requests_per_run,100);
  assert.equal(app.gets.filter(p=>p==='/api/settings').length,2,'save then refresh');
});

test('preset depths submit the integer and unlimited=false',async()=>{
  for(const [mode,expected] of [['1',1],['2',2],['3',3]]){
    const app=setup({},formInputs);await app.page();
    app.set('#setting-network-depth-mode',mode);
    await app.submit();
    assert.deepEqual({depth:app.posts[0].network_depth,unlimited:app.posts[0].network_depth_unlimited},{depth:expected,unlimited:false},mode);
  }
});

test('custom depth submits the typed positive integer',async()=>{
  const app=setup({},formInputs);await app.page();
  app.set('#setting-network-depth-mode','custom');
  app.set('#setting-network-depth','4');
  await app.submit();
  assert.equal(app.posts[0].network_depth,4);
  assert.equal(app.posts[0].network_depth_unlimited,false);
});

test('invalid custom depth shows an error and posts nothing',async()=>{
  for(const raw of ['','0','-3','2.5','abc']){
    const app=setup({},formInputs);await app.page();
    app.set('#setting-network-depth-mode','custom');
    app.set('#setting-network-depth',raw);
    await app.submit();
    assert.deepEqual({posts:app.posts.length,errors:app.errors.length},{posts:0,errors:1},raw);
    assert.match(app.errors[0].message,/正整数/);
    assert.match(app.errors[0].message,/不限层数/);
  }
});

test('the summary tells the truth when a stored key cannot be read',async()=>{
  // 存量密文读不出时 public 视图带 secrets_unavailable。此时「密钥已保存 ·
  // 云端已启用」会是一盏骗人的绿灯：每次云端调用都会在 _complete 失败。
  // 摘要经 insertAdjacentHTML 注入，落在 setup 的 record.inserts 里。
  const summaryHtml=app=>app.record.inserts.map(i=>i.html).join('\n');

  const unreadable=setup({api_key:true,cloud_enabled:true,secrets_unavailable:{api_key:'unreadable'}},formInputs);
  await unreadable.page();
  const html=summaryHtml(unreadable);
  assert.match(html,/已保存但本机无法读取/);
  assert.match(html,/密钥无法读取，云端不可用/);
  assert.ok(!/密钥已保存 · 云端已启用/.test(html),'must not paint the green enabled light');

  // 可读的密钥照常显示「已保存 · 已启用」，不能被这条改动误伤。
  const healthy=setup({api_key:true,cloud_enabled:true},formInputs);
  await healthy.page();
  const ok=summaryHtml(healthy);
  assert.match(ok,/密钥已保存 · 云端已启用/);
  assert.ok(!/无法读取/.test(ok));

  // 没存密钥仍是「未保存 · 未启用」，第三种状态不被吞并。
  const absent=setup({api_key:false,cloud_enabled:false},formInputs);
  await absent.page();
  assert.match(summaryHtml(absent),/密钥未保存 · 云端未启用/);
});
