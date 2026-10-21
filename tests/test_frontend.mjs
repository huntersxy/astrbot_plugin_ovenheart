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
