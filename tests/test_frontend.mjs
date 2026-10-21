import {readFileSync} from 'node:fs';
import {runInContext, createContext} from 'node:vm';
import assert from 'node:assert/strict';
import test from 'node:test';

const source = readFileSync(new URL('../views/workshop/app.js', import.meta.url), 'utf8');

function fixture() {
  const nodes = new Map();
  let confirmations = [];
  const element = selector => {
    if (!nodes.has(selector)) nodes.set(selector, {innerHTML:'', textContent:'', value:'', disabled:false, hidden:false, dataset:{}, addEventListener() {}});
    return nodes.get(selector);
  };
  const context = createContext({
    document:{querySelector:element, querySelectorAll:() => [], addEventListener() {}},
    window:{addEventListener() {}},
    setInterval() {},
    console,
  });
  const dialog = element('#confirmation');
  let onClose;
  dialog.addEventListener = (name, callback) => { if (name === 'close') onClose = callback; };
  dialog.showModal = () => { dialog.returnValue = confirmations.shift() ? 'continue' : 'cancel'; onClose(); };
  runInContext(source, context);
  runInContext(`data = {traits:[{id:1,kind:'expression',situation:'被调侃时',content:'原始习惯',confidence:0.95,status:'pending',evidence:[]}]}; renderLibrary(); dirty = true;`, context);
  runInContext(`bridge = {apiGet:async () => data, apiPost:async () => ({message:'已保存'})};`, context);
  return {context, element, confirmations};
}

for (const [selector, variable, oldValue, nextValue] of [['#kind-filter','category','all','expression'],['#status-filter','status','pending','active']]) {
  test(`cancel ${variable} filter change preserves unsaved editor and selection`, async () => {
    const {context, element, confirmations} = fixture();
    const original = element('#content').innerHTML;
    confirmations.push(false);
    const input = element(selector);
    input.value = nextValue;
    await input.onchange({target:input});
    assert.equal(input.value, oldValue);
    assert.equal(element('#content').innerHTML, original);
    assert.equal(runInContext('dirty', context), true);
    assert.equal(runInContext(variable, context), oldValue);
  });
  test(`confirm ${variable} filter change applies filter and clears discarded draft`, async () => {
    const {context, element, confirmations} = fixture();
    runInContext(`const scope={id:'a'.repeat(24),label:'测试群',persona:'默认',pending:0,review_count:1,profile:{learning_enabled:true}}; selectedScope=scope.id; tab='library'; data={...data,scope,scopes:[scope],settings:{},memory:{},busy:[]}; bridge.apiGet=async endpoint => endpoint === 'library' ? {traits:[],runs:[],total:0,offset:0} : data;`,context);
    confirmations.push(true);
    const input = element(selector);
    input.value = nextValue;
    await input.onchange({target:input});
    assert.equal(runInContext('dirty', context), false);
    assert.equal(runInContext(variable, context), nextValue);
  });
}

test('participation history escapes model text and labels observation honestly', () => {
  const {context,element}=fixture();
  runInContext(`data={settings:{participation_mode:'observe'},runs:[],revisions:[],applications:[],decisions:[{created:1,action:'reply',confidence:.9,mode:'observe',outcome:'observed',reason:'<img src=x onerror=alert(1)>',intent:'先倾听'}]}; renderHistory();`,context);
  const html=element('#content').innerHTML;
  assert.ok(html.includes('&lt;img src=x onerror=alert(1)&gt;'));
  assert.ok(!html.includes('<img src=x'));
  assert.ok(html.includes('当前仅记录判断，不主动回复。'));
  assert.ok(html.includes('仅观察 / 已记录'));
});

test('all participation controls are available with host schema defaults', () => {
  const {context,element}=fixture();
  const schema=JSON.parse(readFileSync(new URL('../_conf_schema.json',import.meta.url),'utf8'));
  const settings=Object.fromEntries(Object.entries(schema).map(([key,value])=>[key,value.default]));
  runInContext(`data={settings:${JSON.stringify(settings)},memory:{message:'未启用'},scope:null}; renderSettings();`,context);
  const html=element('#content').innerHTML;
  for (const name of ['participation_mode','participation_min_messages','participation_debounce','participation_cooldown','participation_hourly_limit','planner_provider_id','planner_timeout','planner_reply_threshold','planner_max_wait','autonomous_tools','debug_enabled','session_isolation']) {
    assert.ok(html.includes('name="'+name+'"'),name);
  }
  assert.ok(html.includes('value="off" selected'));
  assert.ok(html.includes('value="contextual" selected'));
  assert.ok(html.includes('name="rerank_provider_id"'));
  assert.ok(html.includes('name="selector_timeout"'));
  assert.ok(html.includes('name="guidance_delivery"'));
  assert.ok(html.includes('value="cached" selected'));
  assert.ok(html.includes('value="dual"'));
  assert.ok(html.includes('第三方运行器需自行支持'));
  assert.ok(html.includes('仍保留当前输入直接命中的本地词义'));
  assert.ok(html.includes('name="learning_review_enabled"'));
  assert.ok(html.includes('通常增加一次后台调用'));
});

test('learning self-check reasons are visible and escaped in history and the library', () => {
  const {context,element}=fixture();
  runInContext(`data.runs=[{started:1,status:'success',message:'完成',memory:'',review:{enabled:true,passed:0,flagged:1,candidates:[{kind:'expression',situation:'被调侃时',content:'原始习惯',approved:false,reason:'<img src=x onerror=alert(1)>'}]}}]; renderLibrary();`,context);
  let html=element('#content').innerHTML;
  assert.ok(html.includes('学习自检提示'));
  assert.ok(html.includes('人工审核可以覆盖'));
  assert.ok(html.includes('&lt;img src=x onerror=alert(1)&gt;'));
  assert.ok(!html.includes('<img src=x'));
  runInContext(`data.settings={participation_mode:'off'};data.revisions=[];data.applications=[];data.decisions=[];renderHistory();`,context);
  html=element('#content').innerHTML;
  assert.ok(html.includes('查看自检提示与候选'));
  assert.ok(html.includes('未通过的更新不改旧条目'));
  assert.ok(html.includes('&lt;img src=x onerror=alert(1)&gt;'));
  assert.ok(!html.includes('<img src=x'));
});

test('open review details survive automatic history refresh for the same run', () => {
  const {context,element}=fixture();
  context.document.querySelectorAll=selector=>selector==='details[data-review-run][open]' ? [{dataset:{reviewRun:'7'}}] : [];
  runInContext(`data={settings:{participation_mode:'off'},runs:[{id:7,started:1,status:'success',message:'完成',memory:'',review:{enabled:true,passed:0,flagged:1,candidates:[{kind:'expression',situation:'闲聊',content:'候选',approved:false,reason:'待确认'}]}}],revisions:[],applications:[],decisions:[]};renderHistory();`,context);
  assert.ok(element('#content').innerHTML.includes('data-review-run="7" open'));
  runInContext('data.runs[0].id=8;renderHistory();',context);
  assert.ok(!element('#content').innerHTML.includes('data-review-run="8" open'));
});

for (const approved of [false, true]) test(`all-conversation filter respects dirty draft confirmation ${approved}`, async () => {
  const {context,element,confirmations}=fixture();
  runInContext(`libraryScope='a'.repeat(24);renderLibrary();`,context);
  confirmations.push(approved);
  runInContext(`load=async () => {};`,context);
  const input=element('#library-scope'); input.value='all';
  await input.onchange({target:input});
  assert.equal(runInContext('libraryScope',context),approved?'all':'a'.repeat(24));
  assert.equal(runInContext('dirty',context),!approved);
});

test('all-library review uses the entry owner rather than the header conversation',async () => {
  const {context,element}=fixture();
  const reviews=[{dataset:{review:'active'}}], feedback=[{dataset:{feedback:'-1'}}];
  context.document.querySelectorAll=selector => selector==='[data-review]'?reviews:selector==='[data-feedback]'?feedback:[];
  runInContext(`selectedScope='a'.repeat(24);data.traits[0].scope='b'.repeat(24);data.traits[0].label='<群>';data.traits[0].persona='烘焙';data.library={traits:data.traits,runs:[],total:1,offset:0};globalThis.sent=[];mutate=async(...args)=>sent.push(args);renderLibrary();`,context);
  element('#field-situation').value='编辑情境';element('#field-content').value='编辑内容';
  reviews[0].onclick();
  runInContext('dirty=false',context);await feedback[0].onclick();
  const form={situation:{value:'编辑情境'},content:{value:'编辑内容'}};
  await element('#trait-form').onsubmit({preventDefault(){},currentTarget:form});
  assert.equal(runInContext('sent.length',context),3);
  assert.ok(runInContext("sent.every(args=>args[2]==='b'.repeat(24))",context));
  assert.ok(element('#content').innerHTML.includes('&lt;群&gt; / 烘焙'));
});

test('QQ blacklist accepts pasted numbers without platform prefixes',() => {
  const {context,element}=fixture();
  runInContext(`data={settings:{blocked_senders:[],target_senders:[],allowed_sessions:[],autonomous_tools:[]},memory:{}};renderSettings();globalThis.saved=null;mutate=(action,payload)=>saved=payload;`,context);
  const input={value:'123456, 234567，345678\n456789；567890'};
  element('#settings-form').onsubmit({preventDefault(){},currentTarget:{elements:{namedItem:key=>key==='blocked_senders'?input:null}}});
  assert.equal(runInContext('saved.blocked_senders.join(",")',context),'123456,234567,345678,456789,567890');
});

test('legacy common jargon is retained without a global approval action', () => {
  const {context,element}=fixture();
  runInContext(`data.shared_scope={id:'0'.repeat(24)};data.traits[0].kind='jargon';data.traits[0].scope='0'.repeat(24);renderLibrary();`,context);
  const html=element('#content').innerHTML;
  assert.ok(html.includes('仅保留'));
  assert.ok(html.includes('不参与回复'));
  assert.ok(!html.includes('data-review="active"'));
  runInContext(`data.traits[0].scope='a'.repeat(24);renderLibrary();`,context);
  assert.ok(element('#content').innerHTML.includes('data-review="active"'));
  assert.ok(element('#content').innerHTML.includes('不受「启用会话隔离」开关影响'));
});

for (const editing of [false,true]) test(`settings polling refreshes changed configuration while preserving dirty draft ${editing}`, async () => {
  const {context,element}=fixture();
  runInContext(`tab='settings';dirty=${editing};data={settings:{debug_enabled:false,blocked_senders:[],target_senders:[],allowed_sessions:[],autonomous_tools:[]},settings_revision:'old',scopes:[],scope:null,memory:{},busy:[]};renderSettings();bridge.apiGet=async()=>({...data,settings:{...data.settings,debug_enabled:true},settings_revision:'new'});`,context);
  const before=element('#content').innerHTML;
  await runInContext('load(false)',context);
  if (editing) assert.equal(element('#content').innerHTML,before);
  else assert.ok(element('#content').innerHTML.includes('name="debug_enabled" checked'));
});

test('settings submission retains the revision from the rendered form', () => {
  const {context,element}=fixture();
  runInContext(`data={settings:{blocked_senders:[],target_senders:[],allowed_sessions:[],autonomous_tools:[]},settings_revision:'rendered',memory:{}};renderSettings();data.settings_revision='newer';globalThis.savedRevision=null;mutate=(action,payload,scope,revision)=>savedRevision=revision;`,context);
  element('#settings-form').onsubmit({preventDefault(){},currentTarget:{elements:{namedItem:()=>null}}});
  assert.equal(runInContext('savedRevision',context),'rendered');
});
