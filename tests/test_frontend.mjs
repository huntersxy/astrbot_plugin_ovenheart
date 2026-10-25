import {readFileSync} from 'node:fs';
import {runInContext, createContext} from 'node:vm';
import assert from 'node:assert/strict';
import test from 'node:test';

const source = readFileSync(new URL('../views/workshop/app.js', import.meta.url), 'utf8');

test('mention switch and aliases save independently of participation mode', () => {
  const {context,element}=fixture();
  runInContext(`data={settings:{mentioned_bot_reply:false,mention_names:[],participation_mode:'off',blocked_senders:[],target_senders:[],allowed_sessions:[],autonomous_tools:[]},memory:{}};renderSettings();globalThis.saved=null;mutate=(action,payload)=>saved=payload;`,context);
  assert.ok(element('#content').innerHTML.includes('提及时马上回复'));
  assert.ok(element('#content').innerHTML.includes('沿用宿主工具权限'));
  const inputs={mentioned_bot_reply:{checked:true},mention_names:{value:'小夏\n 夏夏 '},participation_mode:{value:'off'}};
  element('#settings-form').onsubmit({preventDefault(){},currentTarget:{elements:{namedItem:key=>inputs[key]||null}}});
  assert.equal(runInContext('saved.mentioned_bot_reply',context),true);
  assert.equal(runInContext('saved.mention_names.join(",")',context),'小夏,夏夏');
  assert.equal(runInContext('saved.participation_mode',context),'off');
});

test('followup switch renders its scope and saves a false value', () => {
  const {context,element}=fixture();
  runInContext(`data={settings:{participation_followup:true,participation_mode:'auto',blocked_senders:[],target_senders:[],allowed_sessions:[],autonomous_tools:[]},memory:{}};renderSettings();globalThis.saved=null;mutate=(action,payload)=>saved=payload;`,context);
  assert.ok(element('#content').innerHTML.includes('允许连续接话'));
  assert.ok(element('#content').innerHTML.includes('120 秒内'));
  const inputs={participation_followup:{checked:false}};
  element('#settings-form').onsubmit({preventDefault(){},currentTarget:{elements:{namedItem:key=>inputs[key]||null}}});
  assert.equal(runInContext('saved.participation_followup',context),false);
});

for (const stale of [false,true]) test(`model preview renders text safely and protects a changed tab (${stale})`, async () => {
  const {context,element}=fixture();
  runInContext(`tab='profile';selectedScope='a'.repeat(24);data={scope:{profile:{identity:'',reply_style:'',behavior:'',learning_enabled:true,apply_enabled:true}},settings:{session_isolation:true}};renderProfile();globalThis.sent=null;bridge.apiPost=(endpoint,body)=>{sent={endpoint,body};return new Promise(resolve=>globalThis.finishPreview=resolve);};`,context);
  assert.ok(element('#content').innerHTML.includes('可能产生费用'));
  element('#preview-query').value='  你好  ';
  element('#model-preview-text').textContent='原来的示例';
  const done=element('#model-preview').onclick({preventDefault(){}});
  if (stale) runInContext(`tab='library';`,context);
  runInContext(`finishPreview({reply:'<img src=x onerror=alert(1)>',seconds:1.2,selected_ids:[1],retrieval:'情境筛选'});`,context);
  await done;
  assert.equal(runInContext('sent.endpoint',context),'preview/model');
  assert.equal(runInContext('sent.body.query',context),'你好');
  assert.equal(element('#model-preview-text').textContent,stale?'原来的示例':'<img src=x onerror=alert(1)>');
  assert.equal(element('#model-preview-text').innerHTML,'');
  assert.equal(element('#model-preview').textContent,'生成示例回复');
});

function fixture() {
  const nodes = new Map();
  let confirmations = [];
  const element = selector => {
    if (!nodes.has(selector)) nodes.set(selector, {innerHTML:'', textContent:'', value:'', disabled:false, hidden:false, dataset:{}, insertAdjacentHTML(_where, html) { this.innerHTML += html; }, addEventListener() {}});
    return nodes.get(selector);
  };
  const context = createContext({
    document:{querySelector:element, querySelectorAll:() => [], addEventListener() {}},
    window:{addEventListener() {}},
    setInterval() {},
    console,
    FormData: class { getAll() { return []; } },
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
  for (const name of ['participation_mode','participation_min_messages','participation_debounce','participation_silence_seconds','participation_cooldown','participation_hourly_limit','planner_provider_id','planner_timeout','planner_reply_threshold','planner_max_wait','debug_enabled','session_isolation']) {
    assert.ok(html.includes('name="'+name+'"'),name);
  }
  assert.ok(element('#autonomous-tool-picker').innerHTML.includes('name="autonomous_tool_policy"'));
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
  assert.ok(html.includes('360 秒'));
  assert.ok(html.includes('120 秒'));
  assert.ok(html.includes('替代主模型规划'));
  assert.ok(html.includes('不再调用主模型参与规划'));
});

test('learning self-check reasons are visible and escaped in history and the library', () => {
  const {context,element}=fixture();
  runInContext(`data.runs=[{started:1,status:'success',message:'完成',memory:'',review:{enabled:true,passed:0,flagged:1,candidates:[{kind:'expression',situation:'被调侃时',content:'原始习惯',approved:false,reason:'<img src=x onerror=alert(1)>'}]}}]; renderLibrary();`,context);
  let html=element('#content').innerHTML;
  assert.ok(html.includes('自检存疑'));
  assert.ok(html.includes('不是正确率，也不能覆盖自检结论'));
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
  element('#field-situation').value='编辑情境';element('#field-content').value='编辑内容';element('#trait-form').situation={value:'编辑情境'};element('#trait-form').content={value:'编辑内容'};
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

test('learning group blacklist accepts group numbers and exact session IDs', () => {
  const {context,element}=fixture();
  runInContext(`data={settings:{blocked_senders:[],learning_blocked_groups:[],target_senders:[],allowed_sessions:[],autonomous_tools:[]},memory:{}};renderSettings();globalThis.saved=null;mutate=(action,payload)=>saved=payload;`,context);
  const input={value:'123456，234567\nbot:GroupMessage:345678'};
  element('#settings-form').onsubmit({preventDefault(){},currentTarget:{elements:{namedItem:key=>key==='learning_blocked_groups'?input:null}}});
  assert.equal(runInContext('saved.learning_blocked_groups.join(",")',context),'123456,234567,bot:GroupMessage:345678');
  assert.ok(element('#content').innerHTML.includes('学习黑名单群聊'));
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

test('prompt editor lists variables, saves one body and restores the default', async () => {
  const {context,element,confirmations}=fixture();
  runInContext(`data={settings:{blocked_senders:[],target_senders:[],allowed_sessions:[],autonomous_tools:[]},memory:{},scopes:[],scope:null,busy:[],prompts:{revision:'r1',items:[{key:'planner',title:'参与决策器',description:'判断回复、等待或旁观。',placeholders:[{name:'persona',description:'宿主人格原文'}],default:'默认正文',body:'自定义正文 {{persona}}',customized:true,unknown:['nope']},{key:'review',title:'入库自检器',description:'核对候选。',placeholders:[],default:'默认自检',body:'默认自检',customized:false,unknown:[]}]}};tab='prompts';selectedPrompt=null;globalThis.posts=[];load=async()=>{};bridge={apiGet:async()=>data,apiPost:async(endpoint,body)=>(posts.push([endpoint,body]),{status:'ok',data:{message:'已保存'}})};renderPrompts();`,context);
  const html=element('#content').innerHTML;
  assert.ok(html.includes('{{persona}}'));
  assert.ok(html.includes('未识别的引用'));
  assert.ok(html.includes('已自定义'));
  assert.ok(html.includes('出厂默认'));
  assert.ok(html.includes('恢复默认'));
  assert.ok(html.includes('自定义正文 {{persona}}'));

  element('#field-prompt-body').value='新正文 {{persona}}';
  await element('#prompt-form').onsubmit({preventDefault(){},currentTarget:{}});
  assert.deepEqual(JSON.parse(JSON.stringify(runInContext('posts',context))),[["prompts/save",{prompts:{planner:'新正文 {{persona}}'},revision:'r1'}]]);
  assert.equal(runInContext('dirty',context),false);

  confirmations.push(true);
  await element('#prompt-reset').onclick();
  assert.deepEqual(JSON.parse(JSON.stringify(runInContext('posts[1]',context))),["prompts/save",{prompts:{planner:null},revision:'r1'}]);
});

test('prompt save rejects a blank body without posting', async () => {
  const {context,element}=fixture();
  runInContext(`data={settings:{blocked_senders:[],target_senders:[],allowed_sessions:[],autonomous_tools:[]},memory:{},scopes:[],scope:null,busy:[],prompts:{revision:'r1',items:[{key:'planner',title:'参与决策器',description:'判断回复。',placeholders:[],default:'默认正文',body:'默认正文',customized:false,unknown:[]}]}};tab='prompts';selectedPrompt=null;globalThis.posts=[];load=async()=>{};bridge={apiGet:async()=>data,apiPost:async(endpoint,body)=>(posts.push([endpoint,body]),{status:'ok',data:{}})};renderPrompts();`,context);
  element('#field-prompt-body').value='   ';
  await element('#prompt-form').onsubmit({preventDefault(){},currentTarget:{}});
  assert.equal(runInContext('posts.length',context),0);
  assert.ok(element('#notice').textContent.includes('不能为空'));
});

test('settings submission retains the revision from the rendered form', () => {
  const {context,element}=fixture();
  runInContext(`data={settings:{blocked_senders:[],target_senders:[],allowed_sessions:[],autonomous_tools:[]},settings_revision:'rendered',memory:{}};renderSettings();data.settings_revision='newer';globalThis.savedRevision=null;mutate=(action,payload,scope,revision)=>savedRevision=revision;`,context);
  element('#settings-form').onsubmit({preventDefault(){},currentTarget:{elements:{namedItem:()=>null}}});
  assert.equal(runInContext('savedRevision',context),'rendered');
});

test('tool picker searches current tools and preserves both policy lists and missing names', () => {
  const {context,element}=fixture();
  runInContext(`data={settings:{blocked_senders:[],target_senders:[],allowed_sessions:[],autonomous_tools:['qq_group_info','legacy_tool'],autonomous_tool_denylist:['existing_ban'],autonomous_tool_policy:'allowlist'},memory:{},tools:{available:true,items:[{name:'qq_group_info',description:'群资料',active:true},{name:'qq_member_manage',description:'管理成员',active:false}]}};renderSettings();globalThis.saved=null;mutate=(action,payload)=>saved=payload;`,context);
  assert.ok(element('#tool-choices').innerHTML.includes('legacy_tool'));
  assert.ok(element('#tool-choices').innerHTML.includes('宿主已停用'));
  element('#tool-search').value='管理';element('#tool-search').oninput();
  assert.ok(!element('#tool-choices').innerHTML.includes('qq_group_info'));
  element('#tool-select-visible').onclick();
  element('#autonomous_tool_policy').value='denylist';element('#autonomous_tool_policy').onchange();
  element('#tool-select-visible').onclick();
  element('#autonomous_tool_policy').value='allowlist';element('#autonomous_tool_policy').onchange();
  element('#tool-extra').value='extra_custom, qq_group_info';
  element('#settings-form').onsubmit({preventDefault(){},currentTarget:{elements:{namedItem:()=>null}}});
  assert.deepEqual(JSON.parse(runInContext('JSON.stringify(saved.autonomous_tools)',context)),['extra_custom','legacy_tool','qq_group_info','qq_member_manage']);
  assert.deepEqual(JSON.parse(runInContext('JSON.stringify(saved.autonomous_tool_denylist)',context)),['existing_ban','qq_member_manage']);
  assert.equal(runInContext('saved.autonomous_tool_policy',context),'allowlist');
});

test('tool refresh keeps the unsaved choice when its tool is no longer registered', async () => {
  const {context,element}=fixture();
  runInContext(`data={settings:{blocked_senders:[],target_senders:[],allowed_sessions:[],autonomous_tools:['old_tool'],autonomous_tool_denylist:[]},memory:{},tools:{items:[{name:'old_tool',description:'旧工具',active:true}]}};renderSettings();bridge.apiGet=async()=>({tools:{items:[{name:'new_tool',description:'新工具',active:true}]}});globalThis.saved=null;mutate=(action,payload)=>saved=payload;`,context);
  await element('#tool-refresh').onclick();
  assert.ok(element('#tool-choices').innerHTML.includes('new_tool'));
  assert.ok(element('#tool-choices').innerHTML.includes('old_tool'));
  assert.ok(element('#tool-choices').innerHTML.includes('未注册'));
  element('#settings-form').onsubmit({preventDefault(){},currentTarget:{elements:{namedItem:()=>null}}});
  assert.deepEqual(JSON.parse(runInContext('JSON.stringify(saved.autonomous_tools)',context)),['old_tool']);
});

test('tool choices escape provider descriptions and names', () => {
  const {context,element}=fixture();
  runInContext(`data={settings:{blocked_senders:[],target_senders:[],allowed_sessions:[],autonomous_tools:[]},memory:{},tools:{items:[{name:'<tool>',description:'<img src=x onerror=alert(1)>',active:true}]}};renderSettings();`,context);
  assert.ok(!element('#tool-choices').innerHTML.includes('<img'));
  assert.ok(element('#tool-choices').innerHTML.includes('&lt;tool&gt;'));
});

for (const approved of [false,true]) test(`switching prompt editor protects an unsaved body ${approved}`, async () => {
  const {context,element,confirmations}=fixture();
  const buttons=[{dataset:{prompt:'review'}}];
  context.document.querySelectorAll=selector=>selector==='[data-prompt]'?buttons:[];
  runInContext(`data={prompts:{revision:'r1',items:[{key:'planner',title:'决策',description:'规则',placeholders:[],default:'默认',body:'旧规则',customized:true,unknown:[]},{key:'review',title:'自检',description:'核对',placeholders:[],default:'默认',body:'核对规则',customized:true,unknown:[]}]}};selectedPrompt='planner';dirty=true;renderPrompts();`,context);
  const before=element('#content').innerHTML;
  confirmations.push(approved);
  await buttons[0].onclick();
  assert.equal(runInContext('selectedPrompt',context),approved?'review':'planner');
  assert.equal(runInContext('dirty',context),!approved);
  if (!approved) assert.equal(element('#content').innerHTML,before);
});


test('review triage separates scores and verdicts and only selects passed pending entries', () => {
  const {context,element}=fixture();
  runInContext(`dirty=false;data.library={total:237,offset:0,review_counts:{passed:1,flagged:1,unknown:235},issue_counts:{learning_target:1},traits:[{...data.traits[0],id:1,scope:'a'.repeat(24),updated:1,confidence:.9,review:{approved:false,reason:'没有机器人自身发言',issue:'learning_target'}},{...data.traits[0],id:2,scope:'b'.repeat(24),updated:2,review:{approved:true}}]};renderLibrary();`,context);
  const html=element('#content').innerHTML;
  assert.ok(html.includes('提取自评 90%'));
  assert.ok(html.includes('自检存疑 · 学习对象混淆'));
  assert.ok(html.includes('无匹配自检（235）'));
  element('#select-passed').onclick();
  assert.equal(runInContext('librarySelection.size',context),1);
  assert.equal(runInContext('librarySelection.has(2)',context),true);
  element('#select-page').onclick();
  assert.equal(runInContext('librarySelection.size',context),2);
  runInContext('data.library.traits[1].updated=3;renderLibrary();',context);
  assert.equal(runInContext('librarySelection.has(2)',context),false);
});
