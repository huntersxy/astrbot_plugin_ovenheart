const $ = (selector) => document.querySelector(selector);
const escape = (value) => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[c]));
const kinds = {personality:"人格倾向", expression:"表达方式", jargon:"群聊词汇", behavior:"行为策略"};
const statuses = {pending:"待审核", active:"已生效", rejected:"已拒绝", disabled:"已停用", success:"学习完成", error:"学习失败", running:"学习中"};
const actions = {profile:"修改人格底稿", trait:"调整学习条目", restore:"恢复历史版本", clear_messages:"清除原始素材", feedback:"调整习惯评价", learning:"合并学习结果"};
const date = value => new Date(value * 1000).toLocaleString("zh-CN", {month:"2-digit", day:"2-digit", hour:"2-digit", minute:"2-digit"});
let libraryScope = "all", libraryOffset = 0;
let bridge, data, selectedScope = "", tab = "profile", selectedTrait = null, category = "all", status = "pending", pending = false, polling = false, dirty = false;

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

async function api(endpoint, body, query = {}) {
  const response = body === undefined ? await bridge.apiGet(endpoint, endpoint === "state" ? {scope:selectedScope} : query) : await bridge.apiPost(endpoint, body);
  if (response?.status === "error") throw new Error(response.message || "请求失败，请刷新页面后重试。");
  return response?.status === "ok" ? response.data : response;
}

async function load(renderContent = true) {
  const scopeAtStart = selectedScope;
  const viewAtStart = JSON.stringify([tab, libraryScope, category, status, libraryOffset]);
  const next = await api("state");
  if (scopeAtStart !== selectedScope || viewAtStart !== JSON.stringify([tab, libraryScope, category, status, libraryOffset])) return;
  if (tab === "library") {
    next.library = await api("library", undefined, {scope:libraryScope, kind:category, status, offset:libraryOffset});
    if (scopeAtStart !== selectedScope || viewAtStart !== JSON.stringify([tab, libraryScope, category, status, libraryOffset])) return;
    libraryOffset = next.library.offset;
  }
  data = next;
  if (!selectedScope && data.scopes.length) {
    selectedScope = data.scopes[0].id;
    const library = data.library;
    data = await api("state");
    data.library = library;
  }
  const scopeSelect = $("#scope");
  scopeSelect.innerHTML = data.scopes.length ? data.scopes.map(s => `<option value="${escape(s.id)}">${escape(s.label)} / ${escape(s.persona === "[%None]" ? "未设置人格" : s.persona)}</option>`).join("") : '<option value="">尚无学习会话</option>';
  scopeSelect.value = selectedScope;
  scopeSelect.disabled = !data.scopes.length || pending;
  $("#session-status").textContent = data.scope ? `${data.scope.pending} 条待学素材，${data.traits.filter(t => t.status === "active").length} 条习惯已生效` : "收到有效聊天消息后，会话将自动出现在这里。";
  $("#memory-status").textContent = data.settings.memory_enabled ? data.memory.message : "记忆联动已关闭";
  $("#review-count").textContent = data.scopes.reduce((sum, scope) => sum + scope.review_count, 0) || "";
  const busy = data.busy.includes(selectedScope);
  $("#learn").disabled = pending || !data.scope || !!data.busy.length || !data.settings.enabled || !data.settings.learning_enabled || !data.scope.profile.learning_enabled || data.scope.pending < data.settings.min_evidence;
  $("#learn").textContent = busy ? "正在学习…" : "开始学习";
  if (renderContent && !dirty) render();
}

function empty(title, description) {
  return `<div class="empty"><h2>${escape(title)}</h2><p>${escape(description)}</p></div>`;
}

function switchField(name, label, description, checked) {
  return `<label class="switch"><input type="checkbox" name="${name}" ${checked ? "checked" : ""}><span>${label}${description ? `<small>${description}</small>` : ""}</span></label>`;
}

function field(name, label, value, help, options = {}) {
  const hint = help ? `aria-describedby="${name}-help"` : "";
  return `<div class="field"><label for="field-${name}">${label}</label>${options.type === "number" ? `<input id="field-${name}" name="${name}" type="number" min="${options.min}" max="${options.max}" step="${options.step || 1}" value="${escape(value)}" ${hint} required>` : options.type === "text" ? `<input id="field-${name}" name="${name}" value="${escape(value)}" ${hint} maxlength="256">` : `<textarea id="field-${name}" name="${name}" ${hint} maxlength="${options.maxlength || 1000}" ${options.large ? 'class="large"' : ""}>${escape(value)}</textarea>`}${help ? `<small id="${name}-help">${help}</small>` : ""}</div>`;
}

function render() {
  for (const button of document.querySelectorAll("[data-tab]")) {
    if (button.dataset.tab === tab) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current");
  }
  if (!data.scope && tab !== "settings" && tab !== "library") {
    $("#content").innerHTML = empty("从一段真实的对话开始", "在已启用的 AstrBot 会话中聊几句，工坊会自动积累文字素材。也可以先打开学习设置，选择学习对象与生效方式。");
    return;
  }
  if (tab === "profile") renderProfile();
  else if (tab === "library") renderLibrary();
  else if (tab === "history") renderHistory();
  else renderSettings();
}

function renderProfile() {
  const p = data.scope.profile;
  $("#content").innerHTML = `<div class="two-col"><form id="profile-form"><h2>保持自己的底色</h2><p class="section-intro">AstrBot 原有人格继续生效。在这里补充身份、表达和应对方式，让学来的习惯有一个稳定的落点。</p>
    ${field("identity", "人格补充", p.identity, "留空时，完整沿用 AstrBot 中配置的核心人格。", {large:true})}
    ${field("reply_style", "表达基调", p.reply_style, "例如：日常、简短，接得住玩笑，不过度解释。")}
    ${field("behavior", "行为准则", p.behavior, "例如：先倾听；拿不准时先澄清；对方难过时少说教。")}
    <div class="switches">${switchField("learning_enabled", "积累并学习", "仅影响当前会话与人格", p.learning_enabled)}${switchField("apply_enabled", "应用已生效的习惯", "关闭后保留全部学习数据", p.apply_enabled)}</div>
    <button class="primary" type="submit">保存人格底稿</button></form>
    <aside class="preview"><h2>看看它会带上哪些习惯</h2><p>输入一句可能出现的话，查看本插件会追加给模型的表达参考。免费预览使用文字检索，不调用模型或发送消息；真实回复还可使用向量、重排序与情境筛选，结果可能不同；兼容模式的角色资料未纳入此预览。</p><form id="preview-form"><label for="preview-query">试一句话</label><textarea id="preview-query" placeholder="例如：今天又搞砸了，有点难过。" maxlength="1200"></textarea><button class="tonal" type="submit">预览生效内容</button></form><pre id="preview-output" hidden></pre><p class="muted">仅展示本插件的补充内容，最终回复仍由 AstrBot 的人格、上下文和模型共同决定。</p></aside></div>`;
  $("#profile-form").addEventListener("submit", async event => {
    event.preventDefault();
    const form = event.currentTarget;
    await mutate("profile", {identity:form.identity.value, reply_style:form.reply_style.value, behavior:form.behavior.value, learning_enabled:form.learning_enabled.checked, apply_enabled:form.apply_enabled.checked});
  });
  $("#preview-form").addEventListener("submit", event => guarded(async () => {
    const result = await api("preview", {scope:selectedScope, query:$("#preview-query").value});
    $("#preview-output").hidden = false;
    $("#preview-output").textContent = `${result.retrieval || ""}\n${(result.selected || []).map(t => `#${t.id} ${t.reason}`).join("；")}\n\n${result.guidance || "当前没有生效的补充内容。可审核学习结果，或检查全局与当前会话的应用开关。"}`.trim();
  }, event));
}

function renderLibrary() {
  const library = data.library;
  const traits = (library?.traits || data.traits).filter(t => (category === "all" || t.kind === category) && (status === "all" || t.status === status));
  if (!traits.some(t => t.id === selectedTrait)) selectedTrait = traits[0]?.id;
  const trait = traits.find(t => t.id === selectedTrait);
  const review = trait && (trait.review || (library ? [] : data.runs || []).filter(run => !run.scope || run.scope === trait.scope).flatMap(run => run.review?.candidates || []).find(candidate => candidate.kind === trait.kind && candidate.situation === trait.situation && candidate.content === trait.content));
  const reviewHint = review?.approved === false ? `<p class="muted" role="note">学习自检提示：${escape(review.reason)}。${trait.manual ? "已有人工作出决定；保留这条历史提示供参考。" : "请结合下方原始证据检查；人工审核可以覆盖模型判断。"}</p>` : "";
  $("#content").innerHTML = `<div class="filter-row"><div class="filter-field"><label for="library-scope">会话</label><select id="library-scope"><option value="all">全部会话</option>${(data.scopes || []).map(s => `<option value="${escape(s.id)}" ${libraryScope === s.id ? "selected" : ""}>${escape(s.label)} / ${escape(s.persona === "[%None]" ? "未设置人格" : s.persona)}</option>`).join("")}</select></div><div class="filter-field"><label for="kind-filter">类别</label><select id="kind-filter"><option value="all">全部类别</option>${Object.entries(kinds).map(([value, name]) => `<option value="${value}" ${category === value ? "selected" : ""}>${name}</option>`).join("")}</select></div><div class="filter-field"><label for="status-filter">状态</label><select id="status-filter">${Object.entries({pending:"待审核", active:"已生效", disabled:"已停用", rejected:"已拒绝", all:"全部状态"}).map(([value, name]) => `<option value="${value}" ${status === value ? "selected" : ""}>${name}</option>`).join("")}</select></div><span class="muted">${library?.total ?? traits.length} 条结果</span></div>
    ${traits.length ? `<div class="library"><div class="trait-list" aria-label="学习条目">${traits.map(t => `<button type="button" class="trait-row" data-trait="${t.id}" aria-pressed="${selectedTrait === t.id}"><span class="row-meta"><span>${kinds[t.kind]}</span><span class="badge ${t.status}">${statuses[t.status]}</span></span><small class="trait-owner">${escape(t.label || data.scope?.label || "")} / ${escape((t.persona || data.scope?.persona) === "[%None]" ? "未设置人格" : (t.persona || data.scope?.persona || ""))}</small><strong>${escape(t.situation)}</strong><p>${escape(t.content)}</p><span class="row-meta">${t.evidence_count || t.evidence.length} 条证据 / ${t.support_count || 1} 批 · 置信度 ${Math.round(t.confidence * 100)}%</span></button>`).join("")}</div><article class="detail"><h2>${kinds[trait.kind]}</h2><p class="muted">所属会话：${escape(trait.label || data.scope?.label || "")} / ${escape((trait.persona || data.scope?.persona) === "[%None]" ? "未设置人格" : (trait.persona || data.scope?.persona || ""))}</p>${reviewHint}<form id="trait-form">${field("situation", "适用情境", trait.situation, "只在适合的情境里使用，不必每次重复。", {maxlength:160})}${field("content", "习惯内容", trait.content, "你可以在生效前调整表达；编辑不会自动改变审核状态。", {maxlength:350})}<button type="submit">保存编辑</button></form><div class="actions">${trait.status !== "active" ? '<button class="primary" data-review="active">批准生效</button>' : '<button data-review="disabled">停用这条</button>'}${trait.status === "pending" ? '<button class="danger" data-review="rejected">拒绝学习</button>' : '<button data-review="pending">移回待审核</button>'}</div><section class="evidence"><h3>积累与应用</h3><p class="muted">${trait.support_count || 1} 批独立素材，累计 ${trait.evidence_count || trait.evidence.length} 条证据。引用 ${trait.use_count || 0} 次；引用次数不代表模型采用或消息送达。${trait.manual ? " 人工决定已锁定，后续学习只补充证据。" : ""}</p><p class="muted">当前评价：${trait.rating === 1 ? "更合适" : trait.rating === -1 ? "不太合适" : "尚未评价"}</p><div class="actions" aria-label="习惯效果评价">${[[1,"更合适"],[-1,"不太合适"],[0,"清除评价"]].map(([value,label]) => `<button type="button" data-feedback="${value}" aria-pressed="${(trait.rating || 0) === value}">${label}</button>`).join("")}</div><p class="muted">评价调整后续排序；不合适的习惯也可直接停用。</p></section><div class="evidence"><h3>最近的学习来源</h3><p class="muted">最多展示最近 32 条摘录，累计证据数保留。</p>${trait.evidence.map(e => `<blockquote><p>${escape(e.text)}</p><small>${escape(e.sender)} · ${date(e.created)}</small></blockquote>`).join("")}</div></article></div>` : empty(status === "pending" ? "这里等待下一次发现" : "还没有这类学习结果", "素材积累后会自动批量学习，也可以点击「开始学习」。自动生效模式下，高置信度结果会进入「已生效」，其余结果仍等待审核。")}`;
  $("#library-scope").value = libraryScope;
  for (const [selector, current] of [["#library-scope", libraryScope], ["#kind-filter", category], ["#status-filter", status]]) {
    $(selector).onchange = async event => {
      const next = event.target.value; event.target.value = current;
      if (dirty && !await confirmAction("切换筛选会放弃未保存的编辑，继续？")) return;
      await guarded(async () => {
        const previous = [libraryScope, category, status, libraryOffset];
        dirty = false;
        if (selector === "#library-scope") libraryScope = next;
        else if (selector === "#kind-filter") category = next;
        else status = next;
        libraryOffset = 0;
        try { await load(); }
        catch (error) { [libraryScope, category, status, libraryOffset] = previous; renderLibrary(); throw error; }
      });
    };
  }
  if (library && library.total > 100) {
    $("#content").insertAdjacentHTML("beforeend", `<div class="actions library-pages"><button id="library-prev" ${library.offset === 0 ? "disabled" : ""}>上一页</button><span class="muted">第 ${library.offset / 100 + 1} / ${Math.ceil(library.total / 100)} 页 · 每页最多 100 条</span><button id="library-next" ${library.offset + 100 >= library.total ? "disabled" : ""}>下一页</button></div>`);
    for (const [id, step] of [["#library-prev", -100], ["#library-next", 100]]) $(id).onclick = async () => {
      if (dirty && !await confirmAction("翻页会放弃未保存的编辑，继续？")) return;
      await guarded(async () => {
        const previous = libraryOffset; dirty = false; libraryOffset += step;
        try { await load(); }
        catch (error) { libraryOffset = previous; throw error; }
      });
    };
  }
  for (const button of document.querySelectorAll("[data-trait]")) button.onclick = async () => { if (dirty && !await confirmAction("放弃当前未保存的编辑？")) return; dirty = false; selectedTrait = Number(button.dataset.trait); renderLibrary(); };
  if (trait) {
    for (const button of document.querySelectorAll("[data-feedback]")) button.onclick = async () => { if (dirty && !await confirmAction("评价会放弃未保存的编辑，继续？")) return; mutate("feedback", {id:trait.id, rating:Number(button.dataset.feedback)}, trait.scope); };
    $("#trait-form").onsubmit = async event => { event.preventDefault(); const form = event.currentTarget; await mutate("trait", {id:trait.id, situation:form.situation.value, content:form.content.value}, trait.scope); };
    for (const button of document.querySelectorAll("[data-review]")) button.onclick = () => mutate("trait", {id:trait.id, status:button.dataset.review, situation:$("#field-situation").value, content:$("#field-content").value}, trait.scope);
  }
}

function renderHistory() {
  const expandedReviews = new Set([...document.querySelectorAll("details[data-review-run][open]")].map(node => node.dataset.reviewRun));
  $("#content").innerHTML = `<h2>每一次学习，都有迹可循</h2><p class="section-intro">成功批次才会消耗素材；失败批次保留素材，修复原因后可以重新学习。</p>${data.runs.length ? data.runs.map(run => `<article class="history-row"><time>${date(run.started)}</time><span class="badge ${run.status}">${statuses[run.status]}</span><div><p>${escape(run.message || "正在分析本批素材…")}</p><small>${escape(run.memory)}</small>${run.review?.enabled && run.review.candidates?.length ? `<p class="muted">自检通过 ${escape(run.review.passed)} 条，标记 ${escape(run.review.flagged)} 条供人工检查。</p>${run.review.flagged ? `<details data-review-run="${escape(run.id)}" ${expandedReviews.has(String(run.id)) ? "open" : ""}><summary>查看自检提示与候选</summary><p class="muted">未通过的新候选转待审；未通过的更新不改旧条目。模型判断可由人工审核覆盖。</p><ul>${run.review.candidates.filter(c => !c.approved).map(c => `<li><strong>${escape(kinds[c.kind])} · ${escape(c.situation)}</strong><p>${escape(c.content)}</p><p>${escape(c.reason)}</p></li>`).join("")}</ul></details>` : ""}` : ""}</div></article>`).join("") : empty("还没有学习记录", "完成一次自动或手动学习后，这里会展示结果和记忆联动情况。")}
    <section class="versions"><h2>参与决策</h2><p class="section-intro">${data.settings?.participation_mode === "off" ? "自主参与已关闭。" : data.settings?.participation_mode === "observe" ? "当前仅记录判断，不主动回复。" : "当前允许自主参与，冷却与限额仍有效。"}「交给宿主」表示通过参与门槛，不保证模型生成或平台送达。</p>${(data.decisions || []).length ? data.decisions.map(d => `<article class="history-row"><time>${date(d.created)}</time><span class="badge ${d.outcome === "error" ? "error" : d.outcome === "admitted" ? "active" : ""}">${({reply:"参与回复",wait:"等待",observe:"旁观"})[d.action] || escape(d.action)}</span><div><p>${escape(d.reason)}</p>${d.intent ? `<small>回复目标：${escape(d.intent)}</small>` : ""}<small>${d.mode === "observe" ? "仅观察" : "自动参与"} / ${({admitted:"交给宿主",waiting:"等待后再判断",observed:"已记录",limited:"冷却或限额",cancelled:"已取消",error:"决策失败"})[d.outcome] || escape(d.outcome)} / 把握 ${Math.round(d.confidence * 100)}%</small></div></article>`).join("") : '<p class="muted">在学习设置中选择「仅观察」可先查看群聊决策，再决定是否允许自主回复。</p>'}</section>
    <section class="versions"><h2>最近引用的习惯</h2><p class="section-intro">记录进入宿主发送阶段的引用，不推断用户满意度，也不保证消息实际送达。</p>${(data.applications || []).length ? data.applications.map(a => `<div class="version-row"><span>${date(a.created)}<small> ${escape(({contextual:"情境筛选",contextual_fallback:"情境筛选回退",rerank:"模型重排序",hybrid:"语义与文字检索",lexical:"文字检索"}[a.mode] || "相关性检索"))}：${a.trait_ids.map(id => `#${id}`).join("、")}</small></span></div>`).join("") : '<p class="muted">应用习惯并进入宿主发送阶段后，这里会展示引用记录。</p>'}</section><section class="versions"><h2>修改前的版本</h2><p class="section-intro">每次学习、编辑、审核或恢复前都会留存快照，最多保留 30 个版本。恢复只影响人格底稿与学习条目，不恢复原始素材。</p>${data.revisions.length ? data.revisions.map(r => `<div class="version-row"><span>${actions[r.action] || escape(r.action)} <small>之前 · ${date(r.created)}</small></span><button data-restore="${r.id}">恢复此版本</button></div>`).join("") : '<p class="muted">首次修改后，将自动保留之前的版本。</p>'}</section>`;
  for (const button of document.querySelectorAll("[data-restore]")) button.onclick = async () => { if (await confirmAction("恢复这个版本的人格底稿和学习状态？当前版本也会自动保留。")) mutate("restore", {id:Number(button.dataset.restore)}); };
}

function renderSettings() {
  const s = data.settings;
  $("#content").innerHTML = `<form id="settings-form"><div class="settings-grid">
    <section class="settings-group debug-settings"><h2>调试日志</h2><p>在 AstrBot 日志中搜索「OvenHeart debug」查看本插件的处理过程。</p>${switchField("debug_enabled", "启用 Debug 模式", "完整输出实际注入内容、学习请求与响应、自检和检索筛选结果。包含聊天、人格和记忆摘录；仅在调试时开启。不会输出模型配置密钥或 HTTP 鉴权头。", s.debug_enabled)}</section>
    <section class="settings-group participation-settings"><h2>自主参与群聊</h2><p>根据最近聊天与已生效的习惯，决定参与、等待或旁观。直接唤醒、私聊和命令继续按宿主原有规则处理。自动回复使用 AstrBot 本地 Agent 运行器。</p><div class="two-col"><div>
      <div class="field"><label for="participation_mode">参与方式</label><select id="participation_mode" name="participation_mode"><option value="off" ${s.participation_mode === "off" ? "selected" : ""}>关闭自主参与</option><option value="observe" ${s.participation_mode === "observe" ? "selected" : ""}>仅观察，记录决策</option><option value="auto" ${s.participation_mode === "auto" ? "selected" : ""}>自动参与，交由宿主回复</option></select><small>建议先用「仅观察」检查决策。记录在「学习与参与」中。</small></div>
      <div class="number-fields">${field("participation_min_messages","最少新消息",s.participation_min_messages,"条；十分钟内保留最多 30 条上下文",{type:"number",min:1,max:30})}${field("participation_debounce","连续输入等待",s.participation_debounce,"秒；新输入会取消旧决策",{type:"number",min:0,max:10})}${field("participation_cooldown","回复冷却",s.participation_cooldown,"秒；直接唤醒的回复也会推迟自主参与",{type:"number",min:5,max:3600})}${field("participation_hourly_limit","每小时参与上限",s.participation_hourly_limit,"次；同群的隔离会话共享限额",{type:"number",min:1,max:120})}</div>
      </div><details><summary>决策模型与工具</summary>${field("planner_provider_id","参与决策模型 ID",s.planner_provider_id,"留空使用学习模型或当前会话模型。",{type:"text"})}<div class="number-fields">${field("planner_timeout","决策请求超时",s.planner_timeout,"秒；异常时保持旁观",{type:"number",min:3,max:90})}${field("planner_reply_threshold","最低参与把握",s.planner_reply_threshold,"由模型评估，不等同于统计概率",{type:"number",min:.5,max:1,step:.01})}${field("planner_max_wait","单次最长等待",s.planner_max_wait,"秒；最多等待后再判断一次",{type:"number",min:1,max:120})}</div>${field("autonomous_tools","自主回复可用工具",s.autonomous_tools.join("\n"),"每行一个精确工具名称。留空仅发文字，不改变直接唤醒的工具权限。",{maxlength:16000})}</details></div>
    </section>
    <section class="settings-group"><h2>学习与生效</h2><p>可以先审核，也可以让证据充足的结果自动加入习惯。</p>
      ${switchField("enabled", "启用人格工坊", "关闭后停止采集、学习和应用", s.enabled)}${switchField("learning_enabled", "启用素材采集与后台学习", "关闭后继续保留并应用已生效的习惯", s.learning_enabled)}${switchField("apply_enabled", "应用人格与学习结果", "可先积累，准备好后再启用", s.apply_enabled)}
      <div class="field"><label for="activation_mode">新结果的生效方式</label><select id="activation_mode" name="activation_mode"><option value="review" ${s.activation_mode === "review" ? "selected" : ""}>先审核后生效</option><option value="auto" ${s.activation_mode === "auto" ? "selected" : ""}>高置信度自动生效</option></select><small>影响新结果与尚未人工决定的条目；人工审核、拒绝和停用状态保持不变。</small></div>
      ${switchField("learning_review_enabled", "学习结果入库自检", "用学习模型核对原文与使用条件。未通过的新候选转待审，未通过的更新不改旧条目；自检失败保留整批素材。通常增加一次后台调用，提取与自检合计最多180秒。", s.learning_review_enabled)}
      <div class="number-fields">${field("auto_confidence", "自动生效置信度", s.auto_confidence, "由模型评估，不等同于统计概率。", {type:"number", min:.5, max:1, step:.01})}${field("min_evidence", "每条最少证据数", s.min_evidence, "不足时不保存学习条目。", {type:"number", min:1, max:10})}</div>
      ${Object.entries(kinds).map(([key, label]) => switchField(`learn_${key}`, `学习${label}`, "", s[`learn_${key}`])).join("")}
    </section>
    <section class="settings-group"><h2>素材与节奏</h2><p>只采集收到的文字消息，跳过机器人自己和命令。采集本身不会触发回复；是否自主参与由参与设置决定。</p>
      <div class="number-fields">${field("min_messages", "自动学习素材门槛", s.min_messages, "条消息", {type:"number", min:4, max:100})}${field("batch_size", "每批最多素材", s.batch_size, "条消息", {type:"number", min:4, max:100})}${field("interval_seconds", "每会话学习间隔", s.interval_seconds, "秒；同时作为失败重试冷却", {type:"number", min:30, max:86400})}${field("retention_days", "原始素材保留时间", s.retention_days, "天；学习证据摘录随条目保留", {type:"number", min:1, max:90})}${field("max_messages_per_scope", "每会话素材上限", s.max_messages_per_scope, "条；超出后淘汰最旧素材", {type:"number", min:100, max:1000})}</div>
      ${field("provider_id", "学习模型 ID", s.provider_id, "留空使用该会话的当前模型。填写 AstrBot 中已配置的模型提供商 ID。", {type:"text"})}
    </section>
    <section class="settings-group"><h2>学习范围</h2><p>每个会话与人格单独积累。AstrBot 的会话开关、插件开关与白名单始终有效。</p>
      ${field("target_senders", "只向这些用户学习", s.target_senders.join("\n"), "每行一个用户 ID。留空学习所有人；指定后，旁人的邻近发言仅短暂用于理解语境，不入素材库或证据。", {maxlength:16000})}
      ${field("blocked_senders", "不学习的用户（QQ 号）", (s.blocked_senders || []).join("\n"), "直接填写 QQ 号即可，每行一个，也支持空格、逗号分隔。黑名单优先于学习对象；不采集、不学习，也不作为学习上下文。已学到的习惯保留，可在学习库手动停用。", {maxlength:128000})}
      ${field("allowed_sessions", "允许学习与应用的会话", s.allowed_sessions.join("\n"), "每行一个完整 unified_msg_origin，可通过 AstrBot 的 /sid 获取。留空允许全部已启用会话。", {maxlength:32000})}
    </section>
    <section class="settings-group"><h2>记忆与表达</h2><p>记忆帮助理解来龙去脉；人格工坊只学习有原始聊天证据的习惯。</p>
      ${switchField("memory_enabled", "参考 LivingMemory 长期记忆", data.memory.message, s.memory_enabled)}
      <div class="number-fields">${field("memory_top_k", "最多参考记忆", s.memory_top_k, "条；仅在学习时检索", {type:"number", min:1, max:8})}${field("max_traits", "每次最多应用习惯", s.max_traits, "条；优先匹配当前对话", {type:"number", min:1, max:24})}${field("prompt_budget", "补充内容字数上限", s.prompt_budget, "字符；包含人工底稿", {type:"number", min:500, max:8000})}</div>
      <div class="field"><label for="guidance_delivery">提示投递方式</label><select id="guidance_delivery" name="guidance_delivery"><option value="system" ${s.guidance_delivery === "system" ? "selected" : ""}>仅系统提示词</option><option value="dual" ${s.guidance_delivery === "dual" ? "selected" : ""}>系统与临时上下文（兼容模式）</option></select><small>当接口不遵循 system 中的角色或语气时使用兼容模式。只附加已有角色资料与本轮选中习惯，不复制完整系统提示词。原生 Agent 保存历史时剔除临时内容；第三方运行器需自行支持。</small></div>
      <div class="field"><label for="selection_mode">习惯检索方式</label><select id="selection_mode" name="selection_mode"><option value="contextual" ${s.selection_mode === "contextual" ? "selected" : ""}>按当前对话筛选</option><option value="hybrid" ${s.selection_mode === "hybrid" ? "selected" : ""}>语义与文字结合</option><option value="lexical" ${s.selection_mode === "lexical" ? "selected" : ""}>仅文字相关性</option></select><small>情境模式每次回复最多额外调用一次学习模型，未指定时用当前聊天模型。从最多 24 条候选中选 0–5 条，允许本轮不选；其他模式仅按相关性排序。</small></div>
      ${field("embedding_provider_id", "向量模型 ID", s.embedding_provider_id, "填写 AstrBot 中已配置的 embedding 提供商 ID。留空使用文字检索。", {type:"text"})}
      ${field("rerank_provider_id", "重排序模型 ID", s.rerank_provider_id, "可选，填写 AstrBot 中已配置的 rerank 提供商 ID。只改变候选顺序，不能代替情境判断。", {type:"text"})}
      ${field("selector_timeout", "情境筛选等待上限", s.selector_timeout, "秒；异常时不选表达习惯，仍保留当前输入直接命中的本地词义。", {type:"number", min:3, max:30})}
      <div class="number-fields">${field("retrieval_timeout", "召回等待上限", s.retrieval_timeout, "秒；向量与重排序共用，情境筛选另计", {type:"number", min:1, max:15})}${field("semantic_threshold", "语义相关性门槛", s.semantic_threshold, "余弦相似度，按模型效果调整", {type:"number", min:0, max:1, step:.01})}${field("habit_half_life", "习惯新鲜度半衰期", s.habit_half_life, "天；逐渐降权，不自动删除", {type:"number", min:7, max:730})}</div>
      <p class="muted">${escape(data.retrieval?.message || "真实回复后显示检索状态。")}</p>
      <p class="muted">LivingMemory 检索可能更新访问时间并触发其原有迁移机制。QQ 能力增强插件可并用，无需额外配置。</p>
    </section>
</div><div class="form-end"><button class="primary" type="submit">保存学习设置</button><span class="muted">保存后立即生效；本页设置优先于插件配置文件。</span></div></form>
    ${data.scope ? '<div class="form-end"><button id="clear-messages" class="danger">清除当前会话的原始素材</button><span class="muted">保留人格与学习证据摘录；清除后无法恢复原始素材。</span></div>' : ""}`;
  $("#settings-form").onsubmit = event => {
    event.preventDefault();
    const form = event.currentTarget;
    const patch = {};
    for (const [key, value] of Object.entries(s)) {
      const input = form.elements.namedItem(key);
      if (!input) continue;
      patch[key] = typeof value === "boolean" ? input.checked : typeof value === "number" ? Number(input.value) : Array.isArray(value) ? input.value.split(key === "blocked_senders" ? /[\s,，;；]+/ : "\n").map(v => v.trim()).filter(Boolean) : input.value.trim();
    }
    mutate("settings", patch);
  };
  if ($("#clear-messages")) $("#clear-messages").onclick = async () => { if (await confirmAction("永久清除当前会话所有原始聊天素材？学习条目及其证据摘录会保留。")) mutate("clear_messages"); };
}

async function guarded(work, event) {
  event?.preventDefault();
  if (pending) return;
  pending = true;
  const controls = [...document.querySelectorAll("button, select, input, textarea")].filter(el => !el.disabled);
  controls.forEach(el => el.disabled = true);
  try { await work(); }
  catch (error) { notify(error.message || "请求失败，请检查 AstrBot 连接。", true); }
  finally { pending = false; controls.forEach(el => { if (el.isConnected) el.disabled = false; }); if (data) await load(false).catch(() => {}); }
}

async function mutate(action, payload = {}, scope = selectedScope) {
  return guarded(async () => {
    const result = await api("action", {action, scope, payload});
    notify(result.message || "已保存。");
    dirty = false;
    await load();
  });
}

$("#content").addEventListener("input", event => { if (event.target.closest("form")?.id !== "preview-form") dirty = true; });
$("#content").addEventListener("change", event => { if (event.target.closest("form")) dirty = true; });
$("#scope").onchange = async event => {
  const next = event.target.value; event.target.value = selectedScope;
  if (dirty && !await confirmAction("切换会话会放弃未保存的编辑，继续？")) return;
  selectedScope = next; selectedTrait = null; dirty = false; guarded(() => load());
};
$("#refresh").onclick = async () => { if (!dirty || await confirmAction("刷新会放弃未保存的编辑，继续？")) { dirty = false; guarded(() => load()); } };
$("#learn").onclick = async () => { if (!dirty || await confirmAction("开始学习会放弃当前未保存的编辑，继续？")) mutate("learn"); };
for (const button of document.querySelectorAll("[data-tab]")) button.onclick = async () => {
  if (dirty && !await confirmAction("离开此页会放弃未保存的编辑，继续？")) return;
  dirty = false; tab = button.dataset.tab; if (tab === "library") await guarded(() => load()); else render();
};
window.addEventListener("beforeunload", event => { if (dirty) { event.preventDefault(); event.returnValue = ""; } });

async function start() {
  // The host injects its bridge script at the end of the entry document.
  bridge = window.AstrBotPluginView || window.AstrBotPluginPage;
  if (!bridge) {
    notify("未连接 AstrBot Panel。请安装并启用插件，从 Panel 的插件页面打开「人格工坊」。", true);
    $("#scope").innerHTML = '<option value="">请从 AstrBot Panel 打开</option>';
    $("#content").innerHTML = empty("需要从 AstrBot Panel 打开", "此页面使用 AstrBot 的登录与权限，不需要单独启动网页服务。");
    return;
  }
  try {
    const context = await bridge.ready();
    const theme = value => { document.documentElement.dataset.theme = value?.isDark ? "dark" : "light"; };
    theme(context); bridge.onContext?.(theme);
    await load();
  } catch (error) { notify(error.message || "无法连接 AstrBot，请刷新 Panel 后重试。", true); }
  setInterval(async () => {
    if (pending || polling || document.hidden || !data) return;
    polling = true;
    try { await load(!dirty && (tab === "history" || tab === "library" || !!data.busy.length)); }
    catch (error) { notify(error.message || "连接已中断，请刷新。", true); }
    finally { polling = false; }
  }, 5000);
}
start();
