const $ = (selector) => document.querySelector(selector);
const escape = (value) => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[c]));
const kinds = {personality:"人格倾向", expression:"表达方式", jargon:"群聊词汇", behavior:"行为策略"};
const statuses = {pending:"待审核", active:"已生效", rejected:"已拒绝", disabled:"已停用", success:"学习完成", error:"学习失败", running:"学习中"};
const actions = {review_batch:"批量审核", profile:"修改人格底稿", trait:"调整学习条目", restore:"恢复历史版本", clear_messages:"清除原始素材", feedback:"调整习惯评价", learning:"合并学习结果", jargon_isolation:"迁回来源词汇"};
const date = value => new Date(value * 1000).toLocaleString("zh-CN", {month:"2-digit", day:"2-digit", hour:"2-digit", minute:"2-digit"});
const reviewGroups = {passed:"自检通过", flagged:"自检存疑", unknown:"无匹配自检"};
const reviewIssues = {learning_target:"学习对象混淆", evidence:"证据不足", scope:"适用范围或前提", identity:"身份或个人事实", merge:"合并不相容", delivery:"投递或工具要求", other:"其他问题"};
let settingsSection = "learning", memorySection = "core";
let libraryScope = "all", libraryOffset = 0, reviewFilter = "all", libraryQuery = "", librarySearchDraft = "";
const librarySelection = new Map();
const libraryPositions = new Map(), historyDetails = new Map();
let libraryMobileDetail = false, libraryPageScroll = 0, libraryFiltersOpen = false;
let historySection = "learning", historyStatus = "all", historyOffset = 0, historyAnchor = 0, historyOpenId = null;
let historyQuery = "", historySearchDraft = "", scopeFilter = "";
const historySections = {learning:"学习批次", participation:"参与判断", replies:"回复链路", applications:"习惯引用", versions:"历史版本"};
const historyStatuses = {learning:{error:"失败", running:"学习中", success:"完成"}, participation:{admitted:"交给宿主", waiting:"静默等待", observed:"旁观", limited:"冷却或限额", cancelled:"已取消", error:"决策失败"}, replies:{send_hook:"发送阶段", prepared:"已组装", cancelled:"已取消", empty_response:"未生成回复"}, applications:{}, versions:{}};


function reviewLabel(trait) {
  if (trait.review?.approved === true) return "自检通过";
  if (trait.review?.approved === false) return `自检存疑 · ${reviewIssues[trait.review.issue] || "其他问题"}`;
  return "无匹配自检";
}
let bridge, data, selectedScope = "", tab = "profile", selectedTrait = null, selectedPrompt = null, category = "all", status = "pending", pending = false, polling = false, dirty = false;

function confirmAction(message) {
  const dialog = $("#confirmation");
  if (dialog.open) return Promise.resolve(false);
  $("#confirmation-message").textContent = message;
  dialog.returnValue = "cancel";
  return new Promise(resolve => {
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "continue"), {once:true});
    dialog.showModal();
  });
}

function notify(message, error = false) {
  $("#notice").hidden = false;
  $("#notice").textContent = message;
  $("#notice").dataset.error = String(error);
}

const readRequests = new Map();
const viewCache = new Map();
const localOperations = new Set();
let loadVersion = 0, cacheEpoch = 0, editVersion = 0, pollTimer, pollErrors = 0, toolsCache;
const viewKey = () => JSON.stringify([selectedScope, tab, ...(tab === "library" ? [libraryScope, category, status, libraryOffset, reviewFilter, libraryQuery] : tab === "history" ? [historySection, historyStatus, historyOffset, historyAnchor, historyQuery] : [])]);
const viewName = () => ["memory", "emojis"].includes(tab) ? "settings" : tab === "portrait" ? "profile" : ["library", "prompts", "history"].includes(tab) ? "summary" : tab;

function deadline(promise, milliseconds, message) {
  let timer;
  return Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(message)), milliseconds); })]).finally(() => clearTimeout(timer));
}

async function api(endpoint, body, query = {}) {
  const params = endpoint === "state" ? {scope:selectedScope, ...query} : query;
  let promise;
  if (body === undefined) {
    const key = JSON.stringify([cacheEpoch, endpoint, params]);
    promise = readRequests.get(key);
    if (!promise) {
      promise = Promise.resolve(bridge.apiGet(endpoint, params)).finally(() => { if (readRequests.get(key) === promise) readRequests.delete(key); });
      readRequests.set(key, promise);
    }
  } else promise = bridge.apiPost(endpoint, body);
  const response = body !== undefined && !["preview", "preview/model"].includes(endpoint)
    ? await promise // An uncertain write is never silently retried.
    : await deadline(promise, endpoint === "preview/model" ? 100000 : 15000, "读取数据超时，当前页面仍可操作；可切换页面或稍后刷新。");
  if (response?.status === "error") throw new Error(response.message || "请求失败，请刷新页面后重试。");
  return response?.status === "ok" ? response.data : response;
}

function invalidateViews() { cacheEpoch++; loadVersion++; viewCache.clear(); }
function writeContext() { return {key:viewKey(), edit:editVersion}; }
async function afterWrite(context) {
  invalidateViews();
  if (context.key === viewKey() && context.edit === editVersion) dirty = false;
  load(!dirty).catch(error => notify(`修改已提交，但本页刷新失败：${error.message}。可稍后点击刷新。`, true));
}
function scopeOptionLabel(scope) {
  return `${scope.label} / ${scope.persona === "[%None]" ? "未设置人格" : scope.persona}`;
}
function fillScopeOptions() {
  const scopeSelect = $("#scope"), filterInput = $("#scope-filter");
  const needle = scopeFilter.trim().toLowerCase();
  const scopes = data?.scopes || [];
  const matched = needle
    ? scopes.filter(scope => scopeOptionLabel(scope).toLowerCase().includes(needle) || scope.id.toLowerCase().includes(needle))
    : scopes;
  const keepSelected = selectedScope && !matched.some(scope => scope.id === selectedScope)
    ? scopes.find(scope => scope.id === selectedScope)
    : null;
  const options = keepSelected ? [keepSelected, ...matched.filter(scope => scope.id !== selectedScope)] : matched;
  scopeSelect.innerHTML = options.length
    ? options.map(scope => `<option value="${escape(scope.id)}">${escape(scopeOptionLabel(scope))}</option>`).join("")
    : scopes.length
      ? '<option value="">无匹配会话</option>'
      : '<option value="">尚无学习会话</option>';
  if (selectedScope && options.some(scope => scope.id === selectedScope)) scopeSelect.value = selectedScope;
  scopeSelect.disabled = !scopes.length;
  if (filterInput) {
    filterInput.disabled = !scopes.length;
    filterInput.value = scopeFilter;
    filterInput.placeholder = scopes.length > 8 ? `搜索 ${scopes.length} 个会话或人格` : "搜索会话或人格";
  }
}
function updateHeader() {
  fillScopeOptions();
  const activeHabits = data.active_count ?? data.traits?.filter(t => t.status === "active").length ?? 0;
  const memoryNote = !data.memory_runtime?.available ? "记忆降级" : !data.settings.memory_enabled ? "记忆关闭" : data.settings.native_memory_enabled ? (data.settings.memory_isolation ? "记忆按会话" : "记忆同人格共享") : "原生记忆未开";
  $("#session-status").textContent = data.scope
    ? `${data.scope.pending} 条待学 · ${activeHabits} 条已生效 · ${memoryNote}`
    : "收到有效聊天后，会话会出现在这里。";
  if (!window.__ovenheartMemoryWarned && data.memory_runtime && !data.memory_runtime.available) {
    window.__ovenheartMemoryWarned = true;
    notify(`记忆以降级模式运行：${data.memory_runtime.degraded_reason || "准备失败，学习与参与仍可用。"}`, true);
  }
  $("#review-count").textContent = (data.review_count ?? data.scopes.reduce((sum, scope) => sum + scope.review_count, 0)) || "";
  renderNavigation();
}
function rememberView() {
  viewCache.delete(viewKey());
  viewCache.set(viewKey(), {data, at:Date.now()});
  while (viewCache.size > 8) viewCache.delete(viewCache.keys().next().value);
}
function showView() {
  const cached = viewCache.get(viewKey());
  if (cached) data = cached.data;
  if (cached || data && ["profile", "settings", "memory", "emojis"].includes(tab) && (!selectedScope || data.scope?.id === selectedScope)) {
    render();
    updateHeader();
  } else {
    renderNavigation();
    $("#content").innerHTML = empty("正在读取本页", "可以继续切换页面，数据就绪后会在此显示。");
  }
}

async function load(renderContent = true) {
  if (renderContent && tab === "history" && !historyOffset && !historyOpenId) historyAnchor = 0;
  const version = ++loadVersion, key = viewKey(), scopeAtStart = selectedScope;
  const pageAtStart = tab, view = renderContent ? viewName() : "summary";
  const previousSettingsRevision = data?.settings_revision;
  const previousPromptsRevision = data?.prompts?.revision;
  const detail = renderContent && pageAtStart === "library"
    ? api("library", undefined, {scope:libraryScope, kind:category, status, offset:libraryOffset, review:reviewFilter, query:libraryQuery})
    : renderContent && pageAtStart === "history" && selectedScope ? api("history", undefined, {scope:selectedScope, section:historySection, status:historyStatus, offset:historyOffset, anchor:historyAnchor, query:historyQuery})
    : renderContent && pageAtStart === "prompts" ? api("prompts") : Promise.resolve(null);
  const current = () => version === loadVersion && key === viewKey();
  const [next, extra] = await Promise.all([api("state", undefined, {scope:scopeAtStart, view}), detail]).catch(error => { if (!current()) return [null, null]; throw error; });
  if (!current() || !next) return;
  const previous = data;
  const preserved = view === "summary" && previous?.scope?.id === next.scope?.id
    ? Object.fromEntries(["traits", "runs", "revisions", "applications", "decisions", "turn_traces"].filter(k => k in previous).map(k => [k, previous[k]])) : {};
  data = {...previous, ...next, ...preserved};
  if (!selectedScope && data.scope) selectedScope = data.scope.id;
  if (pageAtStart === "library" && extra) { data.library = extra; libraryOffset = extra.offset; }
  if (pageAtStart === "prompts" && extra) data.prompts = extra;
  if (pageAtStart === "history" && extra) { data.history = extra; historyOffset = extra.offset || 0; historyAnchor = extra.anchor || 0; }
  if (renderContent && pageAtStart === "history" && !extra && data.scope) {
    const history = await api("history", undefined, {scope:selectedScope, section:historySection, status:historyStatus, offset:historyOffset, anchor:historyAnchor, query:historyQuery});
    if (version !== loadVersion || tab !== pageAtStart) return;
    data.history = history; historyOffset = history.offset || 0; historyAnchor = history.anchor || 0;
  }
  if (pageAtStart === "prompts" && !extra && next.prompts_revision && next.prompts_revision !== previousPromptsRevision) {
    const prompts = await api("prompts");
    if (version !== loadVersion || tab !== pageAtStart || selectedScope !== data.scope?.id && data.scope) return;
    data.prompts = prompts;
  }
  updateHeader();
  const promptsChanged = pageAtStart === "prompts" && data.prompts?.revision !== previousPromptsRevision;
  if (!dirty && (renderContent || (["settings", "memory", "emojis"].includes(tab) && data.settings_revision !== previousSettingsRevision) || promptsChanged)) render();
  rememberView();
}

function empty(title, description) {
  return `<div class="empty"><h2>${escape(title)}</h2><p>${escape(description)}</p></div>`;
}

function fieldHelp(id, help) {
  if (!help) return "";
  if (help.length <= 72) return `<small id="${id}">${help}</small>`;
  return `<details class="field-help" id="${id}"><summary>说明</summary><small>${help}</small></details>`;
}
function switchField(name, label, description, checked) {
  const shortHelp = description && description.length <= 48 ? `<small>${description}</small>` : "";
  const longHelp = description && description.length > 48
    ? `<details class="field-help"><summary>说明</summary><small>${description}</small></details>`
    : "";
  return `${longHelp ? '<div class="switch-block">' : ""}<label class="switch"><input type="checkbox" name="${name}" ${checked ? "checked" : ""}><span>${label}${shortHelp}</span></label>${longHelp}${longHelp ? "</div>" : ""}`;
}

function field(name, label, value, help, options = {}) {
  const hint = help ? `aria-describedby="${name}-help"` : "";
  return `<div class="field"><label for="field-${name}">${label}</label>${options.type === "number" ? `<input id="field-${name}" name="${name}" type="number" min="${options.min}" max="${options.max}" step="${options.step || 1}" value="${escape(value)}" ${hint} required>` : options.type === "text" ? `<input id="field-${name}" name="${name}" value="${escape(value)}" ${hint} maxlength="256">` : `<textarea id="field-${name}" name="${name}" ${hint} maxlength="${options.maxlength || 1000}" ${options.large ? 'class="large"' : ""}>${escape(value)}</textarea>`}${fieldHelp(`${name}-help`, help)}</div>`;
}

function renderNavigation() {
  $("#session-context").hidden = !(["portrait", "history", "memory", "emojis"].includes(tab)
    || tab === "profile" && data?.settings.session_isolation
    || tab === "settings" && settingsSection === "maintenance");
  for (const button of document.querySelectorAll("[data-tab]")) {
    if (button.dataset.tab === tab) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current");
  }
}

function render() {
  renderNavigation();
  if (!data) return;
  if (!data.scope && tab !== "settings" && tab !== "memory" && tab !== "emojis" && tab !== "library" && tab !== "prompts") {
    $("#content").innerHTML = empty("从一段真实的对话开始", "在已启用的 AstrBot 会话中聊几句，工坊会自动积累文字素材。也可以先打开设置，选择学习对象与生效方式。");
    return;
  }
  if (tab === "profile") renderProfile();
  else if (tab === "portrait") renderSelfPortrait();
  else if (tab === "library") renderLibrary();
  else if (tab === "history") renderHistory();
  else if (tab === "memory") renderMemory();
  else if (tab === "emojis") window.OvenHeartEmojis.render({root:$("#content"),data,api,escape,notify,confirmAction,isDirty:()=>dirty,clearDirty:()=>{dirty=false;},saveSettings:(payload)=>mutate("settings",payload,selectedScope,data.settings_revision)});
  else if (tab === "prompts") renderPrompts();
  else renderSettings();
}

function renderProfile() {
  const p = data.scope.profile;
  const profileScope = data.effect_scope || selectedScope;
  $("#content").innerHTML = `<div class="two-col"><form id="profile-form"><h2>${data.settings.session_isolation ? "当前会话的人格底稿" : "共用人格底稿"}</h2><p class="section-intro">${data.settings.session_isolation ? "只影响所选会话与人格。" : "底稿与习惯在允许的会话共用；群聊词汇仍按来源隔离。"}在 AstrBot 人格之上补充身份、表达和应对方式。</p>
    ${field("identity", "人格补充", p.identity, "留空则沿用 AstrBot 核心人格。", {large:true})}
    ${field("reply_style", "表达基调", p.reply_style, "例如：日常、简短，接得住玩笑。")}
    ${field("behavior", "行为准则", p.behavior, "例如：先倾听；拿不准时先澄清。")}
    <div class="switches">${switchField("learning_enabled", "积累并学习", data.settings.session_isolation ? "仅当前会话与人格" : "影响所有允许的会话", p.learning_enabled)}${switchField("apply_enabled", "应用已生效的习惯", "关闭后仍保留学习数据", p.apply_enabled)}</div>
    <button class="primary" type="submit">保存人格底稿</button></form>
    <aside class="preview"><h2>试一句看看</h2><p>免费预览本插件会追加的表达参考；不调用模型、不发消息。</p><form id="preview-form">${!data.settings.session_isolation && data.scopes.length > 1 ? `<label for="preview-scope">示例会话</label><select id="preview-scope">${data.scopes.map(scope => `<option value="${escape(scope.id)}" ${scope.id === selectedScope ? "selected" : ""}>${escape(scopeOptionLabel(scope))}</option>`).join("")}</select>` : ""}<label for="preview-query">试一句话</label><textarea id="preview-query" placeholder="例如：今天又搞砸了，有点难过。" maxlength="1200"></textarea><div class="preview-actions"><button class="tonal" type="submit">预览生效内容</button><button class="tonal" type="button" id="model-preview" ${!selectedScope || selectedScope === data.shared_scope?.id ? "disabled" : ""}>生成示例回复</button></div><details class="field-help"><summary>说明</summary><small>示例回复会调用模型，可能产生费用；不保存。请先选真实会话。预览用文字检索，真实回复还可能经向量与情境筛选。</small></details></form><pre id="preview-output" hidden></pre><div id="model-preview-result" class="model-preview-result" hidden aria-live="polite"><p id="model-preview-meta" class="muted"></p><div id="model-preview-text" class="model-preview-text"></div></div></aside></div>`;
  if ($("#preview-scope")) $("#preview-scope").onchange = changeScope;
  $("#profile-form").addEventListener("submit", async event => {
    event.preventDefault();
    const form = event.currentTarget;
    await mutate("profile", {identity:form.identity.value, reply_style:form.reply_style.value, behavior:form.behavior.value, learning_enabled:form.learning_enabled.checked, apply_enabled:form.apply_enabled.checked}, profileScope);
  });
  $("#preview-form").addEventListener("submit", event => guarded(async () => {
    const scope = selectedScope, output = $("#preview-output");
    const result = await api("preview", {scope, query:$("#preview-query").value});
    if (tab !== "profile" || selectedScope !== scope || $("#preview-output") !== output) return;
    $("#preview-output").hidden = false;
    $("#preview-output").textContent = `${result.retrieval || ""}\n${(result.selected || []).map(t => `#${t.id} ${t.reason}`).join("；")}\n\n${result.guidance || "当前没有生效的补充内容。可审核学习结果，或检查全局与当前会话的应用开关。"}`.trim();
  }, event, {local:"preview", selector:"#preview-form button[type=submit]"}));
  $("#model-preview").onclick = event => guarded(async () => {
    const scope = selectedScope;
    const query = $("#preview-query").value.trim();
    if (!query) throw new Error("请先输入一句示例。生成会调用模型，可能产生费用。");
    const button = $("#model-preview"), output = $("#model-preview-result");
    button.textContent = "正在生成示例…";
    try {
      const result = await api("preview/model", {scope, query});
      if (tab !== "profile" || selectedScope !== scope || $("#model-preview-result") !== output) return;
      output.hidden = false;
      $("#model-preview-meta").textContent = `${result.model || result.provider_id || "模型示例"} · 用时 ${result.seconds} 秒${result.usage ? ` · 输入 ${Number(result.usage.input_cached) + Number(result.usage.input_other)} / 输出 ${Number(result.usage.output)} token` : " · 未返回 token 用量"} · ${result.selected_ids.length ? "表达参考 " + result.selected_ids.map(id => "#" + id).join("、") : "沿用人格底稿"}。${result.retrieval || ""}`;
      $("#model-preview-text").textContent = result.reply;
    } finally { button.textContent = "生成示例回复"; }
  }, event, {local:"preview-model", selector:"#model-preview"});
  if (localOperations.has("preview-model")) { $("#model-preview").disabled = true; $("#model-preview").textContent = "示例正在生成…"; }
}

function imageFileData(file) {
  if (file.size > 2 * 1024 * 1024) return Promise.reject(new Error("自画像不能超过 2 MB，请缩小图片后上传。"));
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error("图片读取失败，请重新选择。"));
    reader.readAsDataURL(file);
  });
}

function renderSelfPortrait() {
  const scope = data.scope?.id || selectedScope, persona = data.scope?.persona;
  const canSave = Boolean(scope && scope !== data.shared_scope?.id && scope !== "0".repeat(24) && persona);
  const portrait = data.self_portrait;
  $("#content").innerHTML = `<section class="self-portrait"><h2>人格自画像</h2><p class="section-intro">给当前 AstrBot 人格设置外观描述和参考图，帮助它在聊天图片中辨认自己的形象。不同人格分别保存，同一人格在各会话共用。</p>${canSave ? `<p class="muted">当前人格：${escape(persona)}</p>` : '<p class="notice">先在顶部选择一个真实会话，再为它当前使用的人格设置自画像。</p>'}<form id="portrait-form"><fieldset ${canSave ? "" : "disabled"}><div class="portrait-layout"><div class="portrait-reference"><img id="portrait-preview" alt="当前人格的自画像参考图" hidden><p id="portrait-image-status" class="muted">${portrait?.has_image ? "正在读取参考图…" : "尚未添加参考图，也可以先只填写外观描述。"}</p><label for="portrait-file">参考图片（可选）</label><input id="portrait-file" name="image" type="file" accept="image/png,image/jpeg,image/webp"><small>静态 PNG、JPG 或 WebP，2 MB 以内，最多 419 万像素。更换图片后保存才会生效。</small></div><div><label for="portrait-description">外观特征</label><textarea id="portrait-description" name="description" maxlength="1200" required placeholder="例如：金色短发，头上有插座形发饰，常穿白色连衣裙。描述外观即可，不用重写人格。">${escape(portrait?.description || "")}</textarea><p class="muted">文字聊天只使用这段描述；处理来图且回复模型支持识图时，才会附上参考图。外观资料不会替换人格身份。</p><div class="actions"><button type="submit" class="primary">保存自画像</button>${portrait ? '<button type="button" id="portrait-clear" class="danger">清除自画像</button>' : ""}</div></div></div></fieldset></form></section>`;
  const form = $("#portrait-form"), fileInput = $("#portrait-file");
  const live = () => tab === "portrait" && selectedScope === scope && $("#portrait-form") === form;
  const show = url => { $("#portrait-preview").src = url; $("#portrait-preview").hidden = false; $("#portrait-image-status").textContent = ""; };
  if (canSave && portrait?.has_image) api("portrait", undefined, {scope, image:"true"}).then(saved => {
    if (live() && !fileInput.files?.length && saved?.encoded) show(`data:${saved.mime_type};base64,${saved.encoded}`);
  }).catch(error => { if (live()) $("#portrait-image-status").textContent = `参考图读取失败：${error.message}，可刷新页面重试。`; });
  fileInput.onchange = async () => {
    const file = fileInput.files?.[0];
    if (!file) return;
    try { const url = await imageFileData(file); if (live() && fileInput.files[0] === file) show(url); }
    catch (error) { if (live()) notify(error.message, true); }
  };
  form.onsubmit = event => {
    const description = form.elements.description.value.trim(), file = fileInput.files?.[0], context = writeContext();
    return guarded(async () => {
      const encoded = file ? (await imageFileData(file)).split(",", 2)[1] : undefined;
      await api("portrait", {scope, action:"save", description, ...(encoded ? {encoded} : {})});
      notify("自画像已保存，后续对话会使用当前人格的外观资料。");
      await afterWrite(context);
    }, event);
  };
  if (portrait) $("#portrait-clear").onclick = async event => {
    if (!await confirmAction("清除当前人格的参考图和外观描述？")) return;
    const context = writeContext();
    await guarded(async () => { await api("portrait", {scope, action:"clear"}); notify("自画像已清除。"); await afterWrite(context); }, event);
  };
}

function libraryPositionKey() { return JSON.stringify([libraryScope, category, status, libraryOffset, reviewFilter, libraryQuery]); }
function rememberLibraryPosition() {
  const list = $("#library-list"), detail = $("#library-detail");
  if (!list?.dataset.positionKey) return;
  libraryPositions.set(list.dataset.positionKey, {list:list.scrollTop || 0, detail:detail?.scrollTop || 0, selected:Number(list.dataset.selected)});
  while (libraryPositions.size > 12) libraryPositions.delete(libraryPositions.keys().next().value);
}
function revealLibraryDetail() {
  const workspace = $("#library-workspace");
  if (!workspace) return;
  if (window.innerWidth <= 680) { libraryPageScroll = window.scrollY; libraryMobileDetail = true; workspace.dataset.detail = "true"; }
  const bounds = workspace.getBoundingClientRect?.();
  if (bounds && (window.innerWidth <= 680 || bounds.bottom > window.innerHeight || bounds.top < 0)) workspace.scrollIntoView?.({block:"start", behavior:"auto"});
  $("#library-detail")?.focus?.({preventScroll:true});
}
function renderLibrary() {
  const oldKey = $("#library-list")?.dataset.positionKey;
  const oldSelected = Number($("#library-list")?.dataset.selected);
  rememberLibraryPosition();
  const positionKey = libraryPositionKey(), position = libraryPositions.get(positionKey);
  if (oldKey && oldKey !== positionKey && position?.selected) selectedTrait = position.selected;
  const library = data.library;
  const traits = (library?.traits || data.traits).filter(t => (category === "all" || t.kind === category) && (status === "all" || t.status === status));
  if (!traits.some(t => t.id === selectedTrait)) selectedTrait = traits[0]?.id;
  const trait = traits.find(t => t.id === selectedTrait);
  const review = trait && (trait.review || (library ? [] : data.runs || []).filter(run => !run.scope || run.scope === trait.scope).flatMap(run => run.review?.candidates || []).find(candidate => candidate.kind === trait.kind && candidate.situation === trait.situation && candidate.content === trait.content));
  const archived = trait && data.shared_scope && trait.kind === "jargon" && trait.scope === data.shared_scope.id;
  const expressionExamples = trait?.kind === "expression" && trait.examples?.length ? `<section class="evidence" aria-label="原话片段"><h3>原话片段</h3><p class="muted">勾选要移除的片段，随下方保存或审核一起生效；原证据与历史版本保留。实际改写规则时会清除全部旧片段。</p>${trait.examples.map((example, index) => `<blockquote><p>${escape(example.text)}</p><small>${escape(example.sender || "学习对象")} · ${date(example.created)}</small><label class="switch sample-removal"><input type="checkbox" name="remove_examples" value="${index}" aria-label="移除片段：${escape(example.text)}"><span>移除此片段</span></label></blockquote>`).join("")}</section>` : "";
  const reviewHint = trait ? `<section class="review-note" aria-label="学习自检"><h3>${escape(reviewLabel(trait))}</h3><p>${review?.approved === false ? escape(review.reason) : review?.approved === true ? "入库自检通过；仍是模型判断。" : "无匹配自检记录，不代表通过。"}</p><p class="muted">提取自评 ${Math.round(trait.confidence * 100)}% · ${trait.manual ? "人工已锁定" : "以审核状态为准"}</p>${review?.run_id ? `<small>批次 #${escape(review.run_id)} · ${date(review.checked_at)}</small>` : ""}</section>` : "";
  for (const [id, entry] of librarySelection) {
    if (!traits.some(t => t.id === id && t.status === "pending" && t.updated === entry.updated)) librarySelection.delete(id);
  }
  const counts = library?.review_counts || {};
  const filterActive = libraryScope !== "all" || category !== "all" || status !== "pending" || reviewFilter !== "all";
  const filterSummary = [
    libraryScope !== "all" ? "会话" : "",
    category !== "all" ? kinds[category] : "",
    status !== "pending" ? (statuses[status] || "全部状态") : "",
    reviewFilter !== "all" ? (reviewGroups[reviewFilter] || reviewIssues[reviewFilter] || "自检") : "",
  ].filter(Boolean).join(" · ") || "默认：待审核";
  const searchForm = `<form id="library-search" class="library-search" role="search" aria-label="搜索学习库">
    <div class="filter-field"><label for="library-query">搜索</label><input id="library-query" type="search" maxlength="200" autocomplete="off" placeholder="情境、习惯内容或学习原文" value="${escape(librarySearchDraft)}"></div>
    <button type="submit" class="primary">搜索</button><button type="button" id="library-clear" ${librarySearchDraft || libraryQuery ? "" : "disabled"}>清空</button>
    </form>`;
  const noResults = libraryQuery ? empty("未找到匹配条目", "换关键词，或调整筛选条件。") : empty(status === "pending" ? "暂无待审核条目" : "没有符合条件的结果", "可点「开始学习」，或换筛选查看。");
  const summary = `<header class="library-heading"><div><h2>学习库</h2><p class="muted">${library?.total ?? traits.length} 条 · 先搜再审</p></div></header>`;
  const batchControls = `<div class="batch-review" aria-label="批量审核"><details class="batch-selection-tools"><summary>批量审核</summary><div class="actions"><button type="button" id="select-page">勾选本页待审</button><button type="button" id="select-passed">只选自检通过</button><button type="button" id="clear-selection">清空勾选</button><span id="selected-count" role="status"></span><button type="button" class="tonal" data-batch-review="active">批准勾选</button><button type="button" class="danger" data-batch-review="rejected">拒绝勾选</button></div></details></div>`;
  const pagination = library && library.total > 100 ? `<div class="actions library-pages"><button id="library-prev" ${library.offset === 0 ? "disabled" : ""}>上一页</button><span class="muted">${library.offset / 100 + 1} / ${Math.ceil(library.total / 100)} 页</span><button id="library-next" ${library.offset + 100 >= library.total ? "disabled" : ""}>下一页</button></div>` : "";
  $("#content").innerHTML = `${summary}${searchForm}<div class="library-filterbar" id="library-filterbar" data-open="${libraryFiltersOpen}"><button type="button" id="library-filter-toggle" aria-controls="library-filters" aria-expanded="${libraryFiltersOpen}">筛选${filterActive ? " · 已调整" : ""}<span>${escape(filterSummary)}</span></button><div class="filter-row" id="library-filters"><div class="filter-field"><label for="library-scope">会话</label><select id="library-scope"><option value="all">全部会话</option>${([...(data.shared_scope ? [data.shared_scope] : []), ...(data.scopes || [])]).map(s => `<option value="${escape(s.id)}" ${libraryScope === s.id ? "selected" : ""}>${escape(s.label)} / ${escape(s.persona === "[%None]" ? "未设置人格" : s.persona)}</option>`).join("")}</select></div><div class="filter-field"><label for="kind-filter">类别</label><select id="kind-filter"><option value="all">全部类别</option>${Object.entries(kinds).map(([value, name]) => `<option value="${value}" ${category === value ? "selected" : ""}>${name}</option>`).join("")}</select></div><div class="filter-field"><label for="status-filter">状态</label><select id="status-filter">${Object.entries({pending:"待审核", active:"已生效", disabled:"已停用", rejected:"已拒绝", all:"全部状态"}).map(([value, name]) => `<option value="${value}" ${status === value ? "selected" : ""}>${name}</option>`).join("")}</select></div><div class="filter-field"><label for="review-filter">自检</label><select id="review-filter"><option value="all">全部自检结论</option>${Object.entries(reviewGroups).map(([key, label]) => `<option value="${key}" ${reviewFilter === key ? "selected" : ""}>${label}（${counts[key] ?? 0}）</option>`).join("")}${Object.entries(reviewIssues).map(([key, label]) => `<option value="${key}" ${reviewFilter === key ? "selected" : ""}>存疑：${label}（${library?.issue_counts?.[key] ?? 0}）</option>`).join("")}</select></div></div></div>${batchControls}
    ${traits.length ? `<div class="library" id="library-workspace" data-detail="${libraryMobileDetail}"><div class="library-list-pane"><div class="trait-list" id="library-list" tabindex="0" aria-label="学习条目，可独立滚动">${traits.map(t => `<div class="trait-choice">${t.status === "pending" ? `<label class="trait-check"><input type="checkbox" data-select-trait="${t.id}" aria-label="勾选：${escape(t.situation)}" ${librarySelection.has(t.id) ? "checked" : ""}></label>` : ""}<button type="button" class="trait-row" data-trait="${t.id}" aria-pressed="${selectedTrait === t.id}"><span class="row-meta"><span>${kinds[t.kind]}</span><span class="badge ${t.kind === "jargon" && data.shared_scope && t.scope === data.shared_scope.id ? "" : t.status}">${t.kind === "jargon" && data.shared_scope && t.scope === data.shared_scope.id ? "仅保留" : statuses[t.status]}</span></span><small class="trait-owner">${escape(t.label || data.scope?.label || "")} / ${escape((t.persona || data.scope?.persona) === "[%None]" ? "未设置人格" : (t.persona || data.scope?.persona || ""))}</small><strong>${escape(t.situation)}</strong><p>${escape(t.content)}</p><span class="row-meta">${t.evidence_count || t.evidence.length} 条证据 / ${t.support_count || 1} 批 · 提取自评 ${Math.round(t.confidence * 100)}%</span><small class="review-verdict">${escape(reviewLabel(t))}${t.manual ? " · 人工已决定" : ""}</small></button></div>`).join("")}</div>${pagination}</div><article class="detail" id="library-detail" tabindex="-1" aria-label="所选条目详情"><button id="library-back" type="button">返回条目列表</button><h2>${kinds[trait.kind]}</h2><p class="muted">所属会话：${escape(trait.label || data.scope?.label || "")} / ${escape((trait.persona || data.scope?.persona) === "[%None]" ? "未设置人格" : (trait.persona || data.scope?.persona || ""))}</p>${archived ? '<p class="muted" role="note">旧共用词汇的来源尚未确定，条目与证据完整保留，不参与回复。请在来源会话重新学习后审核。</p>' : trait.kind === "jargon" ? '<p class="muted" role="note">群聊词汇始终按来源会话与人格隔离，不受「启用会话隔离」开关影响。</p>' : ""}${reviewHint}<form id="trait-form">${expressionExamples}${field("situation", "适用情境", trait.situation, "只在适合的情境里使用，不必每次重复。", {maxlength:160})}${field("content", "习惯内容", trait.content, "你可以在生效前调整表达；编辑不会自动改变审核状态。", {maxlength:350})}<button type="submit">保存编辑</button></form><div class="actions">${!archived && trait.status !== "active" ? '<button class="primary" data-review="active">批准生效</button>' : trait.status === "active" ? '<button data-review="disabled">停用这条</button>' : ""}${trait.status === "pending" ? '<button class="danger" data-review="rejected">拒绝学习</button>' : '<button data-review="pending">移回待审核</button>'}</div><section class="evidence"><h3>积累与应用</h3><p class="muted">${trait.support_count || 1} 批独立素材，累计 ${trait.evidence_count || trait.evidence.length} 条证据。引用 ${trait.use_count || 0} 次；引用次数不代表模型采用或消息送达。${trait.manual ? " 人工决定已锁定，后续学习只补充证据。" : ""}</p><p class="muted">当前评价：${trait.rating === 1 ? "更合适" : trait.rating === -1 ? "不太合适" : "尚未评价"}</p><div class="actions" aria-label="习惯效果评价">${[[1,"更合适"],[-1,"不太合适"],[0,"清除评价"]].map(([value,label]) => `<button type="button" data-feedback="${value}" aria-pressed="${(trait.rating || 0) === value}">${label}</button>`).join("")}</div><p class="muted">评价调整后续排序；不合适的习惯也可直接停用。</p></section><details class="evidence library-evidence"><summary>最近的学习来源（${trait.evidence.length} 条）</summary><p class="muted">最多展示最近 32 条摘录，累计证据数保留。</p>${trait.evidence.map(e => `<blockquote><p>${escape(e.text)}</p><small>${e.label ? `${escape(e.label)} / ${escape(e.persona || "")} · ` : ""}${escape(e.sender)} · ${date(e.created)}</small></blockquote>`).join("")}</details></article></div>` : noResults}`;
  $("#library-filter-toggle").onclick = () => {
    libraryFiltersOpen = !libraryFiltersOpen;
    $("#library-filterbar").dataset.open = String(libraryFiltersOpen);
    $("#library-filter-toggle").setAttribute?.("aria-expanded", String(libraryFiltersOpen));
  };
  $("#library-scope").value = libraryScope;
  const search = async value => {
    const next = value.trim();
    if (next === libraryQuery) { librarySearchDraft = next; $("#library-query").value = next; $("#library-clear").disabled = !next; return; }
    if (dirty && !await confirmAction("搜索会放弃未保存的编辑，继续？")) return;
    const form = $("#library-search"), submit = form.querySelector("button[type=submit]");
    form.setAttribute("aria-busy", "true"); submit.disabled = true; submit.textContent = "搜索中…";
    try {
      await readView(async () => {
        const previous = [libraryQuery, libraryOffset, libraryMobileDetail];
        dirty = false; libraryQuery = librarySearchDraft = next; libraryOffset = 0; libraryMobileDetail = false;
        try { await load(); }
        catch (error) { [libraryQuery, libraryOffset, libraryMobileDetail] = previous; librarySearchDraft = libraryQuery; renderLibrary(); throw error; }
      });
    } finally {
      if (form.isConnected) { form.removeAttribute("aria-busy"); submit.disabled = false; submit.textContent = "搜索"; }
    }
  };
  $("#library-search").onsubmit = event => { event.preventDefault(); return search($("#library-query").value); };
  $("#library-clear").onclick = () => search("");
  $("#library-query").oninput = event => { librarySearchDraft = event.target.value; $("#library-clear").disabled = !librarySearchDraft && !libraryQuery; };
  for (const [selector, current] of [["#library-scope", libraryScope], ["#kind-filter", category], ["#status-filter", status], ["#review-filter", reviewFilter]]) {
    $(selector).onchange = async event => {
      const next = event.target.value; event.target.value = current;
      if (dirty && !await confirmAction("切换筛选会放弃未保存的编辑，继续？")) return;
      await readView(async () => {
        const previous = [libraryScope, category, status, libraryOffset, reviewFilter];
        dirty = false;
        if (selector === "#library-scope") libraryScope = next;
        else if (selector === "#kind-filter") category = next;
        else if (selector === "#status-filter") status = next;
        else reviewFilter = next;
        libraryOffset = 0; libraryMobileDetail = false; libraryFiltersOpen = false;
        try { await load(); }
        catch (error) { [libraryScope, category, status, libraryOffset, reviewFilter] = previous; renderLibrary(); throw error; }
      });
    };
  }
  const updateSelection = () => {
    $("#selected-count").textContent = `已勾选 ${librarySelection.size} 条待审核`;
    for (const node of document.querySelectorAll("[data-select-trait]")) node.checked = librarySelection.has(Number(node.dataset.selectTrait));
    for (const button of document.querySelectorAll("[data-batch-review]")) button.disabled = !librarySelection.size;
  };
  for (const input of document.querySelectorAll("[data-select-trait]")) input.onchange = () => {
    const t = traits.find(t => t.id === Number(input.dataset.selectTrait));
    if (input.checked) librarySelection.set(t.id, {id:t.id, scope:t.scope, updated:t.updated});
    else librarySelection.delete(t.id);
    updateSelection();
  };
  $("#select-page").onclick = () => { for (const t of traits.filter(t => t.status === "pending")) librarySelection.set(t.id, {id:t.id, scope:t.scope, updated:t.updated}); updateSelection(); };
  $("#select-passed").onclick = () => { librarySelection.clear(); for (const t of traits.filter(t => t.status === "pending" && t.review?.approved === true)) librarySelection.set(t.id, {id:t.id, scope:t.scope, updated:t.updated}); updateSelection(); };
  $("#clear-selection").onclick = () => { librarySelection.clear(); updateSelection(); };
  for (const button of document.querySelectorAll("[data-batch-review]")) button.onclick = async () => {
    const entries = [...librarySelection.values()];
    if (!entries.length) return;
    const targetStatus = button.dataset.batchReview;
    const unchecked = entries.filter(e => traits.find(t => t.id === e.id)?.review?.approved !== true).length;
    const message = `${targetStatus === "active" ? "批准生效" : "拒绝学习"}勾选的 ${entries.length} 条？${targetStatus === "active" && unchecked ? `其中 ${unchecked} 条自检存疑或无匹配自检，本次批准将覆盖该判断。` : ""}${dirty ? "未保存的编辑不会一并保存。" : ""}可在所属会话的历史版本中恢复。`;
    if (!await confirmAction(message)) return;
    const context = writeContext();
    await guarded(async () => {
      const result = await api("action", {action:"review_batch", payload:{entries, status:targetStatus}});
      for (const entry of entries) if (librarySelection.get(entry.id)?.updated === entry.updated) librarySelection.delete(entry.id);
      notify(result.message); await afterWrite(context);
    });
  };
  updateSelection();
  if (library && library.total > 100) {

    for (const [id, step] of [["#library-prev", -100], ["#library-next", 100]]) $(id).onclick = async () => {
      if (dirty && !await confirmAction("翻页会放弃未保存的编辑，继续？")) return;
      await readView(async () => {
        const previous = libraryOffset; dirty = false; libraryOffset += step; libraryMobileDetail = false;
        try { await load(); }
        catch (error) { libraryOffset = previous; throw error; }
      });
    };
  }
  const list = $("#library-list"), detail = $("#library-detail");
  if (list && traits.length) { list.dataset.positionKey = positionKey; list.dataset.selected = String(selectedTrait); list.scrollTop = position?.list || 0; }
  if (detail) detail.scrollTop = (oldSelected === selectedTrait || !oldKey && position?.selected === selectedTrait) ? position?.detail || 0 : 0;
  const back = $("#library-back");
  if (back) back.onclick = () => {
    libraryMobileDetail = false; $("#library-workspace").dataset.detail = "false";
    window.scrollTo?.({top:libraryPageScroll, behavior:"auto"});
    document.querySelector(`[data-trait="${selectedTrait}"]`)?.focus?.({preventScroll:true});
  };
  for (const button of document.querySelectorAll("[data-trait]")) button.onclick = async () => {
    const next = Number(button.dataset.trait);
    if (next === selectedTrait) { revealLibraryDetail(); return; }
    if (dirty && !await confirmAction("放弃当前未保存的编辑？")) return;
    dirty = false; selectedTrait = next; renderLibrary(); revealLibraryDetail();
  };

  if (trait) {
    for (const button of document.querySelectorAll("[data-feedback]")) button.onclick = async () => { if (dirty && !await confirmAction("评价会放弃未保存的编辑，继续？")) return; mutate("feedback", {id:trait.id, rating:Number(button.dataset.feedback)}, trait.scope); };
    $("#trait-form").onsubmit = async event => {
      event.preventDefault();
      const form = event.currentTarget;
      const removals = new FormData(form).getAll("remove_examples").map(index => trait.examples[Number(index)].text);
      await mutate("trait", {id:trait.id, situation:form.situation.value, content:form.content.value, ...(removals.length ? {remove_examples:removals} : {})}, trait.scope);
    };
    for (const button of document.querySelectorAll("[data-review]")) button.onclick = () => {
      const form = $("#trait-form");
      const removals = new FormData(form).getAll("remove_examples").map(index => trait.examples[Number(index)].text);
      mutate("trait", {id:trait.id, status:button.dataset.review, situation:form.situation.value, content:form.content.value, ...(removals.length ? {remove_examples:removals} : {})}, trait.scope);
    };
  }
}

function historyDetailKey(row) {
  return JSON.stringify([selectedScope, historySection, row.id, row.status, row.outcome, row.finished, row.updated]);
}
function historyDetail(section, row) {
  if (section === "learning") {
    const review = row.review || {}, timing = review.timing;
    return `<p>${escape(row.message || "正在分析本批素材…")}</p>${row.memory ? `<p class="muted">${escape(row.memory)}</p>` : ""}${timing ? `<p class="muted">提取 ${escape(timing.extraction_seconds)} 秒；自检 ${escape(timing.review_seconds)} 秒。</p>` : ""}${review.enabled && review.candidates?.length ? `<h3>自检提示与候选</h3><p>自检通过 ${escape(review.passed)} 条，标记 ${escape(review.flagged)} 条。未通过的新候选转待审；未通过的更新不改旧条目。模型判断可由人工审核覆盖。</p><ul>${review.candidates.filter(c => !c.approved).map(c => `<li><strong>${escape(kinds[c.kind] || c.kind)} · ${escape(c.situation)}</strong><p>${escape(c.content)}</p><p>${escape(c.reason)}</p></li>`).join("")}</ul>` : '<p class="muted">没有需要展开的自检候选；不代表已人工审核。</p>'}`;
  }
  if (section === "participation") return `<p>${escape(row.reason)}</p>${row.intent ? `<p>回复目标：${escape(row.intent)}</p>` : ""}<p class="muted">${row.mode === "direct" ? "直接唤醒" : row.mode === "observe" ? "仅观察" : "自动参与"} / ${({admitted:"交给宿主",waiting:"等待后再判断",observed:"已记录",limited:"冷却或限额",cancelled:"已取消",error:"决策失败"})[row.outcome] || escape(row.outcome)} / 把握 ${Math.round((row.confidence || 0) * 100)}%</p>`;
  if (section === "replies") {
    const t = row.details || {}, selected = t.selected_ids || [], included = t.included_ids || [], dropped = selected.filter(id => !included.includes(id));
    const cache = [["情境筛选",t.selector_cache_hit],["重排序",t.rerank_cache_hit]].map(([name, hit]) => `${name}：${hit === true ? "命中" : hit === false ? "未命中" : "未返回缓存信息"}`).join("；");
    return `<p>${escape(t.cancel_reason || t.reason)}</p><p>${escape(t.retrieval_message)}</p><p>检索选中 ${selected.length} 条，装入 ${included.length} 条，上限 ${escape(t.max_traits)} 条。</p><p>习惯与底稿 ${escape(t.habit_chars)} / ${escape(t.habit_budget)} 字符；额外群聊 ${escape(t.context_count)} / ${escape(t.context_limit)} 条、${escape(t.context_chars)} / ${escape(t.context_budget)} 字符。</p>${t.selection_seconds != null ? `<p>习惯检索 ${escape(t.selection_seconds)} 秒；请求组装 ${escape(t.assembly_seconds)} 秒；收到消息至组装 ${escape(t.input_age_seconds)} 秒${t.elapsed_seconds != null ? `；至当前阶段 ${escape(t.elapsed_seconds)} 秒` : ""}。</p>` : ""}<p>已装入：${included.length ? included.map(id => `#${escape(id)}`).join("、") : "无学习习惯"}${dropped.length ? `；选中未装入：${dropped.map(id => `#${escape(id)}`).join("、")}（受生效状态或字符预算限制）` : ""}</p><p>${escape(cache)}</p><small>目标用户 ${escape(t.target_sender_id)} · 消息 ${escape(t.target_message_id || "无平台编号")}。临时文本 ${escape(t.temporary_chars)} 字符；不是整个请求的字符数。</small>`;
  }
  if (section === "applications") return `<p>记录引用：${(row.trait_ids || []).map(id => `#${escape(id)}`).join("、") || "无条目"}</p><p class="muted">引用次数不代表模型采用，也不代表平台送达。</p>`;
  return `<p>${row.scope === data.shared_scope?.id ? "共用空间" : "当前会话"}的${escape(actions[row.action] || row.action)}之前版本。</p><p class="muted">恢复底稿和学习条目，不恢复原始素材。恢复前也会留存当前版本。</p><button type="button" data-restore="${escape(row.id)}" data-restore-scope="${escape(row.scope)}">恢复此版本</button>`;
}
function bindHistoryRestore(root = document) {
  for (const button of root.querySelectorAll("[data-restore]")) button.onclick = async () => {
    if (await confirmAction("恢复这个版本的人格底稿和学习状态？当前版本也会自动保留。")) await mutate("restore", {id:Number(button.dataset.restore)}, button.dataset.restoreScope);
  };
}
function renderModelUsage() {
  const usage = data.auxiliary_usage;
  if (!usage) return "";
  const rows = usage.rows || [];
  const sum = key => rows.reduce((total, row) => total + Number(row[key] || 0), 0);
  const stages = {memory_review:"记忆主动复核",native_reply:"回复正文",planner:"聊天规划",reply_split:"语义断句",emoji_select:"表情选择",person_profile:"人物画像",person_memory:"人物记忆",person_memory_repair:"记忆校验纠正",memory_summary:"记忆整理"};
  return `<details class="card"><summary>最近 24 小时正文与辅助用量 · ${sum("calls")} 次 · 输入 ${(sum("input_cached") + sum("input_other")).toLocaleString()} / 输出 ${sum("output").toLocaleString()} token</summary><p class="muted">${sum("reported")} 次返回 token 用量。未报告的调用只计次数；输入包含缓存 token。正文按每次实际传输统计，包含工具循环和备用模型。Jev 独立接口与嵌入请求另计。</p>${rows.map(row => `<p><strong>${escape(stages[row.stage] || row.stage)}</strong> · ${Number(row.calls)} 次 · 输入 ${(Number(row.input_cached) + Number(row.input_other)).toLocaleString()} / 输出 ${Number(row.output).toLocaleString()} · 错误 ${Number(row.errors)} / 取消 ${Number(row.cancelled)}<br><small class="muted">${escape(row.provider || "未返回模型配置")} · ${escape(row.model || "未返回实际模型名")}</small></p>`).join("") || '<p class="muted">还没有调用记录。</p>'}<p class="muted">本次加载已接入 ${Number(usage.native_bound || 0)} 轮原生正文${usage.native_unavailable ? `，${Number(usage.native_unavailable)} 轮宿主未提供可接入的运行器` : ""}。${usage.pending ? `待入库 ${Number(usage.pending)} 条。` : ""}${usage.dropped ? `队列溢出丢弃 ${Number(usage.dropped)} 条。` : ""}</p></details>`;
}

function renderHistory() {
  const expanded = new Set([...document.querySelectorAll("details[data-review-run][open]")].map(node => node.dataset.reviewRun));
  const legacyKeys = {learning:"runs", participation:"decisions", replies:"turn_traces", applications:"applications", versions:"revisions"};
  const page = data.history?.items && data.history.section === historySection ? data.history : null;
  const items = page?.items || (data[legacyKeys[historySection]] || []).slice(0,10);
  const counts = page?.counts || Object.fromEntries(Object.entries(legacyKeys).map(([key,value]) => [key,(data[value] || []).length]));
  const total = page?.total ?? items.length, offset = page?.offset ?? 0;
  const searchHints = {learning:"搜索学习消息", participation:"搜索参与理由", replies:"搜索回复详情", applications:"搜索习惯编号或模式", versions:"搜索操作类型"};
  $("#content").innerHTML = `${renderModelUsage()}<section class="history-workspace"><header class="history-heading"><div><h2>学习与参与</h2><p class="muted">${total} 条记录</p></div><button id="history-latest" type="button" class="tonal">查看最新</button></header><nav class="history-categories" aria-label="记录类型">${Object.entries(historySections).map(([key,label]) => `<button type="button" data-history-section="${key}" aria-pressed="${key === historySection}">${label}<span>${counts[key] || 0}</span></button>`).join("")}</nav><form id="history-search" class="history-search" role="search" aria-label="搜索记录"><div class="filter-field"><label for="history-query">搜索</label><input id="history-query" type="search" maxlength="200" autocomplete="off" placeholder="${escape(searchHints[historySection])}" value="${escape(historySearchDraft)}"></div><button type="submit" class="primary">搜索</button><button type="button" id="history-clear" ${historySearchDraft || historyQuery ? "" : "disabled"}>清空</button><label class="history-status-field">状态<select id="history-status"><option value="all">全部状态</option>${Object.entries(historyStatuses[historySection]).map(([key,label]) => `<option value="${key}" ${historyStatus === key ? "selected" : ""}>${label}</option>`).join("")}</select></label></form><div class="history-records">${items.length ? items.map(row => {
    const state = historySection === "participation" ? row.outcome : row.status;
    const label = historyStatuses[historySection][state] || (historySection === "versions" ? "版本" : historySection === "applications" ? "引用" : state || "记录");
    const tone = ["error","cancelled","empty_response"].includes(state) ? "error" : ["running","waiting"].includes(state) ? "pending" : ["success","admitted","send_hook"].includes(state) ? "success" : "";
    const title = historySection === "learning" ? row.message || "正在分析本批素材…" : historySection === "participation" ? row.reason : historySection === "replies" ? `${row.target_sender || row.details?.target_sender || "当前成员"} · ${({at:"@ 唤醒",quote:"引用唤醒",name:"名称唤醒",autonomous:"自主参与",native:"原生回复"})[row.route || row.details?.route] || "回复记录"}` : historySection === "versions" ? actions[row.action] || row.action : `引用 ${row.trait_count ?? row.trait_ids?.length ?? 0} 条习惯`;
    const inline = row.review || row.details || row.trait_ids || historySection === "participation" || historySection === "versions" ? row : historyDetails.get(historyDetailKey(row))?.data;
    const open = Number(row.id) === historyOpenId || expanded.has(String(row.id));
    return `<details class="history-record" data-history-id="${row.id}" ${historySection === "learning" ? `data-review-run="${row.id}"` : ""} ${open ? "open" : ""}><summary><time>${date(row.started || row.created)}</time><span class="badge ${tone}">${escape(label)}</span><span class="history-record-title">${escape(title)}${historySection === "learning" && row.flagged ? `<small>${row.flagged} 条自检存疑</small>` : ""}</span></summary><div class="history-record-detail">${inline ? historyDetail(historySection, inline) : '<p class="muted">展开后读取详情。</p>'}</div></details>`;
  }).join("") : empty(historyQuery ? "未找到匹配记录" : "没有符合条件的记录", historyQuery ? "换关键词，或清空搜索后再试。" : "切换类型或状态，或等待下一次学习与回复。")}</div><footer class="history-pagination"><span class="muted">${total ? `${offset + 1}–${Math.min(offset + 10,total)} / ${total} 条` : "0 条记录"}</span><div class="actions"><button id="history-prev" ${offset ? "" : "disabled"}>上一页</button><button id="history-next" ${offset + 10 < total ? "" : "disabled"}>下一页</button></div></footer></section>`;
  const runHistorySearch = async value => {
    const next = value.trim();
    if (next === historyQuery) { historySearchDraft = next; $("#history-query").value = next; $("#history-clear").disabled = !next; return; }
    await readView(async () => {
      const previous = [historyQuery, historyOffset, historyAnchor, historyOpenId];
      historyQuery = historySearchDraft = next; historyOffset = historyAnchor = 0; historyOpenId = null;
      try { await load(); }
      catch (error) { [historyQuery, historyOffset, historyAnchor, historyOpenId] = previous; historySearchDraft = historyQuery; renderHistory(); throw error; }
    });
  };
  $("#history-search").onsubmit = event => { event.preventDefault(); return runHistorySearch($("#history-query").value); };
  $("#history-clear").onclick = () => runHistorySearch("");
  $("#history-query").oninput = event => { historySearchDraft = event.target.value; $("#history-clear").disabled = !historySearchDraft && !historyQuery; };
  for (const button of document.querySelectorAll("[data-history-section]")) button.onclick = async () => {
    historySection = button.dataset.historySection; historyStatus = "all"; historyQuery = historySearchDraft = ""; historyOffset = historyAnchor = 0; historyOpenId = null;
    await readView(() => load());
  };
  $("#history-status").onchange = async event => {
    historyStatus = event.target.value; historyOffset = historyAnchor = 0; historyOpenId = null;
    await readView(() => load());
  };
  $("#history-latest").onclick = async () => { historyOffset = historyAnchor = 0; historyOpenId = null; historyDetails.clear(); await readView(() => load()); };
  for (const [id,step] of [["#history-prev",-10],["#history-next",10]]) $(id).onclick = async () => {
    historyOffset = Math.max(0,offset + step); historyOpenId = null; await readView(() => load());
  };
  for (const node of document.querySelectorAll("details[data-history-id]")) node.ontoggle = async () => {
    const id = Number(node.dataset.historyId);
    if (!node.open) { if (historyOpenId === id) historyOpenId = null; return; }
    historyOpenId = id; loadVersion++;
    for (const other of document.querySelectorAll("details[data-history-id]")) if (other !== node) other.open = false;
    const item = items.find(row => row.id === id), key = historyDetailKey(item);
    const scope = selectedScope, section = historySection;
    const body = node.querySelector(".history-record-detail");
    const cached = historyDetails.get(key);
    if (cached && Date.now() - cached.at < 30000) { body.innerHTML = historyDetail(section,cached.data); bindHistoryRestore(body); return; }
    body.innerHTML = '<p class="muted" role="status">正在读取这条记录…</p>';
    try {
      const result = await api("history/detail", undefined, {scope, section, id});
      if (tab !== "history" || scope !== selectedScope || section !== historySection || !node.isConnected) return;
      historyDetails.set(key,{data:result,at:Date.now()});
      while (historyDetails.size > 12) historyDetails.delete(historyDetails.keys().next().value);
      body.innerHTML = historyDetail(section,result); bindHistoryRestore(body);
    } catch (error) { if (node.isConnected) body.textContent = error.message || "详情读取失败，请重新展开或刷新。"; }
  };
  bindHistoryRestore();
}


function bindSettingsTabs(active, memoryPage) {
  const buttons = [...document.querySelectorAll("[data-settings-tab]")];
  const panels = [...document.querySelectorAll("[data-settings-panel]")];
  function select(key, focus = false) {
    for (const button of buttons) {
      const selected = button.dataset.settingsTab === key;
      button.setAttribute("aria-selected", String(selected));
      button.tabIndex = selected ? 0 : -1;
      if (selected && focus) { button.focus(); button.scrollIntoView({block:"nearest",inline:"nearest"}); }
    }
    for (const panel of panels) panel.hidden = panel.dataset.settingsPanel !== key;
    if (memoryPage) memorySection = key; else settingsSection = key;
    renderNavigation();
  }
  for (const [index, button] of buttons.entries()) {
    button.onclick = () => select(button.dataset.settingsTab);
    button.onkeydown = event => {
      const next = {ArrowRight:(index + 1) % buttons.length, ArrowLeft:(index + buttons.length - 1) % buttons.length, Home:0, End:buttons.length - 1}[event.key];
      if (next === undefined) return;
      event.preventDefault();
      select(buttons[next].dataset.settingsTab, true);
    };
  }
  $("#settings-form").addEventListener("invalid", event => {
    const panel = event.target.closest("[data-settings-panel]");
    if (panel) select(panel.dataset.settingsPanel);
  }, true);
  select(active);
}

function renderMemory() {
  window.OvenHeartMemory.render({
    root: $("#content"), data, api, escape, notify, confirmAction,
    renderSettings: () => renderSettings("#memory-body"),
    isDirty: () => dirty, editVersion: () => editVersion,
    clearDirty: () => { dirty = false; },
  });
}

function renderSettings(target = "#content") {
  const s = data.settings;
  const settingsRevision = data.settings_revision;
  const memoryPage = tab === "memory";
  const tabs = memoryPage ? {core:"通用与图片", background:"经历与人物"} : {learning:"学习与生效", participation:"群聊参与", expression:"表达与检索", maintenance:"调试与维护"};
  const active = memoryPage ? memorySection : settingsSection;
  const review = Object.values(data.memory_review?.rooms || {}).sort((a,b) => b.attempted-a.attempted)[0];
  const reviewStatus = {kept:"无需修改，原记录保留",corrected:"已按新证据修正，可在修正历史回滚",needs_confirmation:"有复核建议待确认，请查看修正历史",up_to_date:"已检查，没有需要复核的新内容",paused:"设置已变化，本次未自动执行",failed:"复核未完成，原记录保留，将按间隔重试",running:"正在复核"}[review?.status] || "等待检查";
  $(target).innerHTML = `<div class="settings-heading"><h2>${memoryPage ? "记忆设置" : "设置"}</h2><p>${memoryPage ? "范围、图片与后台整理；人格之间始终分开。" : "按任务调整。未保存修改可在页内换标签。"}</p></div>
    <form id="settings-form"><div class="settings-tabs" role="tablist" aria-label="${memoryPage ? "记忆分类" : "设置分类"}">${Object.entries(tabs).map(([key,label]) => `<button type="button" id="settings-tab-${key}" role="tab" aria-controls="settings-panel-${key}" aria-selected="${key === active}" tabindex="${key === active ? 0 : -1}" data-settings-tab="${key}">${label}</button>`).join("")}</div>
    ${memoryPage ? `<div class="settings-panel settings-grid" id="settings-panel-core" role="tabpanel" aria-labelledby="settings-tab-core" data-settings-panel="core">
<section class="settings-group"><h2>通用与图片记忆</h2><p>选择记忆的使用范围，管理通用记忆与图片。</p>
      ${switchField("memory_enabled", "参考长期记忆", "控制学习和回复时是否参考记忆；关闭后已有记忆仍保留。", s.memory_enabled)}
      ${switchField("memory_isolation", "按会话隔离记忆（推荐）", "开启：只召回当前会话的记忆。关闭：同一人格召回全部会话的记忆，包括切换前已有资料；私聊内容也可能在群聊被回忆。记录保留原始来源，不搬移或清空，人物仍按真实账号区分，聊天历史与习惯范围不受影响。", s.memory_isolation)}
      ${switchField("native_memory_enabled", "A_memorix 通用记忆", "保存有真实来源的段落和关系，遵循上方记忆范围；使用原版 BM25、图谱与双路检索，配置嵌入模型后启用双向量池。", s.native_memory_enabled)}
      ${switchField("image_memory_enabled", "图片记忆", "保存当前会话中真实出现的图片、作者原话和模型观察；图片描述沿用宿主的图片描述模型，暂停学习后不再入库。", s.image_memory_enabled)}
      ${field("image_embedding_provider_id", "图片嵌入模型 ID", s.image_embedding_provider_id ?? "", "AstrBot 的 DashScope 或硅基流动多模态嵌入提供商 ID；留空仍可回忆同一图片。", {type:"text"})}
      ${switchField("memory_feedback_enabled", "根据后续反馈维护记忆", "跟进已召回记忆的实际后续反馈，按原版窗口和置信度判断是否修正。暂停学习时停止更新，调用计入辅助用量。", s.memory_feedback_enabled)}
<div class="number-fields">${field("memory_top_k", "最多参考记忆", s.memory_top_k, "条；仅在学习时检索", {type:"number", min:1, max:8})}${field("memory_runtime_max_rooms", "同时驻留的记忆会话", s.memory_runtime_max_rooms ?? 4, "个；超出时关闭不活跃会话，记忆保留，下次访问自动加载", {type:"number", min:1, max:16})}</div>
<h3>检索模型</h3><p class="muted">以下两项与表达检索共用，修改后两处同步生效。</p>
      ${field("embedding_provider_id", "向量模型 ID", s.embedding_provider_id, "用于回复、参与判断和旧习惯对照，生效习惯会做有界后台预热。填写 AstrBot 的 embedding 提供商 ID；留空用文字检索。", {type:"text"})}
<div class="number-fields">${field("retrieval_timeout", "召回等待上限", s.retrieval_timeout, "推荐 30 秒；留足完整召回时间，情境筛选另计", {type:"number", min:1, max:120})}</div>
</section>
</div>
<div class="settings-panel settings-grid" id="settings-panel-background" role="tabpanel" aria-labelledby="settings-tab-background" data-settings-panel="background">
<section class="settings-group"><h2>主动复核</h2><p>轮流检查最久未复核和与新记忆相关的内容。有新原文证据、把握足够的更正自动执行，其余建议在「记忆检修 → 修正历史」确认；可查看依据和回滚。</p>
      ${switchField("memory_review_enabled", "主动复核记忆（推荐）", "默认全插件每小时最多一轮，每轮最多检查两条；复用原版修正机制，会产生辅助模型用量。没有足够记忆时不调用，暂停学习后也停止。不会仅因年代久而删除记录。", s.memory_review_enabled)}
      ${field("memory_review_interval", "主动复核间隔", s.memory_review_interval ?? 3600, "秒；全插件共用间隔，各记忆库轮流检查，推荐 3600。", {type:"number",min:300,max:604800})}
      <p class="muted">${data.memory_review?.busy ? "正在复核；原记录在建议获准前继续保留。" : data.memory_review?.latest ? `最近尝试：${escape(date(data.memory_review.latest))} · ${escape(reviewStatus)}。` : "还没有复核记录。积累至少两条记忆后按间隔检查。"}</p>
</section>
<section class="settings-group"><h2>经历整理与人物画像</h2><p>分别控制聊天回想、群聊经历与人物信息的整理节奏。</p>
      ${switchField("memory_summary_enabled", "自动整理群聊经历", "累计新素材后生成增量摘要和可检索的情景；空增量不会反复整理。暂停学习时停止更新，已有记忆继续可查。", s.memory_summary_enabled)}
      ${switchField("midterm_memory_enabled", "聊天回想", "把离开短期上下文的发言压缩成近期回想，在话题相关时想起来。原生模式复用宿主消息历史；暂停学习后停止更新。", s.midterm_memory_enabled)}
      ${field("midterm_memory_limit", "聊天回想保留数", s.midterm_memory_limit ?? 10, "当前原生会话参与语义匹配的近期摘要数", {type:"number",min:1,max:50})}
      <div class="number-fields">${field("memory_summary_min_messages", "群聊摘要最少新发言", s.memory_summary_min_messages ?? 12, "条；每批最多50条", {type:"number",min:1,max:50})}${field("memory_summary_interval", "群聊记忆整理间隔", s.memory_summary_interval ?? 600, "秒；摘要与情景整理共用尝试间隔", {type:"number",min:0,max:86400})}</div>
      ${field("memory_summary_provider_id", "群聊记忆整理模型 ID", s.memory_summary_provider_id ?? "", "留空使用学习模型或当前会话模型；后台调用计入辅助用量。", {type:"text"})}
      ${switchField("person_memory_enabled", "人物记忆与画像", "按真实账号保存有原文来源的事实，遵循所选记忆范围；冲突保留待更正，不确定信息不当作稳定了解。暂停学习时停止更新。", s.person_memory_enabled)}
      <div class="number-fields">${field("person_memory_min_messages", "人物记忆最少新发言", s.person_memory_min_messages ?? 6, "条；同一成员的新素材达到门槛后后台提取", {type:"number",min:1,max:30})}${field("person_memory_interval", "人物记忆更新间隔", s.person_memory_interval ?? 600, "秒；每位成员的自动提取间隔", {type:"number",min:0,max:86400})}</div>
      ${field("person_memory_provider_id", "人物记忆提取模型 ID", s.person_memory_provider_id ?? "", "留空使用学习模型或当前会话模型。", {type:"text"})}
</section>
</div>
<div class="settings-panel settings-grid" id="settings-panel-legacy" role="tabpanel" aria-labelledby="settings-tab-legacy" data-settings-panel="legacy">

</div>` : `<div class="settings-panel settings-grid" id="settings-panel-learning" role="tabpanel" aria-labelledby="settings-tab-learning" data-settings-panel="learning">
<section class="settings-group"><h2>学习与生效</h2><p>可以先审核，也可以让证据充足的结果自动加入习惯。</p>
      ${switchField("enabled", "启用人格工坊", "关闭后停止采集、学习和应用", s.enabled)}${switchField("learning_enabled", "启用素材采集与后台学习", "关闭后继续保留并应用已生效的习惯", s.learning_enabled)}${switchField("apply_enabled", "应用人格与学习结果", "可先积累，准备好后再启用", s.apply_enabled)}
      <div class="field"><label for="activation_mode">新结果的生效方式</label><select id="activation_mode" name="activation_mode"><option value="review" ${s.activation_mode === "review" ? "selected" : ""}>先审核后生效</option><option value="auto" ${s.activation_mode === "auto" ? "selected" : ""}>高置信度自动生效</option></select><small>影响新结果与尚未人工决定的条目；人工审核、拒绝和停用状态保持不变。</small></div>
      ${switchField("learning_review_enabled", "学习结果入库自检", "用学习模型核对原文与使用条件。未通过的新候选转待审，未通过的更新不改旧条目；自检失败保留整批素材。模型阶段合计最多 360 秒，提取最多 240 秒，为自检预留 120 秒。", s.learning_review_enabled)}
      <div class="number-fields">${field("auto_confidence", "自动生效置信度", s.auto_confidence, "由模型评估，不等同于统计概率。", {type:"number", min:.5, max:1, step:.01})}${field("min_evidence", "每条最少证据数", s.min_evidence, "不足时不保存学习条目。", {type:"number", min:1, max:10})}</div>
      ${Object.entries(kinds).map(([key, label]) => switchField(`learn_${key}`, `学习${label}`, "", s[`learn_${key}`])).join("")}
    </section>
<section class="settings-group"><h2>素材与节奏</h2><p>只采集收到的文字消息，跳过机器人自己和命令。采集本身不会触发回复；是否自主参与由参与设置决定。</p>
      <div class="number-fields">${field("min_messages", "自动学习素材门槛", s.min_messages, "条消息", {type:"number", min:4, max:100})}${field("batch_size", "每批最多素材", s.batch_size, "条消息", {type:"number", min:4, max:100})}${field("interval_seconds", "每会话学习间隔", s.interval_seconds, "秒；同时作为失败重试冷却", {type:"number", min:30, max:86400})}${field("retention_days", "原始素材保留时间", s.retention_days, "天；含明确引用，证据摘录随条目保留", {type:"number", min:1, max:90})}${field("max_messages_per_scope", "每会话素材上限", s.max_messages_per_scope, "条；超出后淘汰最旧素材", {type:"number", min:100, max:1000})}</div>
      ${field("provider_id", "学习模型 ID", s.provider_id, "留空使用该会话的当前模型。填写 AstrBot 中已配置的模型提供商 ID。", {type:"text"})}
    </section>
<section class="settings-group"><h2>学习范围</h2><p>默认所有会话共用人格底稿、人格、表达与行为习惯。群聊词汇始终按来源会话与人格隔离；素材与真实聊天上下文也保留来源边界。AstrBot 的会话开关、插件开关与白名单始终有效。</p>${switchField("session_isolation", "启用会话隔离", "开启后，人格底稿和其他三类学习也按会话与人格分别设置、学习和应用。群聊词汇始终隔离。切换保留数据，进行中的旧学习不会跨模式提交。", s.session_isolation)}
      ${field("target_senders", "只向这些用户学习", s.target_senders.join("\n"), "每行一个用户 ID。留空学习所有人；指定后，旁人的邻近发言仅短暂用于理解语境，不入素材库或证据。", {maxlength:16000})}
      ${field("blocked_senders", "不学习的用户（QQ 号）", (s.blocked_senders || []).join("\n"), "直接填写 QQ 号即可，每行一个，也支持空格、逗号分隔。黑名单优先于学习对象；不采集、不学习，也不作为学习上下文。已学到的习惯保留，可在学习库手动停用。", {maxlength:128000})}
      ${field("learning_blocked_groups", "学习黑名单群聊", (s.learning_blocked_groups || []).join("\n"), "填群号或完整会话标识，每行一个。停止该群素材采集和学习，已有素材保留；聊天、参与判断和已有习惯继续生效。", {maxlength:128000})}
      ${field("allowed_sessions", "允许学习与应用的会话", s.allowed_sessions.join("\n"), "每行一个完整 unified_msg_origin，可通过 AstrBot 的 /sid 获取。留空允许全部已启用会话。", {maxlength:32000})}
    </section>
</div>
<div class="settings-panel settings-grid" id="settings-panel-participation" role="tabpanel" aria-labelledby="settings-tab-participation" data-settings-panel="participation">
<section class="settings-group"><h2>群聊上下文</h2><div class="field"><label for="context_mode">群聊历史由谁管理</label><select id="context_mode" name="context_mode" aria-describedby="context_mode-help"><option value="native" ${s.context_mode === "native" ? "selected" : ""}>AstrBot 管理（推荐）</option><option value="structured" ${s.context_mode === "structured" ? "selected" : ""}>烤箱之心管理（进阶）</option></select><small id="context_mode-help">推荐「AstrBot 管理」，无需改动其他设置。希望烤箱之心统一整理群聊成员、引用和图片上下文时，再选「烤箱之心管理」；切换前先关闭 AstrBot 的群聊上下文感知与原生主动回复，否则不会接管。切换不会清空聊天记录。</small></div></section>
<section class="settings-group mention-settings"><h2>提及时马上回复</h2><div class="two-col"><div><p>在群聊正文任意位置叫到名字或别名，就交给 AstrBot 正常回复。@ 和引用机器人的消息由宿主原生处理。</p>${switchField("mentioned_bot_reply", "启用提及回复", "独立于自主参与模式；跳过最少新消息、连续输入等待、冷却、每小时上限和决策模型。仍遵守宿主权限、限流与内容检查，沿用宿主工具权限。", s.mentioned_bot_reply)}</div><div>${field("mention_names", "机器人名字与别名", (s.mention_names || []).join("\n"), "每行一个，例如小夏、夏夏。区分大小写；留空不新增名字唤醒。不匹配引用、转发正文或 @他人的显示名；避免过短或常用词。", {maxlength:128000})}</div></div></section>
<section class="settings-group participation-settings"><h2>自主参与群聊</h2><p>根据最近聊天与已生效的习惯，决定参与、等待或旁观。直接唤醒、私聊和命令继续按宿主原有规则处理。自动回复使用 AstrBot 本地 Agent 运行器。</p><div class="two-col"><div>
      <div class="field"><label for="participation_mode">参与方式</label><select id="participation_mode" name="participation_mode"><option value="off" ${s.participation_mode === "off" ? "selected" : ""}>关闭自主参与</option><option value="observe" ${s.participation_mode === "observe" ? "selected" : ""}>仅观察，记录决策</option><option value="auto" ${s.participation_mode === "auto" ? "selected" : ""}>自动参与，交由宿主回复</option></select><small>建议先用「仅观察」检查决策。记录在「学习与参与」中。</small></div>
      <div class="number-fields">${field("participation_min_messages","最少新消息",s.participation_min_messages,"条；十分钟内保留最多 30 条上下文",{type:"number",min:1,max:30})}${field("participation_debounce","判断前输入合并",s.participation_debounce,"秒；尚未发请求时合并连续输入",{type:"number",min:0,max:10})}${field("participation_silence_seconds","决定参与后的静默",s.participation_silence_seconds ?? 8,"秒；固定窗口收集新消息，到期统一判断；无新输入不重复请求，0 为即时模式",{type:"number",min:0,max:120})}${field("participation_cooldown","回复冷却",s.participation_cooldown,"秒；直接唤醒的回复也会推迟自主参与",{type:"number",min:5,max:3600})}${field("participation_hourly_limit","每小时参与上限",s.participation_hourly_limit,"次；同群的隔离会话共享限额",{type:"number",min:1,max:120})}</div>
      <div class="field"><label for="planner_mode">聊天规划</label><select id="planner_mode" name="planner_mode"><option value="maibot" ${s.planner_mode === "maibot" ? "selected" : ""}>MaiBot 持续工具规划</option><option value="legacy" ${s.planner_mode === "legacy" ? "selected" : ""}>Jev / JSON 单次判读</option></select><small>持续规划支持记忆查询、回复结果回流、等待恢复和结束参与；由决策模型规划，Jev 继续辅助表达筛选。固定静默收集仅用于单次判读方式。</small></div>
      <div class="field"><label for="participation_rhythm">参与节奏</label><select id="participation_rhythm" name="participation_rhythm"><option value="maibot" ${s.participation_rhythm === "maibot" ? "selected" : ""}>MaiBot 动态节奏</option><option value="fixed" ${s.participation_rhythm === "fixed" ? "selected" : ""}>固定门槛与冷却</option></select><small>动态模式使用 MaiBot 原版评分与频率反馈，不叠加固定消息数、判断间隔和冷却；权限与每小时上限继续生效。</small></div>
      ${field("participation_frequency", "动态回复频率", s.participation_frequency ?? .5, "0停止普通自主参与，1允许每批进入规划；不是随机回复概率。仅动态节奏生效。", {type:"number",min:0,max:1,step:.05})}
      ${switchField("participation_followup", "允许连续接话", "刚回复的成员在 120 秒内继续说话时，允许一次跳过新消息门槛与冷却；中间夹有其他成员发言也能衔接。续接收集窗口最多 2 秒，仍判断是否需要回应，保留每小时限额。", s.participation_followup)}
      </div><div><details><summary>决策模型与工具</summary><p>持续规划使用这里的模型 ID 和超时；仅原判断方式下，Jev 主动参与会接管判断。</p>${field("planner_provider_id","参与决策模型 ID",s.planner_provider_id,"留空使用学习模型或当前会话模型。",{type:"text"})}<div class="number-fields">${field("planner_interval_seconds","普通群聊判断间隔",s.planner_interval_seconds ?? 60,"秒；减少重复旁观判断，静默窗口内集中处理新消息",{type:"number",min:5,max:300})}${field("planner_timeout","决策请求超时",s.planner_timeout,"秒；异常时保持旁观",{type:"number",min:3,max:90})}${field("planner_reply_timeout","原生回复等待上限",s.planner_reply_timeout ?? 120,"秒；超时取消该轮，不自动重发；长任务可调高",{type:"number",min:30,max:600})}${field("planner_reply_threshold","最低参与把握",s.planner_reply_threshold,"由模型评估，不等同于统计概率",{type:"number",min:.5,max:1,step:.01})}${field("planner_max_wait","单次最长等待",s.planner_max_wait,"秒；持续规划在超时或新输入后继续判断",{type:"number",min:1,max:120})}</div></details><div id="autonomous-tool-picker"></div></div></div>
    </section>
<section class="settings-group"><h2>Jev 决策与学习核验</h2><p>接口地址、模型名和 API Key 在 AstrBot 原生插件配置的「Jev / TypeSafe 连接」填写。烤箱之心独占主动回复；意图与情绪由 Oven Multi 的独立开关管理。</p>
      <p class="muted">${data.jev?.configured ? "已配置原生连接。" : "尚未填写 API Key；启用 Jev 参与后将暂时旁观，学习核验沿用原流程。"}</p>
      <p class="muted">本次加载以来：请求 ${Number(data.jev?.transport?.requests || 0)} 次 · 缓存 ${Number(data.jev?.transport?.cache_hits || 0)} 次 · 并发合并 ${Number(data.jev?.transport?.coalesced || 0)} 次 · 输入 ${Number(data.jev?.transport?.input_tokens || 0)} / 输出 ${Number(data.jev?.transport?.output_tokens || 0)} token。仅统计 Jev 已报告用量。</p>
      ${switchField("jev_enabled", "启用 Jev", "主动参与启用后替代主模型规划；不可用时本轮旁观。固定模板为英文，原文保留原语言。", s.jev_enabled)}
      ${switchField("jev_participation_enabled", "主动参与判断", "独立选择回复目标、等待或旁观，不再调用主模型参与规划；明确 @ 与名字唤醒仍直接回复。", s.jev_participation_enabled)}
      ${switchField("jev_learning_enabled", "学习置信度核验", "按原始证据独立核验，只能降低候选置信度；保留现有自检与人工审核。", s.jev_learning_enabled)}
      <div class="number-fields">${field("jev_reply_threshold", "主动回复门槛", s.jev_reply_threshold, "P(应回复)，由 Jev 判断，不等同于校准后的事实概率。", {type:"number",min:.1,max:1,step:.05})}${field("jev_timeout_sec", "调用超时", s.jev_timeout_sec, "秒；参与决策超时旁观，学习核验超时沿用原学习流程。", {type:"number",min:1,max:20})}${field("jev_max_state_chars", "参与语境上限", s.jev_max_state_chars, "字符；优先保留目标，省略更旧消息。", {type:"number",min:1500,max:16000})}</div>
    </section>
</div>
<div class="settings-panel settings-grid" id="settings-panel-expression" role="tabpanel" aria-labelledby="settings-tab-expression" data-settings-panel="expression">
<section class="settings-group"><h2>表达与回复</h2><p>调整表情、回复节奏和本轮习惯的使用方式。</p>
      <p class="muted">表情图库、导入与自动发送已集中到侧栏的“表情”页；本页管理文字分段和表达参考。</p>
      ${switchField("reply_rhythm_enabled", "分段回复与发送节奏", "首段直接发送，后续段落再模拟打字；支持普通模型回复与Planner正文。宿主开启分段时完全沿用宿主。", s.reply_rhythm_enabled)}
      <div class="field"><label for="reply_split_mode">断句方式</label><select id="reply_split_mode" name="reply_split_mode"><option value="rule" ${s.reply_split_mode !== "llm" ? "selected" : ""}>本地规则（推荐，无额外模型调用）</option><option value="llm" ${s.reply_split_mode === "llm" ? "selected" : ""}>模型语义（额外调用，最多等待设定秒数）</option></select><small>初次安装保持本地规则即可：按 AstrBot 分段词与换行切分，保留原文标点。代码块与列表整条发送，颜文字和括号内部不切分、外部正常分段；流式与图文混排沿用原发送方式。</small></div>
      <div class="number-fields">${field("reply_split_max_messages", "每次正文最多分段数", s.reply_split_max_messages ?? 3, "超过时按原版规则合并", {type:"number",min:1,max:10})}${field("reply_typing_speed", "模拟打字时间倍率", s.reply_typing_speed ?? 0.25, "0关闭等待；首段不等待，后续每段最多5秒", {type:"number",min:0,max:3,step:0.05})}</div>
      <div class="number-fields">${field("reply_split_provider_id", "语义断句模型 ID", s.reply_split_provider_id ?? "", "仅模型语义模式使用；推荐填写 AstrBot 已配置的快速模型 ID。留空使用Planner或当前模型，计入语义断句用量。", {type:"text"})}${field("reply_split_timeout", "语义断句等待上限", s.reply_split_timeout ?? 3, "秒；独立于Planner，失败或超时使用本地规则结果。", {type:"number",min:1,max:10})}</div>
</section>
<section class="settings-group"><h2>习惯检索与上下文</h2><p>控制每轮选用的习惯、群聊参考窗口与补充提示。</p>
      <div class="number-fields">${field("max_traits", "每次最多应用习惯", s.max_traits, "条；所有检索模式统一上限，可少选或不选", {type:"number", min:1, max:24})}${field("prompt_budget", "补充内容字数上限", s.prompt_budget, "字符；包含人工底稿", {type:"number", min:500, max:8000})}</div>
      <div class="number-fields">${field("reply_context_messages", "额外群聊消息上限", s.reply_context_messages, "条；0 不附带额外窗口", {type:"number", min:0, max:20})}${field("reply_context_budget", "额外群聊参考字符预算", s.reply_context_budget, "字符；含消息字段与引用，0 不附带窗口", {type:"number", min:0, max:24000})}</div><p class="muted">两项同时约束直接与自主回复的额外群聊窗口，优先保留最近消息；保留当前目标、必要引用、连续补充与宿主原生历史。不是整个请求的字数或 token 上限，自定义模板重复展开另计。</p>
      <div class="field"><label for="guidance_delivery">如何让模型使用人格与习惯</label><select id="guidance_delivery" name="guidance_delivery" aria-describedby="guidance_delivery-help"><option value="cached" ${s.guidance_delivery === "cached" ? "selected" : ""}>默认投递（推荐）</option><option value="system" ${s.guidance_delivery === "system" ? "selected" : ""}>系统投递（特殊接口使用）</option><option value="dual" ${s.guidance_delivery === "dual" ? "selected" : ""}>双重投递（排查人设不生效）</option></select><small id="guidance_delivery-help">推荐「默认投递」，日常使用无需改动。只有模型明显不按人设说话时，再尝试「双重投递」，它会增加输入长度。「系统投递」仅适用于明确要求补充全部放在系统提示词的接口。这些选项都不会修改你填写的人格内容。</small></div>
      <div class="field"><label for="selection_mode">已学表达的选择方式</label><select id="selection_mode" name="selection_mode" aria-describedby="selection_mode-help"><option value="contextual" ${s.selection_mode === "contextual" ? "selected" : ""}>结合当前对话（推荐）</option><option value="hybrid" ${s.selection_mode === "hybrid" ? "selected" : ""}>按意思匹配（需向量模型）</option><option value="lexical" ${s.selection_mode === "lexical" ? "selected" : ""}>按字词匹配（最省资源）</option></select><small id="selection_mode-help">推荐「结合当前对话」，避免把相关但不合时宜的表达硬套进回复。已配置向量模型、只需按含义查找时可选「按意思匹配」；希望少用资源、不配置向量模型时选「按字词匹配」。未配置向量模型时会使用文字匹配。</small></div>
      ${field("embedding_provider_id", "向量模型 ID", s.embedding_provider_id, "用于回复、参与判断和旧习惯对照，生效习惯会做有界后台预热。填写 AstrBot 的 embedding 提供商 ID；留空用文字检索。", {type:"text"})}
      ${field("rerank_provider_id", "重排序模型 ID", s.rerank_provider_id, "可选，填写 AstrBot 中已配置的 rerank 提供商 ID。只改变候选顺序，不能代替情境判断。", {type:"text"})}
      ${field("selector_timeout", "情境筛选等待上限", s.selector_timeout, "秒；异常时不选表达习惯，仍保留当前输入直接命中的本地词义。", {type:"number", min:3, max:30})}
      <div class="number-fields">${field("retrieval_timeout", "召回等待上限", s.retrieval_timeout, "推荐 30 秒；留足完整召回时间，情境筛选另计", {type:"number", min:1, max:120})}${field("semantic_threshold", "语义相关性门槛", s.semantic_threshold, "余弦相似度，按模型效果调整", {type:"number", min:0, max:1, step:.01})}${field("habit_half_life", "习惯新鲜度半衰期", s.habit_half_life, "天；逐渐降权，不自动删除", {type:"number", min:7, max:730})}</div>
      <p class="muted">${escape(data.retrieval?.message || "真实回复后显示检索状态。")}</p>
</section>
</div>
<div class="settings-panel settings-grid" id="settings-panel-maintenance" role="tabpanel" aria-labelledby="settings-tab-maintenance" data-settings-panel="maintenance">
<section class="settings-group debug-settings"><h2>调试日志</h2><p>在 AstrBot 日志中搜索「OvenHeart debug」查看本插件的处理过程。</p>${switchField("debug_enabled", "启用 Debug 模式", "完整输出实际注入内容、学习请求与响应、自检和检索筛选结果。包含聊天、人格和记忆摘录；仅在调试时开启。不会输出模型配置密钥或 HTTP 鉴权头。", s.debug_enabled)}</section>
<section class="settings-group"><h2>素材维护</h2><p>仅清理当前会话的原始聊天素材，保留人格、学习条目与证据摘录。</p>${data.scope && data.scope.id !== data.shared_scope?.id ? '<button type="button" id="clear-messages" class="danger">清除当前会话的原始素材</button><p class="muted">清除后无法恢复原始素材。</p>' : '<p class="muted">选择具体会话后可清理原始素材。</p>'}</section>
</div>`}
    <div class="form-end"><button class="primary" type="submit">${memoryPage ? "保存记忆设置" : "保存设置"}</button><span class="muted">保存本页所有标签中的修改，立即生效并同步到 AstrBot 插件配置。</span></div></form>`;
  bindSettingsTabs(active, memoryPage);
  const toolSelection = memoryPage ? () => ({}) : renderAutonomousTools(s);
  const updateSplitControls = () => {
    if (!$("#reply_split_mode")) return;
    const semantic = $("#reply_split_mode").value === "llm";
    $("#field-reply_split_provider_id").disabled = !semantic;
    $("#field-reply_split_timeout").disabled = !semantic;
  };
  if ($("#reply_split_mode")) $("#reply_split_mode").onchange = updateSplitControls;
  updateSplitControls();
  $("#settings-form").onsubmit = event => {
    event.preventDefault();
    const form = event.currentTarget;
    const patch = {};
    for (const [key, value] of Object.entries(s)) {
      const input = form.elements.namedItem(key);
      if (!input) continue;
      patch[key] = typeof value === "boolean" ? input.checked : typeof value === "number" ? Number(input.value) : Array.isArray(value) ? input.value.split(["blocked_senders", "learning_blocked_groups"].includes(key) ? /[\s,，;；]+/ : "\n").map(v => v.trim()).filter(Boolean) : input.value.trim();
    }
    const tools = toolSelection();
    if (!tools) return;
    Object.assign(patch, tools);
    mutate("settings", patch, selectedScope, settingsRevision);
  };
  if ($("#clear-messages")) $("#clear-messages").onclick = async () => { if (await confirmAction("永久清除当前会话所有原始聊天素材？学习条目及其证据摘录会保留。")) mutate("clear_messages"); };
}

function renderAutonomousTools(settings) {
  const selected = {allowlist:new Set(settings.autonomous_tools || []), denylist:new Set(settings.autonomous_tool_denylist || [])};
  if (!toolsCache && data.tools) toolsCache = {value:data.tools, at:Date.now()};
  let inventory = toolsCache?.value || {items:[], message:"正在独立读取工具列表；其他设置可先编辑，已选名称会保留。"};
  let visible = [];
  const container = $("#autonomous-tool-picker");
  container.innerHTML = `<section class="tool-picker" aria-labelledby="tool-picker-title"><h3 id="tool-picker-title">自主回复可用工具</h3><div class="field"><label for="autonomous_tool_policy">工具名单模式</label><select id="autonomous_tool_policy" name="autonomous_tool_policy"><option value="allowlist">白名单：仅允许选中的工具</option><option value="denylist">黑名单：排除选中的工具</option></select><small id="tool-policy-help"></small></div><div class="tool-search-row"><div class="field"><label for="tool-search">搜索 AstrBot 当前工具</label><input id="tool-search" type="search" placeholder="工具名称或功能说明" autocomplete="off"></div><button type="button" id="tool-refresh" class="tonal">刷新工具</button></div><p id="tool-inventory-status" class="muted"></p><p id="tool-selection-count" class="muted" role="status" aria-live="polite"></p><div class="actions tool-bulk"><button type="button" id="tool-select-visible">选择搜索结果</button><button type="button" id="tool-clear-visible">取消搜索结果</button><button type="button" id="tool-clear-all">清空当前名单</button></div><div id="tool-choices" class="tool-choices" role="group" aria-label="自主工具名单"></div><details class="tool-manual"><summary>手动补充工具名称</summary><label for="tool-extra">工具名称</label><textarea id="tool-extra" placeholder="每行一个精确工具名称"></textarea><small>支持换行、空格和逗号。可添加到当前名单，或直接保存设置；尚未注册的名称也会保留。</small><button type="button" id="tool-add-names" class="tonal">添加到当前名单</button></details></section>`;
  const policy = $("#autonomous_tool_policy");
  policy.value = settings.autonomous_tool_policy || "allowlist";
  function renderChoices() {
    const mode = policy.value || "allowlist", chosen = selected[mode];
    const registered = new Map((inventory.items || []).map(item => [item.name,item]));
    const rows = [...registered.values(), ...[...chosen].filter(name => !registered.has(name)).map(name => ({name,description:"尚未注册，名称会保留；不会因此加载或启用工具。",missing:true}))];
    const query = $("#tool-search").value.trim().toLowerCase();
    const matches = rows.filter(item => `${item.name} ${item.description}`.toLowerCase().includes(query));
    visible = matches.map(item => item.name);
    $("#tool-policy-help").textContent = mode === "allowlist" ? "只允许名单内且宿主原本可用的工具。名单为空时仅文字回复。" : "排除名单内工具，其余宿主原本可用的工具都可用于自主回复。黑名单为空时不额外排除。";
    $("#tool-inventory-status").textContent = (inventory.message || "") + (inventory.truncated ? " 当前展示前 1000 个，其他工具可手动补充。" : "");
    $("#tool-selection-count").textContent = `${mode === "allowlist" ? "白名单" : "黑名单"}已选 ${chosen.size} 个 · 当前显示 ${matches.length} 个`;
    $("#tool-select-visible").disabled = $("#tool-clear-visible").disabled = !visible.length;
    $("#tool-choices").innerHTML = matches.length ? matches.map(item => `<label class="tool-option"><input type="checkbox" data-autonomous-tool="${escape(item.name)}" ${chosen.has(item.name) ? "checked" : ""}><span><strong>${escape(item.name)}</strong>${item.missing ? '<small class="tool-state">未注册</small>' : !item.active ? '<small class="tool-state">宿主已停用</small>' : ""}<small>${escape(item.description || "暂无功能说明")}</small></span></label>`).join("") : '<p class="muted tool-empty">没有匹配的工具，可更换关键词或手动补充。</p>';
    for (const input of document.querySelectorAll("[data-autonomous-tool]")) input.onchange = () => {
      if (input.checked) chosen.add(input.dataset.autonomousTool); else chosen.delete(input.dataset.autonomousTool);
      dirty = true;
      $("#tool-selection-count").textContent = `${mode === "allowlist" ? "白名单" : "黑名单"}已选 ${chosen.size} 个 · 当前显示 ${matches.length} 个`;
    };
  }
  policy.onchange = () => { dirty = true; renderChoices(); };
  $("#tool-search").oninput = renderChoices;
  $("#tool-select-visible").onclick = () => { for (const name of visible) selected[policy.value || "allowlist"].add(name); dirty = true; renderChoices(); };
  $("#tool-clear-visible").onclick = () => { for (const name of visible) selected[policy.value || "allowlist"].delete(name); dirty = true; renderChoices(); };
  $("#tool-clear-all").onclick = () => { selected[policy.value || "allowlist"].clear(); dirty = true; renderChoices(); };
  $("#tool-add-names").onclick = () => {
    for (const name of $("#tool-extra").value.split(/[\s,，;；]+/).filter(Boolean)) selected[policy.value || "allowlist"].add(name);
    $("#tool-extra").value = "";
    dirty = true;
    renderChoices();
  };
  const refresh = $("#tool-refresh");
  refresh.onclick = async () => {
    refresh.disabled = true;
    try {
      const next = await api("tools");
      const received = (next.items ? next : next.tools);
      if (received) toolsCache = {value:received, at:Date.now()};
      if ($("#autonomous-tool-picker") !== container) return;
      inventory = received || {items:[], message:"暂时无法读取工具列表，已选名称仍保留。"};
      renderChoices();
    } catch (error) { notify(error.message || "工具列表刷新失败，已选名称仍保留。", true); }
    finally { refresh.disabled = false; }
  };
  renderChoices();
  if (!toolsCache || Date.now() - toolsCache.at >= 30000) Promise.resolve().then(() => { if ($("#autonomous-tool-picker") === container) return refresh.onclick(); });
  return () => {
    for (const name of $("#tool-extra").value.split(/[\s,，;；]+/).filter(Boolean)) selected[policy.value || "allowlist"].add(name);
    if (Object.values(selected).some(names => names.size > 500 || [...names].some(name => name.length > 256))) {
      notify("每份名单最多 500 个名称，每个名称不超过 256 字。", true);
      return null;
    }
    return {autonomous_tool_policy:policy.value || "allowlist", autonomous_tools:[...selected.allowlist].sort(), autonomous_tool_denylist:[...selected.denylist].sort()};
  };
}

function promptPlaceholder(name) {
  return `{{${name}}}`;
}

// Prompt bodies live in their own list because a single wording change applies
// to every conversation; the structured payload is still appended by the plugin.
function renderPrompts() {
  const items = data.prompts?.items || [];
  if (!items.length) {
    $("#content").innerHTML = empty("没有可编辑的提示词", "提示词列表尚未就绪。确认插件已启用，然后刷新本页。");
    return;
  }
  if (!items.some(entry => entry.key === selectedPrompt)) selectedPrompt = items[0].key;
  const item = items.find(entry => entry.key === selectedPrompt);
  const promptsRevision = data.prompts.revision;
  const variables = item.placeholders.length ? item.placeholders.map(p => `<li><code>${escape(promptPlaceholder(p.name))}</code> ${escape(p.description)}</li>`).join("") : "<li>这段提示词不接受变量。</li>";
  const unknown = item.unknown.length ? `<p class="prompt-unknown">未识别的引用：${escape(item.unknown.map(promptPlaceholder).join("、"))}。它们会原样保留，不会被替换。</p>` : "";
  $("#content").innerHTML = `<header class="library-heading"><div><h2>提示词</h2><p class="muted">用 <code>{{变量}}</code> 引用运行时内容；保存立即生效。</p></div></header>
    <form id="prompt-search" class="library-search" role="search" aria-label="搜索提示词"><div class="filter-field"><label for="prompt-query">搜索</label><input id="prompt-query" type="search" maxlength="120" autocomplete="off" placeholder="标题或说明"></div></form>
    <div class="prompts"><div class="trait-list" id="prompt-list">${items.map(entry => `<button type="button" class="trait-row" data-prompt="${escape(entry.key)}" data-prompt-text="${escape(`${entry.title} ${entry.description}`.toLowerCase())}" aria-pressed="${entry.key === item.key}"><span class="row-meta"><span>${escape(entry.title)}</span><span class="badge ${entry.customized ? "active" : ""}">${entry.customized ? "已自定义" : "出厂默认"}</span></span><strong>${escape(entry.description)}</strong></button>`).join("")}</div>
    <div class="prompt-editor"><form id="prompt-form"><div class="field"><label for="field-prompt-body">${escape(item.title)}</label><textarea id="field-prompt-body" name="body" class="large prompt-body" maxlength="8000" spellcheck="false">${escape(item.body)}</textarea><small>${item.body.length} / 8000 字 · 保存后立即生效</small></div>${unknown}<details class="prompt-placeholders"><summary>可用变量</summary><ul>${variables}</ul></details><div class="form-end"><button class="primary" type="submit">保存这段提示词</button><button type="button" class="tonal" id="prompt-reset" ${item.customized ? "" : "disabled"}>恢复默认</button></div></form><details class="prompt-default"><summary>查看出厂默认</summary><pre>${escape(item.default)}</pre></details></div></div>`;
  $("#prompt-query").oninput = event => {
    const needle = event.target.value.trim().toLowerCase();
    for (const button of document.querySelectorAll("#prompt-list [data-prompt]")) {
      button.hidden = Boolean(needle) && !button.dataset.promptText.includes(needle);
    }
  };
  for (const button of document.querySelectorAll("[data-prompt]")) button.onclick = async () => { if (dirty && !await confirmAction("切换提示词会放弃未保存的编辑，继续？")) return; dirty = false; selectedPrompt = button.dataset.prompt; renderPrompts(); };
  $("#prompt-form").onsubmit = event => {
    event.preventDefault();
    const body = $("#field-prompt-body").value;
    if (!body.trim()) return notify("提示词内容不能为空；如需还原，请使用「恢复默认」。", true);
    if (body.length > 8000) return notify("提示词最多 8000 字，请精简后再保存。", true);
    return savePrompts({[item.key]: body}, promptsRevision);
  };
  $("#prompt-reset").onclick = async () => {
    if (await confirmAction(`把「${item.title}」恢复为出厂默认？保存后立即生效，当前措辞不再保留。`)) await savePrompts({[item.key]: null}, promptsRevision);
  };
}

async function savePrompts(patch, revision) {
  const context = writeContext();
  return guarded(async () => {
    const result = await api("prompts/save", {prompts:patch, ...(revision !== undefined ? {revision} : {})});
    notify(result.message || "提示词已保存。");
    await afterWrite(context);
  });
}
async function readView(work) {
  try { await work(); } catch (error) { notify(error.message || "本页读取失败，请刷新重试。", true); }
}

async function guarded(work, event, options = {}) {
  event?.preventDefault();
  if (options.local ? localOperations.has(options.local) : pending) {
    if (!options.local) notify("已有保存请求正在处理，仍可切换页面浏览。");
    return;
  }
  if (options.local) localOperations.add(options.local); else { pending = true; notify("正在提交修改，可以继续浏览其他页面。"); }
  const controls = [...document.querySelectorAll(options.selector || "#content form input, #content form textarea, #content form select, #content button")].filter(el => !el.disabled);
  controls.forEach(el => el.disabled = true);
  try { await work(); }
  catch (error) { notify(error.message || "请求失败，请检查 AstrBot 连接。", true); }
  finally {
    if (options.local) localOperations.delete(options.local); else pending = false;
    controls.forEach(el => { if (el.isConnected) el.disabled = false; });
    if (options.local === "preview-model" && tab === "profile") {
      const button = $("#model-preview");
      button.disabled = !selectedScope || selectedScope === data?.shared_scope?.id;
      button.textContent = "生成示例回复";
    }
    if (data?.scopes && data?.busy && data?.settings) updateHeader();
  }
}

async function mutate(action, payload = {}, scope = selectedScope, settingsRevision) {
  const context = writeContext();
  return guarded(async () => {
    const result = await api("action", {action, scope, payload, ...(action === "profile" ? {effect_scope:scope} : {}), ...(settingsRevision !== undefined ? {settings_revision:settingsRevision} : {})});
    notify(result.message || "已保存。");
    await afterWrite(context);
  });
}

function markDirty(event) {
  if (event.target.id !== "tool-search" && event.target.closest("form") && !["preview-form", "memory-search", "library-search", "history-search", "prompt-search"].includes(event.target.closest("form").id)) { dirty = true; editVersion++; }
}
$("#scope-filter").oninput = event => {
  scopeFilter = event.target.value;
  fillScopeOptions();
};
$("#content").addEventListener("input", markDirty);
$("#content").addEventListener("change", markDirty);
async function navigateView() {
  rememberLibraryPosition();
  const page = tab, scope = selectedScope;
  const cached = viewCache.get(viewKey());
  showView();
  $("#content").scrollIntoView?.({block:"start",behavior:"auto"});
  if (cached && Date.now() - cached.at < 30000) { loadVersion++; return; }
  try {
    await load();
    if (tab === page && selectedScope === scope && !dirty) $("#content").scrollIntoView?.({block:"start",behavior:"auto"});
  } catch (error) { notify(error.message || "本页数据读取失败，请点击刷新重试。", true); }
}
async function changeScope(event) {
  const next = event.target.value; event.target.value = selectedScope;
  if (dirty && !await confirmAction("切换会话会放弃未保存的编辑，继续？")) return;
  selectedScope = next; selectedTrait = null; historyOpenId = null; historyAnchor = historyOffset = 0; dirty = false; await navigateView();
}
$("#scope").onchange = changeScope;
$("#refresh").onclick = async () => {
  if (dirty && !await confirmAction("刷新会放弃未保存的编辑，继续？")) return;
  dirty = false; invalidateViews();
  if (tab === "history") { historyOpenId = null; historyOffset = historyAnchor = 0; historyDetails.clear(); }
  scopeFilter = "";
  try { await load(); } catch (error) { notify(error.message, true); }
};
for (const button of document.querySelectorAll("[data-tab]")) button.onclick = async () => {
  if (dirty && !await confirmAction("离开此页会放弃未保存的编辑，继续？")) return;
  dirty = false; tab = button.dataset.tab; await navigateView();
};
window.addEventListener("beforeunload", event => { if (dirty) { event.preventDefault(); event.returnValue = ""; } });

function schedulePoll(delay) {
  clearTimeout(pollTimer);
  if (!bridge || document.hidden) return;
  const interval = delay ?? (data?.busy?.length ? 8000 : 30000) * Math.min(4, 2 ** pollErrors);
  pollTimer = setTimeout(async () => {
    if (pending || polling || document.hidden) { schedulePoll(); return; }
    polling = true;
    try {
      await load(!data || !dirty && (tab === "library" && document.activeElement?.id !== "library-query" || tab === "history" && document.activeElement?.id !== "history-query" && !historyOffset && !historyOpenId));
      pollErrors = 0;
    } catch (error) { pollErrors = Math.min(2, pollErrors + 1); notify(error.message || "连接已中断，请刷新。", true); }
    finally { polling = false; schedulePoll(); }
  }, interval);
}
document.addEventListener("visibilitychange", () => { if (document.hidden) clearTimeout(pollTimer); else schedulePoll(0); });

async function start() {
  bridge = window.AstrBotPluginView || window.AstrBotPluginPage;
  if (!bridge) {
    notify("未连接 AstrBot Panel。请安装并启用插件，从 Panel 的插件页面打开「人格工坊」。", true);
    $("#scope").innerHTML = '<option value="">请从 AstrBot Panel 打开</option>';
    $("#content").innerHTML = empty("需要从 AstrBot Panel 打开", "此页面使用 AstrBot 的登录与权限，不需要单独启动网页服务。");
    return;
  }
  try {
    const context = await deadline(bridge.ready(), 15000, "等待 AstrBot Panel 连接超时，请刷新重试。");
    const theme = value => { document.documentElement.dataset.theme = value?.isDark ? "dark" : "light"; };
    theme(context); bridge.onContext?.(theme);
    await load();
  } catch (error) { notify(error.message || "无法连接 AstrBot，请刷新 Panel 后重试。", true); }
  schedulePoll();
}
start();
