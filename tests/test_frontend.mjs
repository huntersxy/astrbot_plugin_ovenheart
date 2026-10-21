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
  for (const name of ['participation_mode','participation_min_messages','participation_debounce','participation_cooldown','participation_hourly_limit','planner_provider_id','planner_timeout','planner_reply_threshold','planner_max_wait','autonomous_tools']) {
    assert.ok(html.includes('name="'+name+'"'),name);
  }
  assert.ok(html.includes('value="off" selected'));
  assert.ok(html.includes('value="contextual" selected'));
  assert.ok(html.includes('name="rerank_provider_id"'));
  assert.ok(html.includes('name="selector_timeout"'));
  assert.ok(html.includes('name="guidance_delivery"'));
  assert.ok(html.includes('value="system" selected'));
  assert.ok(html.includes('value="dual"'));
  assert.ok(html.includes('第三方运行器需自行支持'));
  assert.ok(html.includes('本轮不追加学习条目'));
});
