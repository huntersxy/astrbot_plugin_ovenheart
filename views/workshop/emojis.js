/* One emoji workspace over the native image store; no second search service. */
window.OvenHeartEmojis = (() => {
  let view="gallery",scope="all",persona="",query="",offset=0;
  function render(context){
    const {root,data,api,escape:esc,notify,confirmAction,scopeOptions,personaOptions}=context;
    let busy=false,version=0,stop=false;
    const live=()=>root.isConnected&&root.querySelector("#emoji-body")===body;
    root.innerHTML=`<div class="memory-heading"><div><h2>表情</h2><p class="muted">管理可发送的表情，直接由聊天规划选择。公共图库可供允许的会话使用；会话图片仍按原会话隔离。</p></div></div><nav class="memory-tabs" aria-label="表情页面">${Object.entries({gallery:"图库",import:"上传与导入",settings:"选择与发送"}).map(([key,label])=>`<button type="button" data-emoji-view="${key}">${label}</button>`).join("")}</nav><div id="emoji-body"></div>`;
    const body=root.querySelector("#emoji-body"),$=selector=>body.querySelector(selector);
    async function write(action,payload){
      try{return await api("emojis/action",{scope:"library",action,payload});}
      catch(error){if(live())notify(error.message,true);}
    }
    async function navigate(work){
      if(busy){notify("表情操作尚未完成，请稍后切换。",true);return;}
      if(context.isDirty()&&!await confirmAction("切换会放弃未保存的表情编辑，继续？"))return;
      context.clearDirty();return work();
    }
    async function load(){
      const token=++version;
      for(const button of root.querySelectorAll("[data-emoji-view]")){if(button.dataset.emojiView===view)button.setAttribute("aria-current","page");else button.removeAttribute("aria-current");}
      if(view==="settings"){settings();return;}
      if(view==="import"){imports();return;}
      body.innerHTML='<p class="muted" role="status">正在读取图库…</p>';
      try{
        const selected=scope;
        const result=await api("emojis",undefined,{scope:selected,persona,query,offset});
        if(!live()||token!==version)return;
        body.innerHTML=`<form id="emoji-search" class="memory-search"><div class="field"><label for="emoji-query">查找表情</label><input id="emoji-query" type="search" maxlength="500" value="${esc(query)}" placeholder="描述、情绪或使用场景"></div><button type="submit">查询</button><details class="local-filter-panel" ${persona||scope!=="all"?"open":""}><summary>筛选 · ${persona||scope!=="all"?"已启用":"全部来源"}</summary><div class="local-filter-grid"><div class="field"><label for="emoji-persona">人格</label><select id="emoji-persona">${personaOptions(persona)}</select></div><div class="field"><label for="emoji-scope">来源</label><select id="emoji-scope">${scopeOptions(scope,persona).replace("全部会话","全部来源")}<option value="library" ${scope==="library"?"selected":""}>公共图库</option></select></div><button type="button" id="emoji-reset">重置筛选</button></div></details><button type="button" id="emoji-refresh">刷新</button></form><p class="muted">共 ${result.total} 张 · 公共图库可编辑；会话候选保留各自来源。</p><div class="emoji-grid">${result.items.map((item,index)=>`<article class="emoji-item"><div class="emoji-picture" id="emoji-image-${index}"><span class="muted">正在读取缩略图…</span></div><small class="emoji-source">${esc(item.scope_label||item.persona||"未标记来源")}</small><form data-emoji-editor="${index}"><label for="emoji-caption-${index}">含义与适用场景</label><textarea id="emoji-caption-${index}" maxlength="1000" required ${item.storage_scope!==data.shared_scope.id?"readonly":""}>${esc(item.observations.find(row=>row.text)?.text||"")}</textarea>${item.storage_scope===data.shared_scope.id?'<div class="actions"><button type="submit">保存描述</button><button type="button" class="text danger" data-emoji-remove>移出图库</button></div>':""}</form></article>`).join("")||'<div class="empty"><h3>还没有可选表情</h3><p>在“上传与导入”添加素材并填写用途，或导入已有 meme_manager 图库。</p></div>'}</div><div class="pagination"><span class="muted">${result.total?offset+1:0}–${Math.min(offset+result.items.length,result.total)} / ${result.total}</span><div class="actions"><button id="emoji-prev" type="button" ${!offset?"disabled":""}>上一页</button><button id="emoji-next" type="button" ${offset+result.limit>=result.total?"disabled":""}>下一页</button></div></div>`;
        $("#emoji-search").onsubmit=event=>{event.preventDefault();navigate(()=>{scope=$("#emoji-scope").value;persona=$("#emoji-persona").value;query=$("#emoji-query").value.trim();offset=0;load();});};
        $("#emoji-persona").onchange=event=>navigate(()=>{persona=event.target.value;scope="all";offset=0;load();});
        $("#emoji-scope").onchange=event=>navigate(()=>{scope=event.target.value;offset=0;load();});
        $("#emoji-reset").onclick=()=>navigate(()=>{scope="all";persona="";query="";offset=0;load();});
        $("#emoji-refresh").onclick=()=>navigate(load);
        $("#emoji-prev").onclick=()=>navigate(()=>{offset=Math.max(0,offset-result.limit);load();});
        $("#emoji-next").onclick=()=>navigate(()=>{offset+=result.limit;load();});
        for(const form of body.querySelectorAll("[data-emoji-editor]")){
          const item=result.items[Number(form.dataset.emojiEditor)];
          form.onsubmit=async event=>{event.preventDefault();if(busy)return;busy=true;try{if(await write("emoji_caption",{id:item.id,text:form.querySelector("textarea").value.trim()})){context.clearDirty();notify("表情描述已保存。");}}finally{busy=false;}};
          const remove=form.querySelector("[data-emoji-remove]");
          if(remove)remove.onclick=async()=>{if(busy||!await confirmAction("将这张表情移出公共图库？之后不再提供给聊天规划选择。"))return;busy=true;try{if(await write("emoji_remove",{id:item.id})){context.clearDirty();await load();}}finally{busy=false;}};
        }
        // Bound decoded thumbnail memory and concurrent native Panel requests.
        const queue=result.items.entries();
        await Promise.all(Array.from({length:4},async()=>{for(const [index,item] of queue){if(!live()||token!==version)return;try{const image=await api("emojis/image",undefined,{scope:item.scope===data.shared_scope.id?"library":item.scope||"all",id:item.id,storage_scope:item.storage_scope});if(!live()||token!==version)return;const img=document.createElement("img");img.src=`data:image/jpeg;base64,${image.encoded}`;img.alt="表情预览";img.loading="lazy";$("#emoji-image-"+index).replaceChildren(img);}catch{if(live()&&token===version)$("#emoji-image-"+index).textContent="缩略图暂不可用，刷新后重试。";}}}));
      }catch(error){if(live()&&token===version){body.innerHTML=`<div class="empty"><h3>图库读取未完成</h3><p>${esc(error.message)}</p><button id="emoji-retry" type="button">重试</button></div>`;$("#emoji-retry").onclick=load;}}
    }
    function imports(){
      body.innerHTML='<section class="emoji-import"><h3>上传表情</h3><p class="muted">添加到公共图库。填写含义或使用场景，Planner 可直接根据描述选择，无需额外情感模型。支持 PNG、JPEG、WebP、GIF，单图上限 10 MB。</p><form id="emoji-upload"><label for="emoji-file">表情图片</label><input id="emoji-file" type="file" accept=".png,.jpg,.jpeg,.webp,.gif" required><label for="emoji-upload-caption">含义与适用场景</label><textarea id="emoji-upload-caption" maxlength="1000" required placeholder="例如：猫猫点头，用于赞同、确认收到"></textarea><button type="submit" class="primary">添加到图库</button></form><h3>导入 meme_manager 图库</h3><p class="muted">读取同一 AstrBot 中的已安装表情包，复制原图与已有描述。源素材保持原样；按图片内容去重，可在批次间暂停。导入后由烤箱之心自己的规划器选择，不再为自己的规划回复重复调用情感模型。</p><button type="button" id="emoji-read-packs">读取已有表情包</button><div id="emoji-packs"></div><p id="emoji-import-status" class="muted" role="status" aria-live="polite"></p></section>';
      $("#emoji-upload").onsubmit=async event=>{event.preventDefault();if(busy)return;const file=$("#emoji-file").files[0];if(!file||file.size>10*1024*1024){notify("请选择不超过 10 MB 的图片。",true);return;}busy=true;const button=event.currentTarget.querySelector("button");button.disabled=true;button.textContent="正在添加…";try{const encoded=await encode(file);if(await write("emoji_upload",{encoded,text:$("#emoji-upload-caption").value.trim()})){context.clearDirty();notify("表情已添加到公共图库。");view="gallery";scope="all";persona="";query="";offset=0;await load();}}finally{busy=false;if(live())button.disabled=false;}};
      $("#emoji-read-packs").onclick=async()=>{if(busy)return;const button=$("#emoji-read-packs"),panel=$("#emoji-packs");button.disabled=true;try{const result=await api("emojis",undefined,{view:"packs"});if(!live())return;if(!result.packs.length){panel.innerHTML='<p class="muted">未找到已安装的 meme_manager 图库；也可以直接上传表情。</p>';return;}panel.innerHTML=`<label for="emoji-pack">来源表情包</label><select id="emoji-pack">${result.packs.map(pack=>`<option value="${esc(pack.id)}">${esc(pack.id)} · ${pack.count} 张</option>`).join("")}</select><div class="actions"><button type="button" id="emoji-start-import" class="primary">导入到公共图库</button><button type="button" id="emoji-stop-import" hidden>完成本批后暂停</button></div>`;
        $("#emoji-start-import").onclick=async()=>{if(busy||!await confirmAction("将所选表情包导入公共图库？素材可供所有允许使用表情的会话选择，重复图片自动跳过。"))return;busy=true;stop=false;let cursor=0,done=false;const status=$("#emoji-import-status"),pause=$("#emoji-stop-import"),start=$("#emoji-start-import"),pack=$("#emoji-pack").value;start.disabled=true;pause.hidden=false;pause.onclick=()=>{stop=true;pause.disabled=true;};try{while(live()&&!stop){status.textContent=`已处理 ${cursor} 张来源记录，正在导入下一批…`;const result=await write("emoji_import",{pack,offset:cursor});if(!result||!live())break;cursor=result.offset;done=result.done;if(done)break;}if(live()){context.clearDirty();status.textContent=`${done?"导入完成":"已暂停"}：处理 ${cursor} 张记录，重复图片不会重复入库。可到图库查看；再次导入会自动去重。`;notify(status.textContent);}}finally{busy=false;if(live()){start.disabled=false;pause.hidden=true;pause.disabled=false;}}};
      }catch(error){if(live())notify(error.message,true);}finally{if(live())button.disabled=false;}};
    }
    function encode(file){return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(",")[1]);reader.onerror=reject;reader.readAsDataURL(file);});}
    function settings(){
      const s=data.settings;
      body.innerHTML=`<section class="emoji-settings"><h3>选择与发送</h3><p class="muted">上传或导入素材并填写用途后，机器人会在合适的闲聊中主动使用表情。由 Planner 选择表情，正文由 AstrBot 当前会话模型生成，不追加情感判断。</p><form id="emoji-settings"><label class="emoji-switch"><input type="checkbox" name="emoji_enabled" ${s.emoji_enabled?"checked":""}><span>启用 Planner 表情动作<small>可随文字附一张表情，或单独发表情；正文先发送。</small></span></label><label class="emoji-switch"><input type="checkbox" name="emoji_static_to_gif" ${s.emoji_static_to_gif?"checked":""}><span>静态图转 GIF 发送<small>开启后，烤箱之心托管回复中的静态图片转为 1 帧 GIF。GIF 和其他动态图保持原样；不修改图库原文件，不留下临时文件。</small></span></label><label for="emoji-frequency">主动推荐比例 <output id="emoji-frequency-value" for="emoji-frequency">${Math.round(s.emoji_frequency*100)}%</output></label><input id="emoji-frequency" name="emoji_frequency" type="range" min="0" max="100" step="5" value="${Math.round(s.emoji_frequency*100)}" aria-describedby="emoji-frequency-help"><p id="emoji-frequency-help" class="muted">每轮按此比例提前提供候选，模型结合语境决定是否发送。0% 仅按明确请求使用；100% 每轮提供，不代表每条必发。建议 50%～80%。</p><label for="emoji-cooldown">主动表情最短间隔（秒）</label><input id="emoji-cooldown" name="emoji_cooldown" type="number" min="0" max="3600" step="1" value="${s.emoji_cooldown}" required aria-describedby="emoji-cooldown-help"><p id="emoji-cooldown-help" class="muted">同一会话成功发表情后，在此间隔内暂停主动推荐。0 表示不限制间隔；用户明确索要表情仍可发送。</p><label class="emoji-switch"><input type="checkbox" name="planner_visual_enabled" ${s.planner_visual_enabled?"checked":""}><span>视觉候选拼图<small>需要支持图片的规划模型，会增加图片输入；关闭时按描述选择。</small></span></label><p class="muted">可在「设置 → 模型分工」选择独立表情视觉模型，需要支持图片；留空沿用 AstrBot 图片描述模型。仅未提供候选序号时调用，失败时会显示实际状态。</p><p class="muted">当前前置条件：图片记忆${s.image_memory_enabled?"已开启":"未开启"} · 通用记忆${s.native_memory_enabled?"已开启":"未开启"} · 记忆辅助${s.memory_enabled?"已开启":"未开启"}。可在“记忆 → 记忆设置”调整。选图优先按用途检索全库；复用“记忆设置”的文字向量模型，未配置时按字词匹配。描述向量分批缓存，不再追加情感判断模型。</p><button type="submit" class="primary">保存表情设置</button></form></section>`;
      $("#emoji-frequency").oninput=event=>{$("#emoji-frequency-value").textContent=`${event.target.value}%`;};
      $("#emoji-settings").onsubmit=event=>{event.preventDefault();const form=event.currentTarget;context.saveSettings({emoji_enabled:form.elements.emoji_enabled.checked,emoji_static_to_gif:form.elements.emoji_static_to_gif.checked,emoji_frequency:Number(form.elements.emoji_frequency.value)/100,emoji_cooldown:Number(form.elements.emoji_cooldown.value),planner_visual_enabled:form.elements.planner_visual_enabled.checked});};
    }
    for(const button of root.querySelectorAll("[data-emoji-view]"))button.onclick=()=>navigate(()=>{view=button.dataset.emojiView;return load();});
    load();
  }
  return {render};
})();
