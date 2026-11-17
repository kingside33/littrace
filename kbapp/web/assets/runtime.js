/* 唯一命名空间；每个模块通过 ctx 显式访问共享能力。 */
globalThis.KB=(()=>{'use strict';const factories=new Map();let context=null;
return {define(name,factory){if(factories.has(name))throw Error('重复模块：'+name);factories.set(name,factory);},
start({autoStart=true}={}){if(context)return context;context={};for(const[name,factory]of factories)context[name]=factory(context);if(autoStart){if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',context.shell.init);else context.shell.init();}return context;},get context(){return context;}};})();
