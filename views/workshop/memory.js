/* MaiBot-style memory console, backed by authenticated native SDK operations. */
window.OvenHeartMemory = (() => {
  const tabs = {records:"记忆记录",profiles:"人物画像",images:"图片记忆",graph:"关系图谱",timeline:"时间线",import:"导入与来源",inspection:"记忆检修",delete:"删除管理"};
  const kinds = {paragraph:"段落",entity:"实体",relation:"关系",fact:"事实",episode:"情景记忆",profile:"人物画像",image:"图片",correction:"记忆修正",feedback:"反馈处理",delete:"删除操作"};
  const statuses = {active:"有效",inactive:"已冻结",expired:"已过期",deleted:"已删除",retracted:"已撤回",conflicted:"有冲突",superseded:"已被修正",completed:"已完成",executed:"已执行",restored:"已恢复",restore_pending:"正在恢复索引",pending_cleanup:"待完成索引清理",pending:"待处理",failed:"失败",running:"处理中",awaiting_confirmation:"待确认",rolled_back:"已回滚"};
  let view="records",kind="paragraph",query="",offset=0,inactive=false,since="",until="",currentScope="";
  function render(context) {
    const {root,data,api,escape:esc,notify,confirmAction,renderSettings}=context;
    const scope=data.scope?.id;
    if(scope!==currentScope){currentScope=scope;query="";offset=0;since=until="";}
    let version=0,busy=false,selection=0,storageScope="",importing=false,stopImport=false;
    root.innerHTML=`<div class="memory-heading"><div><h2>记忆管理</h2><p class="muted">查看原文与来源，维护人物、图片和关系。${data.settings.memory_isolation ? "仅召回当前会话的记忆。" : "当前人格可召回全部会话记忆，包含已有资料；人物仍按真实账号区分。"}</p></div><button type="button" id="memory-config">记忆设置</button></div><nav class="memory-tabs" aria-label="记忆管理页面">${Object.entries(tabs).map(([key,label])=>`<button type="button" data-memory-view="${key}">${label}</button>`).join("")}</nav><p id="memory-summary" class="muted" role="status"></p><div id="memory-body"></div>`;
    const body=root.querySelector("#memory-body"),$=selector=>body.querySelector(selector),live=()=>root.querySelector("#memory-body")===body;
    const date=value=>value?new Date(value*1000).toLocaleString("zh-CN"):"时间未记录";
    const badge=item=>`<span class="chip">${esc(statuses[item.status]||item.status||kinds[item.kind])}</span>`;
    async function write(action,payload,refresh=true){
      if(busy||!live())return;
      const edit=context.editVersion();
      busy=true;
      for(const button of body.querySelectorAll("[data-memory-write]"))button.disabled=true;
      try{
        const result=await api("memory/action",{scope,action,payload,...(storageScope?{storage_scope:storageScope}:{})});
        if(!live())return result;
        if(refresh){notify(result.message||"记忆操作已完成。",!!result.partial);if(edit===context.editVersion()){context.clearDirty();await load();}}
        return result;
      }catch(error){if(live())notify(error.message,true);}
      finally{busy=false;if(live())for(const button of body.querySelectorAll("[data-memory-write]"))button.disabled=false;}
    }
    async function navigate(work){
      if(busy||importing){notify("记忆操作尚未返回，请稍后切换。",true);return;}
      if(context.isDirty()&&!await confirmAction("切换会放弃未保存的记忆编辑，继续？"))return;
      context.clearDirty();
      return work();
    }
    function filters(){
      const choices=view==="records"?{paragraph:"段落",entity:"实体",relation:"关系",fact:"事实",episode:"情景记忆"}:view==="inspection"?{relation:"关系维护",episode:"情景记忆",correction:"修正历史",feedback:"反馈处理"}:null;
      return `<form id="memory-search" class="memory-search"><div class="field"><label for="memory-query">${view==="profiles"?"查找人物":"查找记忆"}</label><input id="memory-query" type="search" value="${esc(query)}" maxlength="500" placeholder="关键词、内容或来源"></div>${choices?`<div class="field"><label for="memory-kind">记录类型</label><select id="memory-kind">${Object.entries(choices).map(([key,label])=>`<option value="${key}" ${key===kind?"selected":""}>${label}</option>`).join("")}</select></div>`:""}<button type="submit" class="tonal">查询</button><button type="button" id="memory-reload">刷新</button><label class="memory-inactive"><input type="checkbox" id="memory-inactive" ${inactive?"checked":""}>包含失效记录</label>${view==="timeline"?`<div class="memory-dates"><label>开始时间<input type="datetime-local" id="memory-since" value="${esc(since)}"></label><label>结束时间<input type="datetime-local" id="memory-until" value="${esc(until)}"></label></div>`:""}</form>`;
    }
    function pager(result){return `<div class="memory-pagination"><span class="muted">共 ${result.total} 条${result.total?` · ${result.offset+1}–${Math.min(result.offset+result.limit,result.total)}`:""}</span><div class="actions"><button type="button" id="memory-prev" ${!offset?"disabled":""}>上一页</button><button type="button" id="memory-next" ${offset+result.limit>=result.total?"disabled":""}>下一页</button></div></div>`;}
    async function load(){
      for(const button of root.querySelectorAll("[data-memory-view]")){if(button.dataset.memoryView===view)button.setAttribute("aria-current","page");else button.removeAttribute("aria-current");}
      const token=++version;
      selection++;
      if(view==="settings"){root.querySelector("#memory-summary").textContent="";renderSettings();return;}
      if(!scope||scope===data.shared_scope?.id){body.innerHTML='<div class="empty"><h3>选择一个会话</h3><p>选择具体会话与人格后，即可管理对应的原生记忆。</p></div>';return;}
      storageScope="";
      body.innerHTML='<p class="muted" role="status">正在读取原生记忆…</p>';
      try{
        const result=await api("memory",undefined,{scope,view,kind,query,offset,inactive,since:since?new Date(since).getTime()/1000:"",until:until?new Date(until).getTime()/1000:""});
        if(!live()||token!==version)return;
        root.querySelector("#memory-summary").textContent=Object.entries(result.stats).map(([key,count])=>`${kinds[key]} ${count}`).join(" · ")||"当前会话尚无原生记忆。";
        const rows=result.items;
        const description=view==="inspection"?"关系支持强化、冻结和限时保护；修正历史可回滚。操作通过聊天使用的同一原生 SDK 执行。":view==="delete"?"在记忆记录或来源详情中先预览范围，再确认删除。此处保留原生删除日志，可按操作恢复。":"";
        const list=rows.length?rows.map((item,index)=>`<button type="button" class="memory-row" data-memory-record="${index}"><span class="memory-row-meta"><span>${esc(kinds[item.kind]||"记忆")}</span>${badge(item)}</span><strong>${esc(item.kind==="paragraph"?item.text.slice(0,100):item.title)}</strong>${item.text&&item.kind!=="paragraph"?`<span class="memory-excerpt">${esc(item.text.slice(0,180))}</span>`:""}<small>${esc(date(item.timestamp))}</small></button>`).join(""):'<div class="empty"><h3>没有匹配的记录</h3><p>试试其他关键词或类型，也可以启用“包含失效记录”。</p></div>';
        const columns=`<div class="memory-columns"><div class="memory-list" aria-label="记忆记录">${list}</div><section id="memory-detail" class="memory-detail" aria-label="记忆详情"><h3>记录详情</h3><p class="muted">选择一条记录，查看完整内容、原始来源和可用操作。</p></section></div>`;
        body.innerHTML=filters()+(description?`<p class="muted memory-description">${description}</p>`:"")+(view==="import"?importForm()+sourceList(rows):(view==="graph"?graph(rows):"")+columns)+pager(result);
        $("#memory-search").onsubmit=event=>{event.preventDefault();navigate(()=>{query=$("#memory-query").value.trim();kind=$("#memory-kind")?.value||kind;inactive=$("#memory-inactive").checked;since=$("#memory-since")?.value||"";until=$("#memory-until")?.value||"";offset=0;load();});};
        $("#memory-reload").onclick=()=>navigate(load);
        $("#memory-prev").onclick=()=>navigate(()=>{offset=Math.max(0,offset-result.limit);load();});
        $("#memory-next").onclick=()=>navigate(()=>{offset+=result.limit;load();});
        for(const button of body.querySelectorAll("[data-memory-record]"))button.onclick=()=>navigate(()=>detail(rows[Number(button.dataset.memoryRecord)]));
        for(const button of body.querySelectorAll("[data-memory-node]"))button.onclick=()=>navigate(()=>{query=button.dataset.memoryNode;offset=0;load();});
        if(view==="import")bindImports(rows);
        if(view==="graph")$("#memory-edge").onsubmit=event=>{event.preventDefault();const form=new FormData(event.currentTarget);write("edge_create",{subject:form.get("subject"),predicate:form.get("predicate"),object:form.get("object")});};
      }catch(error){if(live()&&token===version){body.innerHTML=`<div class="empty"><h3>记忆读取未完成</h3><p>${esc(error.message)}</p><button type="button" id="memory-retry">重新读取</button></div>`;$("#memory-retry").onclick=load;}}
    }
    async function detail(item){
      const panel=$("#memory-detail"),token=++selection;
      panel.innerHTML='<p class="muted">正在读取完整来源…</p>';
      try{
        const result=await api("memory",undefined,{scope,...(item.storage_scope?{storage_scope:item.storage_scope}:{}),view:item.kind==="profile"?"profiles":item.kind==="image"?"images":"records",kind:item.kind,id:item.id,inactive:true});
        if(!live()||$("#memory-detail")!==panel||token!==selection)return;
        const full=result.items[0];if(!full)throw new Error("这条记录已不存在，请刷新列表。");
        storageScope=full.storage_scope||"";
        const editable=["paragraph","relation","fact","profile"].includes(full.kind),metadata=full.metadata||{},evidence=[...(metadata.evidence||[]),...(metadata.legacy_sources||[]),...(full.preview_json?.auto_review_evidence||[])];
        const review=full.kind==="correction"&&full.preview_json?.auto_review,operations=full.plan_json?.operations||[];
        panel.innerHTML=`<div class="memory-detail-head"><h3>${esc(kinds[full.kind])}详情</h3>${badge(full)}</div><p class="memory-id">${esc(full.id)}</p>${full.kind==="image"?'<div id="memory-image"></div><p id="memory-image-status" class="muted">正在读取图片…</p>':""}${full.kind==="profile"&&full.manual_override?'<p class="muted">当前存在人工画像覆盖。</p>':""}${editable?`<form id="memory-editor"><label for="memory-edit-text">${full.kind==="profile"?"人工画像":"完整内容"}</label><textarea id="memory-edit-text" maxlength="30000" required>${esc(full.manual_override||full.text||full.title)}</textarea><button type="submit" class="primary" data-memory-write>保存${full.kind==="profile"?"人工画像":"修正"}</button></form>`:`<p class="memory-full-text">${esc(full.text||full.title)}</p>`}${review?`<h4>主动复核建议</h4><p>${esc(full.plan_json?.reason||full.reason||"检查新原文是否更正已有事实。")}</p>${operations.filter(op=>op.action==="ingest_text").map(op=>`<blockquote>${esc(op.text)}</blockquote>`).join("")}${!operations.length?'<p class="muted">无需修改，原记录保留。</p>':""}`:""}<dl class="memory-facts"><dt>时间</dt><dd>${esc(date(full.timestamp))}</dd><dt>来源</dt><dd>${esc(full.source||"原生记忆库")}</dd>${full.expires_at?`<dt>有效期至</dt><dd>${esc(date(full.expires_at))}</dd>`:""}</dl>${full.entities?.length?`<h4>相关实体</h4><p>${full.entities.map(esc).join("、")}</p>`:""}${full.relations?.length?`<h4>关联关系</h4>${full.relations.map(row=>`<p>${esc(row.subject)} · ${esc(row.predicate)} · ${esc(row.object)}</p>`).join("")}`:""}${evidence.length?`<h4>原文证据</h4>${evidence.map(row=>`<blockquote>${esc(row.quote||row.text||"")}<small>${esc(row.sender||row.sender_id||"")}</small></blockquote>`).join("")}`:""}${full.observations?.length?`<h4>图片观察与消息来源</h4>${full.observations.map(row=>`<blockquote>${esc(row.text||"暂无描述")}<small>消息 ${esc(row.message_id)} · ${esc(date(row.occurred_at))}</small></blockquote>`).join("")}`:""}<div class="actions memory-record-actions">${["paragraph","relation","entity"].includes(full.kind)&&full.status!=="deleted"?'<button type="button" id="memory-delete" class="danger" data-memory-write>预览删除范围</button>':""}${full.kind==="relation"?'<button type="button" data-maintain="reinforce" data-memory-write>强化</button><button type="button" data-maintain="freeze" data-memory-write>冻结</button><button type="button" data-maintain="protect" data-memory-write>保护 24 小时</button>':""}${full.kind==="delete"&&full.status!=="restored"?'<button type="button" id="memory-restore" data-memory-write>恢复本次删除</button>':""}${review&&full.status==="awaiting_confirmation"&&full.preview_json.auto_review_executable?'<button type="button" id="memory-review-execute" data-memory-write>确认执行复核建议</button>':""}${full.kind==="correction"&&["executed","completed"].includes(full.status)?'<button type="button" id="memory-rollback" data-memory-write>回滚本次修正</button>':""}${full.kind==="profile"&&full.manual_override?'<button type="button" id="memory-clear-profile" data-memory-write>移除人工覆盖</button>':""}${full.kind==="fact"?`<button type="button" id="memory-fact-state" data-memory-write>${full.status==="retracted"?"恢复事实":"撤回事实"}</button>`:""}</div>${full.kind==="entity"?`<form id="memory-rename"><label for="memory-node-name">实体名称</label><input id="memory-node-name" maxlength="120" value="${esc(full.name)}" required><button type="submit" data-memory-write>重命名实体</button></form>`:""}<div id="memory-delete-preview"></div><details class="memory-metadata"><summary>完整来源与状态</summary><pre>${esc(JSON.stringify(full,null,2))}</pre></details>`;
        if($("#memory-editor"))$("#memory-editor").onsubmit=async event=>{event.preventDefault();if(!await confirmAction(full.kind==="profile"?"保存人工画像覆盖？自动生成的原画像会保留。":"确认修正这条记忆？原内容和修正历史会保留。"))return;write(full.kind==="profile"?"profile_save":full.kind==="fact"?"fact_save":"edit",{id:full.id,...(["paragraph","relation"].includes(full.kind)?{kind:full.kind}:{}),text:$("#memory-edit-text").value.trim()});};
        if($("#memory-delete"))$("#memory-delete").onclick=()=>deletion(full.kind,full.id);
        for(const [selector,action,message] of [["#memory-review-execute","review_execute","确认按上方建议修正记忆？原记录与来源保留，可回滚。"],["#memory-restore","delete_restore","恢复本次删除涉及的记忆及关联内容？"],["#memory-rollback","rollback","回滚本次修正并恢复原有状态？"],["#memory-clear-profile","profile_clear","移除人工覆盖，重新使用自动画像？"]]){
          if($(selector))$(selector).onclick=async()=>{if(await confirmAction(message))write(action,{id:full.id});};
        }
        if($("#memory-fact-state"))$("#memory-fact-state").onclick=async()=>{if(await confirmAction("确认更改这条事实的有效状态？"))write(full.status==="retracted"?"fact_restore":"fact_retract",{id:full.id});};
        for(const button of body.querySelectorAll("[data-maintain]"))button.onclick=async()=>{if(await confirmAction(`确认${button.textContent}这条关系？`))write(button.dataset.maintain,{id:full.id});};
        if($("#memory-rename"))$("#memory-rename").onsubmit=event=>{event.preventDefault();write("node_rename",{id:full.name,name:$("#memory-node-name").value.trim()});};
        if(full.kind==="profile"){
          panel.insertAdjacentHTML("beforeend",`<form id="memory-aliases"><label for="memory-alias-text">人工别名</label><textarea id="memory-alias-text" maxlength="3600" placeholder="每行一个别名">${esc((full.aliases||[]).join("\n"))}</textarea><button type="submit" data-memory-write>保存别名</button></form>`);
          $("#memory-aliases").onsubmit=event=>{event.preventDefault();write("profile_aliases",{id:full.id,aliases:$("#memory-alias-text").value.split("\n").map(value=>value.trim()).filter(Boolean)});};
        }
        if(full.kind==="image"){
          const observations=(full.observations||[]).filter(row=>row.occurrence_status==="active");
          panel.insertAdjacentHTML("beforeend",observations.map((row,index)=>`<form data-image-observation="${index}" class="memory-observation"><label>人工观察<textarea maxlength="30000" required>${esc(row.text||"")}</textarea></label><div class="actions"><button type="submit" data-memory-write>保存观察</button><button type="button" class="danger" data-image-forget="${index}" data-memory-write>移除这次引用</button></div></form>`).join(""));
          for(const form of panel.querySelectorAll("[data-image-observation]"))form.onsubmit=event=>{event.preventDefault();const row=observations[Number(form.dataset.imageObservation)];write("image_observe",{id:row.occurrence_id,text:form.querySelector("textarea").value.trim(),previous:row.observation_id||""});};
          for(const button of panel.querySelectorAll("[data-image-forget]"))button.onclick=async()=>{if(await confirmAction("移除这次图片引用及其关联？此入口不能恢复它。"))write("image_forget",{id:observations[Number(button.dataset.imageForget)].occurrence_id});};
        }
        if(full.kind==="image"){const image=await api("memory/image",undefined,{scope,id:full.id,...(storageScope?{storage_scope:storageScope}:{})});if(live()&&$("#memory-detail")===panel&&token===selection){const img=document.createElement("img");img.className="memory-image";img.alt="当前图片记忆";img.src="data:image/jpeg;base64,"+image.encoded;$("#memory-image").replaceChildren(img);$("#memory-image-status").textContent="";}}
        if(matchMedia("(max-width: 900px)").matches)panel.scrollIntoView({block:"start",behavior:"auto"});
      }catch(error){if(live()&&$("#memory-detail")===panel&&token===selection)panel.innerHTML=`<p class="muted">${esc(error.message)}</p>`;}
    }
    async function deletion(recordKind,identity){
      const preview=await write("delete_preview",{kind:recordKind,id:identity},false);if(!preview||!live())return;
      const panel=$("#memory-delete-preview"),counts=preview.counts||preview.preview?.counts||{};
      panel.innerHTML=`<div class="memory-delete-preview"><h4>删除范围预览</h4><p>删除会同步处理原生关联索引，并生成可恢复的删除操作。</p><dl class="memory-facts">${Object.entries(counts).map(([key,count])=>`<dt>${esc(({paragraphs:"段落",relations:"关系",entities:"实体",sources:"来源",vector_ids:"向量",episodes:"情景记忆"})[key]||key)}</dt><dd>${esc(count)}</dd>`).join("")}</dl><details><summary>查看完整影响范围</summary><pre>${esc(JSON.stringify(preview,null,2))}</pre></details><button type="button" class="danger" id="memory-confirm-delete" data-memory-write>确认删除</button></div>`;
      $("#memory-confirm-delete").onclick=async()=>{if(await confirmAction("按预览范围执行删除？完成后可在删除管理中恢复。"))write("delete_execute",{kind:recordKind,id:identity});};
      panel.scrollIntoView({block:"nearest",behavior:"auto"});
    }
    function graph(rows){
      const nodes=[...new Set(rows.flatMap(row=>[row.subject,row.object]))].slice(0,30);
      const positions=new Map(nodes.map((name,index)=>[name,{x:350+260*Math.cos(index*2*Math.PI/nodes.length),y:210+160*Math.sin(index*2*Math.PI/nodes.length)}]));
      return `<div class="memory-graph"><svg viewBox="0 0 700 420" role="img" aria-label="关系图谱，最多展示三十个实体">${rows.filter(row=>positions.has(row.subject)&&positions.has(row.object)).map(row=>{const a=positions.get(row.subject),b=positions.get(row.object);return `<line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}"/><text class="memory-edge-label" x="${(a.x+b.x)/2}" y="${(a.y+b.y)/2}">${esc(row.predicate.slice(0,8))}</text>`;}).join("")}${nodes.map(name=>{const p=positions.get(name);return `<g><circle cx="${p.x}" cy="${p.y}" r="8"/><text x="${p.x}" y="${p.y+24}" text-anchor="middle">${esc(name.slice(0,12))}</text></g>`;}).join("")}</svg><div class="actions">${nodes.map(name=>`<button type="button" data-memory-node="${esc(name)}">${esc(name)}</button>`).join("")||'<p class="muted">暂无匹配的关系。</p>'}</div></div><details class="memory-add-edge"><summary>新增关系</summary><form id="memory-edge"><label>主体<input name="subject" maxlength="120" required></label><label>关系<input name="predicate" maxlength="120" required></label><label>客体<input name="object" maxlength="120" required></label><button type="submit" data-memory-write>保存关系</button></form></details>`;
    }
    function importForm(){return '<section class="memory-import"><h3>从 LivingMemory 导入</h3><p class="muted">选择一个来源会话与人格，将文本记忆复制到当前会话与人格。保留原始来源，重复导入自动跳过；无需启用 LivingMemory。分批处理，使用当前配置的嵌入模型，不复制向量与运行配置。</p><button type="button" id="memory-read-livingmemory" data-memory-write>读取可导入记忆</button><div id="memory-livingmemory-source"></div><p id="memory-livingmemory-progress" class="muted" role="status" aria-live="polite"></p><h3>导入文字资料</h3><p class="muted">写入当前会话原生记忆，保留管理员导入来源；不调用生成模型。</p><form id="memory-import-text"><label for="memory-import-content">资料正文</label><textarea id="memory-import-content" maxlength="30000" required placeholder="粘贴正文，或选择 TXT、Markdown 文件"></textarea><div class="actions"><input type="file" id="memory-text-file" accept=".txt,.md,text/plain,text/markdown"><button type="submit" class="primary" data-memory-write>导入正文</button></div></form><div class="memory-bundles"><h3>原版记忆包</h3><p class="muted">使用 A_memorix 原版包格式。导出当前库完整快照；导入作为知识包安装，不覆盖已有记忆。文件上限 16 MB。</p><div class="actions"><button type="button" id="memory-export" data-memory-write>导出记忆包</button><label class="memory-file-label">选择记忆包<input type="file" id="memory-bundle-file" accept=".amembundle,.zip"></label><button type="button" id="memory-install" data-memory-write>安装记忆包</button></div></div><h3>已入库来源</h3></section>';}
    function sourceList(rows){return (rows.length?rows.map((row,index)=>`<div class="memory-source"><div><strong>${esc(row.source||"未标记来源")}</strong><p class="muted">${row.count} 条段落 · 已删除 ${row.deleted||0} · ${esc(date(row.timestamp))}</p></div><button type="button" class="danger" data-memory-source="${index}" data-memory-write>预览删除来源</button></div>`).join(""):'<p class="muted">当前会话尚无已入库来源。</p>')+'<div id="memory-delete-preview"></div>';}
    function bindImports(rows){
      $("#memory-read-livingmemory").onclick=async()=>{
        if(busy||importing)return;
        const button=$("#memory-read-livingmemory"),panel=$("#memory-livingmemory-source"),progress=$("#memory-livingmemory-progress");
        button.disabled=true;
        progress.textContent="正在读取来源…";
        try{
          const source=await api("memory",undefined,{scope,view:"livingmemory"});
          if(!live())return;
          if(!source.available||!source.groups.length){panel.replaceChildren();progress.textContent=source.message||"没有可导入的文本记忆。";return;}
          panel.innerHTML=`<label for="memory-livingmemory-group">来源会话与人格</label><select id="memory-livingmemory-group">${source.groups.map((group,index)=>`<option value="${index}">${esc(group.session||"未标记会话")} · ${esc(group.persona||"未标记人格")} · ${group.count} 条</option>`).join("")}</select><p class="muted">导入到：${esc(data.scope.label||data.scope.umo||scope)} · ${esc(data.scope.persona||"默认人格")}</p><div class="actions"><button type="button" class="primary" id="memory-livingmemory-start" data-memory-write>导入所选来源</button><button type="button" id="memory-livingmemory-stop" hidden>完成本批后暂停</button></div>`;
          progress.textContent=source.excluded?`${source.excluded} 条空白、超过 30000 字或来源格式无效的记录不在导入范围内。`:"选择来源后开始导入；源数据库保持原样。";
          $("#memory-livingmemory-start").onclick=async()=>{
            if(busy||importing)return;
            const group=source.groups[Number($("#memory-livingmemory-group").value)];
            if(!await confirmAction(`将“${group.session||"未标记会话"} · ${group.persona||"未标记人格"}”的 ${group.count} 条文本记忆导入当前会话与人格？`))return;
            if(!live())return;
            importing=true;stopImport=false;
            const select=$("#memory-livingmemory-group"),stop=$("#memory-livingmemory-stop");
            select.disabled=true;stop.hidden=false;
            stop.onclick=()=>{stopImport=true;stop.disabled=true;progress.textContent="正在完成本批，随后暂停…";};
            let after=0,imported=0,skipped=0,done=false;
            try{
              while(live()&&!stopImport){
                progress.textContent=`已导入 ${imported} 条，已跳过 ${skipped} 条重复记录；正在处理下一批…`;
                const result=await write("import_livingmemory",{session:group.session,persona:group.persona,after,until:group.until},false);
                if(!result||!live())break;
                after=result.after;imported+=result.imported;skipped+=result.skipped;done=result.done;
                if(done)break;
              }
              if(live()){
                progress.textContent=`${done?"导入完成":"导入已暂停"}：新增 ${imported} 条，跳过 ${skipped} 条重复记录。${done?"可在记忆记录中查看。":"再次导入会自动跳过已完成的记录。"}`;
                notify(progress.textContent);
              }
            }finally{importing=false;if(live()){select.disabled=false;stop.hidden=true;stop.disabled=false;}}
          };
        }catch(error){if(live())progress.textContent=error.message;}
        finally{if(live())button.disabled=false;}
      };

      $("#memory-import-text").onsubmit=async event=>{event.preventDefault();if(await confirmAction("将正文资料导入当前会话与人格？"))write("import_text",{text:$("#memory-import-content").value.trim()});};
      $("#memory-text-file").onchange=async event=>{const file=event.target.files[0];if(!file)return;if(file.size>120000){notify("文字文件过大，请缩短至 30000 字内。",true);return;}const text=await file.text();if(text.length>30000)notify("正文不能超过 30000 字。",true);else if(live())$("#memory-import-content").value=text;};
      $("#memory-export").onclick=async()=>{const result=await write("export_bundle",{},false);if(!result)return;const bytes=Uint8Array.from(atob(result.encoded),c=>c.charCodeAt(0)),url=URL.createObjectURL(new Blob([bytes],{type:"application/zip"})),link=document.createElement("a");link.href=url;link.download=result.file_name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);notify("记忆包已生成并开始下载。");};
      $("#memory-install").onclick=async()=>{const file=$("#memory-bundle-file").files[0];if(!file||file.size>16*1024*1024){notify("请选择不超过 16 MB 的原版记忆包。",true);return;}if(!await confirmAction("将所选记忆包安装到当前会话？已有记忆保持。"))return;const encoded=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(",")[1]);reader.onerror=reject;reader.readAsDataURL(file);});write("import_bundle",{encoded});};
      for(const button of body.querySelectorAll("[data-memory-source]"))button.onclick=()=>{const row=rows[Number(button.dataset.memorySource)];storageScope=row.storage_scope||"";deletion("source",row.source);};
    }
    for(const button of root.querySelectorAll("[data-memory-view]"))button.onclick=()=>navigate(()=>{
      view=button.dataset.memoryView;offset=0;
      if(view==="inspection")kind="relation";else if(view==="records"&&!["paragraph","entity","relation","fact","episode"].includes(kind))kind="paragraph";
      return load();
    });
    root.querySelector("#memory-config").onclick=()=>{if(view!=="settings")navigate(()=>{view="settings";return load();});};
    load();
  }
  return {render};
})();
