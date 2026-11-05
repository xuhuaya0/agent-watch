// Read-only native state and separate, explicitly scoped actions. No model or
// permission choices are invented here; only offered capabilities can be sent.
const sessionOptions={id:null,kind:null,signature:'',policyRevision:null,value:null,confirmed:false,advanced:false,changed:false,message:''};
function harnessName(cap) {return ({codex:'Codex',antigravity:'Antigravity',dsh:'DeepSeek Harness'})[cap?.tool||workspace.detail?.tool]||'原应用';}
function permissionLabel(cap) {
 const policy=cap.policy;
 if(cap.settings?.permission)return cap.settings.permission;
 if(policy && policy.scope!=='session-next-turn')return policy.options.find(option=>option.value===policy.current)?.label;
}
function hasSessionSettings(cap) {
 return Boolean(cap && (cap.policy || cap.settings?.model || cap.settings?.reasoning || cap.settings?.permission || cap.settings?.note));
}
function settingsSummaryHtml(cap) {
 if(!hasSessionSettings(cap))return '';
 const model=cap.settings?.model;
 const permission=permissionLabel(cap);
 const permissionText=permission && (cap.settings?.scope==='project'?'项目权限 · '+permission:permission);
 const shortPermission=cap.tool==='codex'?permissionText?.split(' · ')[0]:permissionText;
 const modelText=model && (cap.tool==='antigravity'?'记录模型 · '+model:model);
 const title=[modelText,permissionText].filter(Boolean).join(' · ')||'会话设置';
 return `<button type="button" class="composer-settings" aria-label="会话设置" aria-haspopup="dialog" title="${esc(title)}" aria-description="${esc(title)}" onclick="openSessionOptions('settings')">
  ${modelText?`<span class="composer-model">${esc(modelText)}</span>`:''}${shortPermission?`<span class="composer-permission">${esc(shortPermission)}</span>`:''}${modelText||permissionText?'':'会话设置'}<span class="composer-settings-chevron" aria-hidden="true">⌄</span></button>`;
}
function settingsHtml(cap,disabled) {
 const s=cap.settings||{},h=harnessName(cap),p=cap.policy;
 const permission=permissionLabel(cap);
 const nativeSettings=cap.tool==='codex'?'模型和完整权限模式在 Codex 中选择。':`模型在 ${esc(h)} 中选择。`;
 return `<div class="native-settings-values"><div><span>${cap.tool==='antigravity'?'原生记录模型':'当前模型'}</span><strong>${esc(s.model||'原应用尚未提供')}</strong></div>${s.reasoning?`<div><span>推理强度</span><strong>${esc(s.reasoning)}</strong></div>`:''}<div><span>${s.scope==='project'?'项目权限':'权限范围'}</span><strong>${esc(permission||'原应用尚未提供')}</strong></div></div>
 <p class="settings-note">${nativeSettings}</p>
 ${s.note&&cap.tool!=='codex'?`<p class="settings-note">${esc(s.note)}</p>`:''}
 ${cap.reason && cap.reply?`<p class="settings-note">${esc(cap.reason)}</p>`:''}
 ${p?(p.scope==='session-next-turn'?`<details class="advanced-settings"${sessionOptions.advanced?' open':''} ontoggle="sessionOptions.advanced=this.open"><summary>高级 · 审批规则</summary>${policyForm(p,disabled)}</details>`:policyForm(p,disabled)):'<p class="settings-note">当前权限仅供查看。</p>'}`;
}
function policyForm(policy,disabled) {
 const project=policy.scope==='project-permissions',presets=policy.scope==='session-permissions';
 const target=policy.project_name||policy.project_id;
 const title=project?'项目权限':presets?'会话权限':'审批规则';
 const note=project?`影响项目「${target}」中的所有会话。`:
  presets?'仅此会话 · 同时切换沙盒与审批设置；不修改新会话默认值。':'仅此会话的后续轮次 · 不改变沙盒权限或审批者，也不等同于 Codex 的完整权限模式。';
 const value=sessionOptions.policyRevision===policy.revision?sessionOptions.value:policy.current;
 const selected=policy.options.find(o=>o.value===value);
 const current=policy.options.find(o=>o.value===policy.current)?.label||(policy.current==='custom'?'自定义组合':policy.current);
 const changed=value!==policy.current;
 const choices=policy.options.map(o=>`<label class="permission-choice${o.value===value?' selected':''}"><input type="radio" name="policy" value="${esc(o.value)}"${o.value===value?' checked':''} onchange="chooseSessionPolicy(this.value)"><span>${esc(o.label)}</span>${o.value===policy.current?'<small>当前</small>':''}</label>`).join('');
 const description=selected?.description||(policy.scope==='session-next-turn'?(value==='never'?'需要批准的操作将被拒绝，不会自动获得权限。':'由 Codex 按当前审批者设置处理需要批准的操作。'):'');
 return `<form class="native-policy-form" onsubmit="event.preventDefault();sendPolicy(this,${jsArg(policy.revision)})"><div class="settings-section-heading"><h3>${title}</h3><span class="scope-badge">${project?'整个项目':'此会话'}</span></div><p class="settings-note">${esc(note)}</p><fieldset${disabled}><legend class="sr-only">选择${title}</legend>${choices}</fieldset>${!policy.options.some(o=>o.value===policy.current)?`<p class="settings-note">当前：${esc(current)}（仅显示）</p>`:''}<p id="policy-description" class="policy-description">${esc(description)}</p>${project?`<label class="project-confirm"><input type="checkbox" name="confirmProject"${sessionOptions.confirmed?' checked':''}${disabled} onchange="sessionOptions.confirmed=this.checked;updatePolicyApply()">确认更改项目「${esc(target)}」的权限</label>`:''}<div class="options-footer"><span>${changed?'尚未应用':'当前设置'}</span><button id="policy-apply" class="primary" type="submit"${disabled||!changed||(project&&!sessionOptions.confirmed)?' disabled':''}>${project?'应用到整个项目':'应用到此会话'}</button></div></form>`;
}
function creationForm(cap,draft,disabled) {
 const h=harnessName(cap),create=cap.create,openDraft=create.mode==='draft';
 const brief=workspace.tasks?.find(t=>(t.ui_key||t.key)===workspace.selected)||workspace.detail;
 const location=create.location==='native-project'?['项目',create.project_name||create.project_id]:['工作目录',create.cwd];
 const description=openDraft?'在目标设备的 Codex 中打开预填草稿，需要在 Codex 点击发送后才会创建会话。':create.location==='native-project'?'沿用当前会话的模型，权限使用目标项目设置。':'使用原应用默认模型与权限。';
 return `<form class="new-conversation-form" onsubmit="event.preventDefault();sendCreate(${jsArg(create.revision)})"><div class="creation-destination"><div><span>设备 / 应用</span><strong>${esc(brief?.machine||'当前设备')} · ${esc(h)}</strong></div><div><span>${location[0]}</span><strong>${esc(location[1])}</strong></div></div><p class="settings-note">${description}</p><label for="native-create">首条消息</label><textarea id="native-create" rows="5" maxlength="16000" required placeholder="描述新会话要做的事…" oninput="interactionDraft(workspace.selected).createText=this.value">${esc(draft.createText)}</textarea><div class="options-footer"><button class="primary"${disabled}>${openDraft?'在 Codex 打开草稿':'新建并发送'}</button></div></form>`;
}
function openSessionOptions(kind) {
 const data=interaction.byTask.get(workspace.selected);
 if(!data?.connected||!data.capability)return;
 if(kind!=='create' && kind!=='settings')return;
 if(kind==='create' && !data.capability.create)return;
 if(kind==='settings' && !hasSessionSettings(data.capability))return;
 if(Array.isArray(workspace.tasks) && !workspace.tasks.some(task=>(task.ui_key||task.key)===workspace.selected))return;
 Object.assign(sessionOptions,{id:workspace.selected,kind,signature:'',policyRevision:null,value:null,confirmed:false,advanced:false,changed:false,message:''});
 renderSessionOptions();
 const dialog=document.getElementById('session-options');dialog.onkeydown=trapSessionOptionsFocus;dialog.showModal();
}
function trapSessionOptionsFocus(event) {
 const dialog=document.getElementById('session-options');if(event.key!=='Tab'||!dialog?.open)return;
 const candidates=Array.from(dialog.querySelectorAll('button,input,select,textarea,a[href],summary,[tabindex]'))
  .filter(el=>{
   if(el.tabIndex<0||el.matches(':disabled')||el.closest('[hidden],[inert]')||!el.getClientRects().length)return false;
   // Some browsers keep layout rects for descendants of collapsed details.
   // Only its first direct summary (and that summary's children) is focusable.
   for(let ancestor=el.parentElement;ancestor&&ancestor!==dialog;ancestor=ancestor.parentElement){
    if(ancestor.tagName==='DETAILS'&&!ancestor.open){
     const summary=Array.from(ancestor.children).find(child=>child.tagName==='SUMMARY');
     if(!summary||(summary!==el&&!summary.contains(el)))return false;
    }
   }
   return true;
  });
 // A radio group has one native Tab stop: its checked option, or its first
 // available option when no choice is checked. Arrow keys remain native.
 const stops=candidates.filter(el=>{
  if(el.type!=='radio'||!el.name)return true;
  const group=candidates.filter(other=>other.type==='radio'&&other.name===el.name&&other.form===el.form);
  return el===(group.find(other=>other.checked)||group[0]);
 });
 const active=document.activeElement;
 if(!stops.length){event.preventDefault();dialog.focus();return;}
 if(!stops.includes(active)||(event.shiftKey?active===stops[0]:active===stops[stops.length-1])){
  event.preventDefault();(event.shiftKey?stops[stops.length-1]:stops[0]).focus();
 }
}
function closeSessionOptions() {document.getElementById('session-options')?.close();sessionOptionsClosed();}
function sessionOptionsClosed() {sessionOptions.id=null;sessionOptions.kind=null;sessionOptions.signature='';}
function chooseSessionPolicy(value) {
 sessionOptions.value=value;sessionOptions.confirmed=false;sessionOptions.message='';renderSessionOptions(true);
 document.querySelector('#session-options input[name="policy"]:checked')?.focus({preventScroll:true});
}
function updatePolicyApply() {
 const p=interaction.byTask.get(sessionOptions.id)?.capability?.policy,button=document.getElementById('policy-apply');
 if(button)button.disabled=!p||interactionBusy(sessionOptions.id)||sessionOptions.value===p.current||(p.scope==='project-permissions'&&!sessionOptions.confirmed);
}
function renderSessionActions() {
 const el=document.getElementById('session-actions'),id=workspace.selected,data=interaction.byTask.get(id);
 const selectedTask=workspace.tasks?.find(task=>(task.ui_key||task.key)===id);
 const present=!Array.isArray(workspace.tasks)||Boolean(selectedTask);
 if(el) {
  const destination=[selectedTask?.machine,harnessName(data?.capability)].filter(Boolean).join(' · ');
  const html=present && id && data?.connected && data.capability?.create ?
   `<button type="button" aria-haspopup="dialog" title="${esc(destination)}" onclick="openSessionOptions('create')">＋ 新建会话</button>` : '';
  if(el.innerHTML!==html)el.innerHTML=html;
 }
 if(sessionOptions.id&&(workspace.selected!==sessionOptions.id||workspace.page!=='sessions'||!present))closeSessionOptions();
 else renderSessionOptions();
}
function renderSessionOptions(force=false) {
 const id=sessionOptions.id;if(!id)return;
 const data=interaction.byTask.get(id),cap=data?.capability;
 const signature=JSON.stringify([data?.connected,cap?.settings,cap?.policy,cap?.create,interactionBusy(id)]);
 const el=document.getElementById('session-options-body');if(!el)return;
 if(!force&&signature===sessionOptions.signature){const feedback=el.querySelector('.interaction-feedback');if(feedback)feedback.innerHTML=interactionFeedbackHtml(id,sessionOptions.kind==='settings'?'policy':'create');return;}
 const p=cap?.policy;
 if(sessionOptions.policyRevision!==p?.revision) {
  sessionOptions.changed=Boolean(sessionOptions.policyRevision);
  sessionOptions.policyRevision=p?.revision;sessionOptions.value=p?.current;sessionOptions.confirmed=false;
 }
 sessionOptions.signature=signature;
 const title=sessionOptions.kind==='create'?`新建 ${harnessName(cap)} 会话`:'会话设置';
 const body=!data?.connected||!cap?'<p class="settings-note">交互连接暂时不可用，草稿已保留。连接恢复后可继续。</p>':sessionOptions.kind==='create'?(cap?.create?creationForm(cap,interactionDraft(id),interactionBusy(id)?' disabled':''):'<p class="settings-note">原应用当前未提供新建入口。</p>'):settingsHtml(cap,interactionBusy(id)?' disabled':'');
 // The dialog sits outside the polled detail pane, so ordinary conversation
 // updates do not recreate fields or steal the caret.
 const active=el.contains(document.activeElement)?document.activeElement:null;
 const focusId=active?.id,caret=active&&typeof active.selectionStart==='number'?[active.selectionStart,active.selectionEnd]:null;
 el.innerHTML=`<div class="options-heading"><div><small>${esc(harnessName(cap))}</small><h2 id="session-options-title">${title}</h2></div><button type="button" aria-label="关闭" onclick="closeSessionOptions()">×</button></div>${sessionOptions.changed&&sessionOptions.kind==='settings'?'<p class="settings-change" role="status">原应用状态已更新，请重新确认选择。</p>':''}${body}<div class="options-error" role="alert">${esc(sessionOptions.message||'')}</div><div class="interaction-feedback" aria-live="polite">${interactionFeedbackHtml(id,sessionOptions.kind==='settings'?'policy':'create')}</div>`;
 if(focusId){const input=document.getElementById(focusId);input?.focus({preventScroll:true});if(input&&caret)input.setSelectionRange(...caret);}
}
