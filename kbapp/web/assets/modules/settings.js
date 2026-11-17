/* settings：通过 ctx 访问其他模块；变量保留在本模块闭包内。 */
KB.define('settings',($kb)=>{'use strict';
async function settingsPage(){
  const cfg=await $kb.core.api('/api/settings');
  if($kb.core.state.page!=='settings')return;
  const field=(key,label,type='text',help='',placeholder='')=>`<div class="field"><label for="setting-${key}">${label}</label><input id="setting-${key}" name="${key}" type="${type}" value="${type==='password'?'':$kb.core.esc(cfg[key])}" placeholder="${$kb.core.esc(placeholder||((type==='password'&&cfg[key])?'已保存；留空保持原密钥':''))}" ${type==='number'?'min="0" step="any"':''} ${type==='password'?'autocomplete="new-password"':''}><small>${help}</small></div>`;
  $kb.core.$('#main').innerHTML=$kb.core.intro('LOCAL CONFIGURATION','模型、费用与更新边界','GLM-OCR识别原文，本地模型核验方法关系，DeepSeek再独立复核。三个阶段分时使用显存；云端费用可限额或暂不设上限。')+`<form id="settings-form"><section class="settings-section"><div class="eyebrow">CLOUD ANALYSIS</div><h2>云端独立复核与问答</h2><div class="form-grid">${field('api_base','兼容 API 地址','url','填写服务的 /v1 地址，或完整 /chat/completions 地址','https://api.example.com/v1')}${field('cloud_proxy','首选本地 HTTP 代理（可选）','url','代理建连失败且模型请求尚未发送时使用本机原有网络；留空始终使用原网络','http://127.0.0.1:7897')}${field('model','模型名称')}${field('api_key','API 密钥','password','本机后台使用 Windows 用户范围保护保存')}${field('daily_budget','每日费用上限（人民币）','number','限额不足时保留任务，次日续跑')}${field('input_price','输入价格（元 / 百万 token）','number')}${field('output_price','输出价格（元 / 百万 token）','number')}<div class="field wide"><label><input id="cloud-enabled" type="checkbox" ${cfg.cloud_enabled?'checked':''}> 配置完成后启用云端调用</label><small>模型、密钥、输入与输出价格及每日限额均填写后才能启用。</small></div></div></section><section class="settings-section"><div class="eyebrow">CITATION NETWORK</div><h2>库外元数据与引用补全</h2><div class="form-grid">${field('openalex_key','OpenAlex API key','password','与模型 API 分别配置；分页位置和剩余额度由后台维护')}${field('external_requests_per_run','每轮外部请求上限','number','上下游各两层，未完成部分下次续跑')}${field('cloud_requests_per_run','每轮核实论文数','number','本地核验后进行云端独立复核；限额模式同时受每日费用预算约束。')}${field('zotero_dir','Zotero 数据目录','text','只读快照读取，包含已提交的 WAL 数据')}</div><div class="callout">库外先保存元数据与摘要。关键全文在“全文审批”中批准后获取。</div></section><section class="settings-section"><div class="eyebrow">LOCAL MODELS</div><h2>本地索引与识别</h2><p class="small-text muted">Qwen3-Embedding 8B · 1,024 维 · BM25 + RRF<br>GLM-OCR · 文本 / 公式 / 表格识别<br>调度目标：北京时间 09:00；自动启动的实际安装状态见“索引与更新”。</p><div class="form-grid"><div class="field"><label for="setting-relation_extractor">关系核验方式</label><select id="setting-relation_extractor"><option value="local">GLM-OCR识别 → 本地核验 → 云端复核</option><option value="cloud">已有原文 → 云端分析及复核</option></select><small>GLM-OCR只识别文字、公式和表格；本地语言模型只做方法关系核验。</small></div>${field('local_text_model','本地关系核验模型','text','向量完成后单独加载；不用于生成向量')}${field('local_text_context','本地核验上下文上限','number','先从 4096 开始，按识别后的证据片段核验，禁止静默截断')}${field('ocr_pages_per_run','索引更新每轮页面 OCR 上限','number')}${field('ocr_regions_per_run','索引更新每轮图表 / 公式 OCR 上限','number')}</div></section><button class="button primary" type="submit">保存设置</button><span id="settings-state" class="small-text muted" style="margin-left:16px"></span></form>`;
  $kb.core.$('#setting-daily_budget').closest('.field').insertAdjacentHTML('beforebegin',`<div class="field"><label for="setting-budget-mode">费用模式</label><select id="setting-budget-mode"><option value="limited">按每日人民币限额</option><option value="unlimited">暂不设费用上限</option></select><small>不限额模式记录 token；价格未填写时显示费用未计价。</small></div>`);
  $kb.core.$('#setting-budget-mode').value=cfg.budget_mode||'limited';$kb.core.$('#setting-relation_extractor').value=cfg.relation_extractor||'cloud';
  const applyBudget=()=>{$kb.core.$('#setting-daily_budget').disabled=$kb.core.$('#setting-budget-mode').value==='unlimited';$kb.core.$('#cloud-enabled').closest('.field').querySelector('small').textContent=$kb.core.$('#setting-budget-mode').value==='unlimited'?'当前选择暂不设费用上限；启用后可调用模型，仍保留请求与 token 记录。':'启用前需填写模型、密钥、输入/输出价格及每日限额。';};
  $kb.core.$('#setting-budget-mode').onchange=applyBudget;applyBudget();
  $kb.core.$('#settings-form').insertAdjacentHTML('afterbegin',`<div class="callout" id="settings-summary">当前 API：${$kb.core.esc(cfg.api_base||'未配置')}<br>网络：${$kb.core.esc(cfg.cloud_proxy||'系统网络')}<br>模型：${$kb.core.esc(cfg.model||'未配置')} · 密钥${cfg.api_key?'已保存':'未保存'} · 云端${cfg.cloud_enabled?'已启用':'未启用'}${cfg.connection?.available?' · 模型连接已验证':''}</div>`);
  $kb.core.$('#settings-form').onsubmit=async e=>{e.preventDefault();const data={cloud_enabled:$kb.core.$('#cloud-enabled').checked,budget_mode:$kb.core.$('#setting-budget-mode').value,relation_extractor:$kb.core.$('#setting-relation_extractor').value};let skipped=0;
    for(const input of $kb.core.$$('#settings-form input[name]')){
      if(input.type==='password'){data[input.name]=input.value||null;continue;}
      if(input.type==='number'){
        if(input.value.trim()===''){skipped++;continue;}
        data[input.name]=Number(input.value);continue;
      }
      data[input.name]=input.value;
    }
    try{
      await $kb.core.api('/api/settings',data);
      $kb.core.$('#settings-state').textContent='设置已保存，密钥不会回显。';
      $kb.core.$$('input[type=password]').forEach(i=>{i.value='';i.placeholder='已保存；留空保持原密钥';});
      try{
        const fresh=await $kb.core.api('/api/settings'),sum=$kb.core.$('#settings-summary');
        if(sum)sum.innerHTML=`当前 API：${$kb.core.esc(fresh.api_base||'未配置')}<br>网络：${$kb.core.esc(fresh.cloud_proxy||'系统网络')}<br>模型：${$kb.core.esc(fresh.model||'未配置')} · 密钥${fresh.api_key?'已保存':'未保存'} · 云端${fresh.cloud_enabled?'已启用':'未启用'}${fresh.connection?.available?' · 模型连接已验证':''}`;
      }catch(err){}
      $kb.status.refreshStatus();
      $kb.core.toast(skipped?'设置已保存，留空的数字项保持原值':'设置已保存');
    }catch(err){$kb.feedback.showError(err.message,{title:'设置未能保存'});}};
}
return {get settingsPage(){return settingsPage;},set settingsPage(value){settingsPage=value;}};
});
