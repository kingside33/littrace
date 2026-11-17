// 使用正式模块清单；测试别名只在 VM 内安装，不进入产品运行时。
const fs=require('node:fs'),path=require('node:path');
const root=path.join(__dirname,'../kbapp/web/assets');
const manifest=JSON.parse(fs.readFileSync(path.join(root,'manifest.json'),'utf8'));
function source(){
  const modules=manifest.scripts.filter(n=>n!=='boot.js').map(n=>fs.readFileSync(path.join(root,n),'utf8')).join('\n');
  const aliases=Object.entries(manifest.exports).flatMap(([module,names])=>names.map(name=>`Object.defineProperty(globalThis,${JSON.stringify(name)},{configurable:true,get(){return KB.context.${module}.${name};},set(value){${name==='state'?`Object.assign(KB.context.${module}.${name},value);`:`KB.context.${module}.${name}=value;`}}});`)).join('\n');
  const injection=Object.entries(manifest.exports).flatMap(([module,names])=>names.map(name=>`if(Object.hasOwn(globalThis,${JSON.stringify(name)})){${false?``:`Object.defineProperty(KB.context.${module},${JSON.stringify(name)},{configurable:true,writable:true,value:globalThis[${JSON.stringify(name)}]});`}}`)).join('\n');
  return 'globalThis.window=Object.assign({addEventListener(){}},globalThis.window||{});\n'+modules+'\nKB.start({autoStart:false});\n'+injection+'\n'+aliases;
}
module.exports={source};
