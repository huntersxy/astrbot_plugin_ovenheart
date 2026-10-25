import {readFileSync} from 'node:fs';
import {runInContext,createContext} from 'node:vm';
import assert from 'node:assert/strict';
import test from 'node:test';

const source=readFileSync(new URL('../views/workshop/app.js',import.meta.url),'utf8');
function fixture() {
  const nodes=new Map(),timers=new Map(),events=new Map();
  const element=key=>{
    if(!nodes.has(key)) nodes.set(key,{innerHTML:'',textContent:'',value:'',hidden:false,disabled:false,isConnected:true,dataset:{},addEventListener(){},setAttribute(){},removeAttribute(){},insertAdjacentHTML(_p,text){this.innerHTML+=text;}});
    return nodes.get(key);
  };
  const nav=['profile','library','history','settings','prompts'].map(key=>Object.assign(element(`nav-${key}`),{dataset:{tab:key}}));
  let counter=0;
  const context=createContext({document:{hidden:false,documentElement:{dataset:{}},querySelector:element,querySelectorAll:key=>key==='[data-tab]'?nav:[],addEventListener:(name,fn)=>events.set(name,fn)},window:{addEventListener(){}},console,
    setTimeout:(fn,ms)=>{const id=++counter;timers.set(id,{fn,ms});return id;},clearTimeout:id=>timers.delete(id),FormData:class{getAll(){return [];}}});
  runInContext(source,context);
  runInContext(`const scope={id:'a'.repeat(24),label:'测试群',persona:'默认',pending:3,review_count:0,profile:{identity:'',reply_style:'',behavior:'',learning_enabled:true,apply_enabled:true}};
    data={scope,scopes:[scope],shared_scope:{id:'0'.repeat(24)},effect_scope:'0'.repeat(24),traits:[],runs:[],revisions:[],applications:[],decisions:[],turn_traces:[],busy:[],memory:{},settings:{enabled:true,learning_enabled:true,min_evidence:2,blocked_senders:[],target_senders:[],allowed_sessions:[],autonomous_tools:[]}};
    selectedScope=scope.id;bridge={apiGet:async()=>data,apiPost:async()=>({message:'保存完成'})};dirty=false;render();`,context);
  return {context,element,nav,timers,events,run:code=>runInContext(code,context)};
}

test('initial page selects its scope in one lightweight state request',async()=>{
  const f=fixture();
  f.run(`selectedScope='';globalThis.calls=[];bridge.apiGet=async(endpoint,query)=>(calls.push({endpoint,query}),data);`);
  await f.run('load()');
  const calls=JSON.parse(f.run('JSON.stringify(calls)'));
  assert.equal(calls.length,1);
  assert.equal(calls[0].query.view,'profile');
  assert.equal(f.run('selectedScope'),'a'.repeat(24));
});

test('library detail starts in parallel and duplicate reads are coalesced',async()=>{
  const f=fixture();
  f.run(`tab='library';globalThis.calls=[];bridge.apiGet=(endpoint)=>{calls.push(endpoint);return new Promise(resolve=>{if(endpoint==='state')globalThis.stateDone=()=>resolve(data);else globalThis.libraryDone=()=>resolve({traits:[],total:0,offset:0});});};`);
  const first=f.run('load()'),second=f.run('load()');
  assert.deepEqual(JSON.parse(f.run('JSON.stringify(calls)')),['library','state']);
  f.run('stateDone();libraryDone();');
  await Promise.all([first,second]);
  assert.equal(f.run('pending'),false);
});

test('slow reads never disable navigation and stale response cannot replace the new page',async()=>{
  const f=fixture();
  f.run(`bridge.apiGet=(endpoint,query)=>new Promise(resolve=>{if(query.view==='history')globalThis.historyDone=()=>resolve({...data,runs:[{started:1,status:'error',message:'STALE'}]});else resolve(data);});`);
  const slow=f.nav.find(n=>n.dataset.tab==='history').onclick();
  assert.equal(f.run('tab'),'history');
  assert.ok(f.nav.every(n=>!n.disabled));
  await f.nav.find(n=>n.dataset.tab==='settings').onclick();
  const html=f.element('#content').innerHTML;
  assert.ok(html.includes('settings-form'));
  f.run('historyDone();');await slow;
  assert.equal(f.run('tab'),'settings');
  assert.equal(f.element('#content').innerHTML,html);
});

test('mutation has one refresh and preserves a new draft after navigation',async()=>{
  const f=fixture();
  f.run(`globalThis.reads=0;bridge.apiGet=async()=>{reads++;return data;};`);
  await f.run('mutate("settings",{})');
  assert.equal(f.run('reads'),1);
  f.run(`bridge.apiPost=()=>new Promise(resolve=>globalThis.writeDone=resolve);`);
  const saving=f.run('mutate("profile",{})');
  await f.nav.find(n=>n.dataset.tab==='settings').onclick();
  f.run(`dirty=true;editVersion++;`);
  const html=f.element('#content').innerHTML;
  f.run('writeDone({message:"保存完成"})');await saving;
  assert.equal(f.run('dirty'),true);
  assert.equal(f.element('#content').innerHTML,html);
});

test('cached tab switches are immediate without another read',async()=>{
  const f=fixture();
  f.run(`globalThis.reads=0;bridge.apiGet=async()=>{reads++;return data;};`);
  await f.run('load()');
  await f.nav.find(n=>n.dataset.tab==='settings').onclick();
  const count=f.run('reads');
  await f.nav.find(n=>n.dataset.tab==='profile').onclick();
  assert.equal(f.run('reads'),count);
  assert.ok(f.element('#content').innerHTML.includes('profile-form'));
});

test('read timeout releases UI and does not retry an unresolved bridge request',async()=>{
  const f=fixture();
  f.run(`globalThis.reads=0;bridge.apiGet=()=>{reads++;return new Promise(resolve=>globalThis.readDone=resolve);};`);
  const slow=f.run('load()');
  const timeout=[...f.timers.values()].find(t=>t.ms===15000);
  timeout.fn();
  await assert.rejects(slow,/超时/);
  assert.equal(f.run('pending'),false);
  const retry=f.run('load()');
  assert.equal(f.run('reads'),1);
  f.run('readDone(data)');await retry;
});

test('polling backs off when idle, pauses hidden tabs, and never overlaps',async()=>{
  const f=fixture();
  f.run('schedulePoll()');
  assert.equal([...f.timers.values()][0].ms,30000);
  f.context.document.hidden=true;f.events.get('visibilitychange')();
  assert.equal(f.timers.size,0);
  f.context.document.hidden=false;f.events.get('visibilitychange')();
  const immediate=[...f.timers.values()][0];assert.equal(immediate.ms,0);
  f.run(`globalThis.reads=0;bridge.apiGet=()=>{reads++;return new Promise(resolve=>globalThis.pollDone=resolve);};`);
  const work=immediate.fn();
  assert.equal(f.run('polling'),true);
  assert.equal(f.run('reads'),1);
  f.run('pollDone(data)');await work;
  assert.equal(f.run('polling'),false);
  assert.ok([...f.timers.values()].some(t=>t.ms===30000));
});


test('successful save releases editing before its refresh finishes',async()=>{
 const f=fixture();
 f.run(`bridge.apiGet=()=>new Promise(resolve=>globalThis.refreshDone=resolve);`);
 await f.run('mutate("profile",{})');
 assert.equal(f.run('pending'),false);
 f.run('dirty=true;editVersion++;refreshDone(data);');
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(f.run('dirty'),true);
});

test('explicit refresh recovers a timed-out read without an automatic retry loop',async()=>{
 const f=fixture();
 f.run(`globalThis.reads=0;bridge.apiGet=()=>{reads++;return new Promise(resolve=>{if(reads===1)globalThis.oldDone=resolve;else globalThis.newDone=resolve;});};`);
 const old=f.run('load()');
 [...f.timers.values()].find(t=>t.ms===15000).fn();await assert.rejects(old,/超时/);
 const refreshed=f.element('#refresh').onclick();
 assert.equal(f.run('reads'),2);
 f.run('newDone(data);');await refreshed;
 f.run('oldDone(data);');
});

test('save in another view does not abandon a pending navigation',async()=>{
 const f=fixture();
 f.run(`bridge.apiPost=()=>new Promise(resolve=>globalThis.savedDone=resolve);globalThis.finishes=[];bridge.apiGet=()=>new Promise(resolve=>finishes.push(resolve));`);
 const saving=f.run('mutate("profile",{})');
 const navigation=f.nav.find(n=>n.dataset.tab==='history').onclick();
 f.run('savedDone({message:"已保存"});');await saving;
 assert.equal(f.run('finishes.length'),2);
 f.run('finishes.forEach(resolve=>resolve(data));');await navigation;
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(f.run('tab'),'history');
 assert.ok(f.element('#content').innerHTML.includes('修改前的版本'));
});

test('settings renders before the slow tool inventory and keeps edits during lazy load',async()=>{
 const f=fixture();
 f.run(`delete data.tools;toolsCache=undefined;globalThis.toolReads=0;bridge.apiGet=(endpoint)=>{if(endpoint==='tools'){toolReads++;return new Promise(resolve=>globalThis.toolsDone=resolve);}return Promise.resolve(data);};`);
 await f.nav.find(n=>n.dataset.tab==='settings').onclick();
 assert.ok(f.element('#content').innerHTML.includes('settings-form'));
 assert.equal(f.run('pending'),false);
 assert.equal(f.run('toolReads'),1);
 f.element('#tool-extra').value='my_custom_tool';f.element('#tool-add-names').onclick();
 f.run(`toolsDone({items:[{name:'registered_tool',description:'注册工具',active:true}]});`);
 await new Promise(resolve=>setImmediate(resolve));
 assert.ok(f.element('#tool-choices').innerHTML.includes('my_custom_tool'));
 assert.ok(f.element('#tool-choices').innerHTML.includes('registered_tool'));
 assert.equal(f.run('dirty'),true);
});
