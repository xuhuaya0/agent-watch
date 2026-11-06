// Native choices in anchored menus; only creation uses a modal dialog.
const sessionOptions={id:null,kind:null,signature:'',policyRevision:null,policyCurrent:null,value:null,modelRevision:null,modelCurrent:null,currentEffort:null,modelValue:null,effortValue:null,confirmed:false,advanced:false,changed:false,message:'',anchor:null,confirmValue:null,modelConfirmed:false,modelConfirm:false,commandId:null};
function harnessName(cap) {return ({codex:'Codex',antigravity:'Antigravity',dsh:'DeepSeek Harness'})[cap?.tool||workspace.detail?.tool]||'原应用';}
function policyCurrent(policy) {return policy?.current_option??policy?.current;}
function permissionLabel(cap) {const p=cap?.policy;return p?.current_label||cap?.settings?.permission||p?.options?.find(o=>o.value===policyCurrent(p))?.label;}
function modelEfforts(option) {return (option?.efforts||[]).map(value=>typeof value==='string'?{value,label:option?.effort_labels?.[value]||value}:value);}
function effortLabel(cap) {const m=cap?.model,option=m?.options.find(o=>o.value===m.current);return modelEfforts(option).find(o=>o.value===m.effort)?.label||cap?.settings?.reasoning||m?.effort;}
function settingsSummaryHtml(cap) {
 const model=cap.model?.current_label||cap.model?.options?.find(o=>o.value===cap.model.current)?.label||cap.settings?.model||cap.model?.current;
 const project=cap.settings?.scope==='project'||cap.policy?.scope==='project-permissions';
 const entries=[['model',cap.tool==='antigravity'&&!cap.model?'记录模型':'模型',model]];
 if(effortLabel(cap)||cap.model?.options.some(o=>modelEfforts(o).length))entries.push(['effort','推理强度',effortLabel(cap)||'默认']);
 entries.push(['permission',project?'项目权限':'权限',permissionLabel(cap)]);
 return `<div class="composer-setting-controls">${entries.map(([kind,label,value])=>`<button type="button" class="composer-settings" data-setting-kind="${kind}" aria-label="${label}：${esc(value||'尚未读取')}" aria-haspopup="menu" aria-expanded="${sessionOptions.id===workspace.selected&&sessionOptions.kind===kind}" title="${label}：${esc(value||'尚未读取')}" onclick="openSessionOptions('${kind}',this)"><span class="sr-only">${label}：</span><span class="composer-${kind}">${esc(value||'未读取')}</span><span class="composer-settings-chevron" aria-hidden="true">⌄</span></button>`).join('')}</div>`;
}
function menuChoice(kind,value,label,current,disabled=false,note='') {
 return `<button type="button" class="native-menu-choice${current?' selected':''}" role="menuitemradio" aria-checked="${Boolean(current)}" data-choice="${esc(value??'')}"${disabled?' disabled':''}${note?` title="${esc(note)}"`:''} onclick="${kind==='model'?'chooseSessionModel':kind==='effort'?'chooseModelEffort':'chooseSessionPolicy'}(${jsArg(value)})"><span class="menu-check" aria-hidden="true">${current?'✓':''}</span><span class="menu-choice-label">${esc(label)}</span>${disabled?`<small>${current?'当前':'不可选'}</small>`:''}</button>`;
}
function currentChoiceHtml(name,value,label,selected) {return menuChoice(name,value,label,selected,true);}
function modelSettingsHtml(cap,disabled) {
 const m=cap.model,known=m?.current_label||m?.options.find(o=>o.value===m.current)?.label||cap.settings?.model||m?.current;
 if(!m)return `${known?menuChoice('model','',known,true,true):''}<p class="menu-note">${cap.tool==='antigravity'?'记录中的模型；当前连接未提供切换。':'当前连接未提供可选模型。'}</p>`;
 const value=sessionOptions.modelValue??m.current;
 return `<div role="menu" aria-label="模型">${!m.options.some(o=>o.value===m.current)?menuChoice('model',m.current,known||m.current,value===m.current,true):''}${m.options.map(o=>menuChoice('model',o.value,o.label+(o.provider_label?' · '+o.provider_label:''),value===o.value,Boolean(disabled))).join('')}</div>`;
}
function effortSettingsHtml(cap,disabled) {
 const m=cap.model,value=sessionOptions.modelValue??m?.current,option=m?.options.find(o=>o.value===value),options=modelEfforts(option),effort=sessionOptions.effortValue;
 const known=options.find(o=>o.value===effort)?.label||cap.settings?.reasoning||effort||'原应用默认';
 return `<div role="menu" aria-label="推理强度">${!options.some(o=>o.value===effort)?menuChoice('effort',effort,known,true,true):''}${options.map(o=>menuChoice('effort',o.value,o.label,o.value===effort,Boolean(disabled))).join('')}</div>${!options.length?'<p class="menu-note">当前模型未提供可选强度。</p>':''}`;
}
function permissionSettingsHtml(cap,disabled) {
 const p=cap.policy;
 if(!p)return `${permissionLabel(cap)?menuChoice('permission','',permissionLabel(cap),true,true):''}<p class="menu-note">当前连接未提供可选权限。</p>`;
 return policyForm(p,disabled);
}
function policyForm(policy,disabled) {
 const current=policyCurrent(policy),value=sessionOptions.value??current,project=policy.scope==='project-permissions',target=policy.project_name||policy.project_id;
 const available=policy.options||[],unavailable=policy.unavailable_options||[];
 const all=[...available,...unavailable];
 if(policy.current_option) {const rank=o=>o.label==='Ask for approval'?0:o.value==='guardian-approvals'?1:o.value==='full-access'?2:3;all.sort((a,b)=>rank(a)-rank(b));}
 const rows=all.filter(o=>o.group!=='advanced'||o.value===current),advanced=all.filter(o=>o.group==='advanced'&&o.value!==current);
 const choice=o=>menuChoice('permission',o.value,o.label,o.value===value,Boolean(disabled)||!available.includes(o),o.reason||o.description||'');
 const currentRow=all.some(o=>o.value===current)?'':menuChoice('permission',current,policy.current_label||current,true,true);
 const reasons=[...new Set(unavailable.map(o=>o.reason).filter(Boolean))].map(reason=>`<p class="menu-note">${esc(reason)}</p>`).join('');
 const confirmation=sessionOptions.confirmValue!==null;
 const selected=all.find(o=>o.value===sessionOptions.confirmValue);
 return `${project?`<p class="menu-scope">项目：${esc(target)}</p>`:policy.scope==='session-next-turn'?'<p class="menu-note">审批规则 · 当前连接不提供完整权限模式</p>':''}<div role="menu" aria-label="权限">${currentRow}${rows.map(choice).join('')}${advanced.length?`<details class="menu-more"${sessionOptions.advanced?' open':''} ontoggle="sessionOptions.advanced=this.open"><summary>其他原生配置</summary>${advanced.map(choice).join('')}</details>`:''}</div>${reasons}${confirmation?`<div class="menu-confirm"><p>${project?`将项目「${esc(target)}」的所有会话设为`:'允许此会话使用'}「${esc(selected?.label||sessionOptions.confirmValue)}」？</p><div><button type="button" onclick="cancelPermissionChoice()"${disabled}>取消</button><button type="button" class="primary" onclick="confirmPermissionChoice()"${disabled}>确认切换</button></div></div>`:''}`;
}
function creationForm(cap,draft,disabled) {
 const h=harnessName(cap),create=cap.create,openDraft=create.mode==='draft';
 const brief=workspace.tasks?.find(t=>(t.ui_key||t.key)===workspace.selected)||workspace.detail;
 const location=create.location==='native-project'?['项目',create.project_name||create.project_id]:['工作目录',create.cwd];
 const description=openDraft?'在目标设备的 Codex 中打开预填草稿，需要在 Codex 点击发送后才会创建会话。':create.location==='native-project'?'沿用当前会话的模型，权限使用目标项目设置。':'使用原应用默认模型与权限。';
 return `<form class="new-conversation-form" onsubmit="event.preventDefault();sendCreate(${jsArg(create.revision)})"><div class="creation-destination"><div><span>设备 / 应用</span><strong>${esc(brief?.machine||'当前设备')} · ${esc(h)}</strong></div><div><span>${location[0]}</span><strong>${esc(location[1])}</strong></div></div><p class="settings-note">${description}</p><label for="native-create">首条消息</label><textarea id="native-create" rows="5" maxlength="16000" required placeholder="描述新会话要做的事…" oninput="interactionDraft(workspace.selected).createText=this.value">${esc(draft.createText)}</textarea><div class="options-footer"><button class="primary"${disabled}>${openDraft?'在 Codex 打开草稿':'新建并发送'}</button></div></form>`;
}
function openSessionOptions(kind,anchor=null) {
 const data=interaction.byTask.get(workspace.selected);
 if(!data?.connected||!data.capability||!['create','model','effort','permission'].includes(kind))return;
 if(kind==='create'&&!data.capability.create)return;
 if(Array.isArray(workspace.tasks)&&!workspace.tasks.some(t=>(t.ui_key||t.key)===workspace.selected))return;
 if(sessionOptions.id===workspace.selected&&sessionOptions.kind===kind&&document.getElementById('session-options')?.open){closeSessionOptions();return;}
 const dialog=document.getElementById('session-options');
 if(dialog.open)dialog.close();
 Object.assign(sessionOptions,{id:workspace.selected,kind,signature:'',policyRevision:null,policyCurrent:null,value:null,modelRevision:null,modelCurrent:null,currentEffort:null,modelValue:null,effortValue:null,confirmed:false,advanced:false,changed:false,message:'',confirmValue:null,modelConfirmed:false,modelConfirm:false,commandId:null,anchor:anchor||document.querySelector(`[data-setting-kind="${kind}"]`)});
 dialog.classList?.toggle('session-menu',kind!=='create');
 dialog.onkeydown=kind==='create'?trapSessionOptionsFocus:sessionMenuKeydown;
 renderSessionOptions();
 updateMenuTriggers();
 if(kind==='create')dialog.showModal();else {dialog.show();positionSessionMenu();focusSessionMenu();}
}
function focusSessionMenu(){const dialog=document.getElementById('session-options');(dialog.querySelector('[aria-checked="true"]:not(:disabled)')||dialog.querySelector('[role="menuitemradio"]:not(:disabled)')||dialog.querySelector('button'))?.focus({preventScroll:true});}
function positionSessionMenu() {
 const dialog=document.getElementById('session-options');if(!dialog?.open||sessionOptions.kind==='create'||!sessionOptions.id)return;
 const anchor=document.querySelector(`[data-setting-kind="${sessionOptions.kind}"]`)||sessionOptions.anchor;
 const rect=anchor?.getBoundingClientRect?.();if(!rect||typeof window==='undefined')return;
 const margin=8,width=Math.min(300,window.innerWidth-margin*2);
 const above=Math.max(100,rect.top-margin*2),below=window.innerHeight-rect.bottom-margin*2;
 dialog.style.width=width+'px';dialog.style.maxHeight=Math.min(420,Math.max(above,below))+'px';
 dialog.style.left=Math.max(margin,Math.min(rect.left,window.innerWidth-width-margin))+'px';
 dialog.style.top='auto';dialog.style.bottom='auto';
 if(above>=Math.min(260,below))dialog.style.bottom=(window.innerHeight-rect.top+margin)+'px';else dialog.style.top=(rect.bottom+margin)+'px';
}
function sessionMenuKeydown(event) {
 const dialog=document.getElementById('session-options');
 if(event.key==='Escape'){event.preventDefault();closeSessionOptions();return;}
 if(event.key==='Tab'){closeSessionOptions();return;}
 if(!['ArrowDown','ArrowUp','Home','End'].includes(event.key))return;
 const choices=[...dialog.querySelectorAll('[role="menuitemradio"]:not(:disabled),.menu-confirm button:not(:disabled)')].filter(el=>{
  if(!el.getClientRects().length)return false;
  for(let parent=el.parentElement;parent&&parent!==dialog;parent=parent.parentElement)if(parent.tagName==='DETAILS'&&!parent.open)return false;
  return true;
 });
 if(!choices.length)return;event.preventDefault();const index=choices.indexOf(document.activeElement);
 const next=event.key==='Home'?0:event.key==='End'?choices.length-1:(index+(event.key==='ArrowDown'?1:-1)+choices.length)%choices.length;
 choices[next].focus();
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
function closeSessionOptions() {const anchor=document.querySelector(`[data-setting-kind="${sessionOptions.kind}"]`)||sessionOptions.anchor;document.getElementById('session-options')?.close();sessionOptionsClosed();anchor?.focus?.({preventScroll:true});}
function updateMenuTriggers(){for(const button of document.querySelectorAll?.('[data-setting-kind]')||[])button.setAttribute('aria-expanded',String(sessionOptions.id===workspace.selected&&sessionOptions.kind===button.dataset.settingKind));}
function sessionOptionsClosed() {
 // A queued close event from the previous picker must not clear a newly opened one.
 if(document.getElementById('session-options')?.open)return;
 sessionOptions.id=null;sessionOptions.kind=null;sessionOptions.signature='';sessionOptions.confirmValue=null;updateMenuTriggers();
}
function sessionOptionsActionKind() {return sessionOptions.kind==='permission'?'policy':sessionOptions.kind==='effort'?'model':sessionOptions.kind;}
async function applyMenuModel() {
 const id=sessionOptions.id,model=interaction.byTask.get(id)?.capability?.model;if(id!==workspace.selected||!model)return;
 if(model.scope==='session-and-default'&&!sessionOptions.modelConfirmed){sessionOptions.modelConfirm=true;renderSessionOptions(true);return;}
 const sending=sendModel({elements:{model:{value:sessionOptions.modelValue},effort:{value:sessionOptions.effortValue??''}}},sessionOptions.modelRevision);
 sessionOptions.commandId=interaction.pending.get(id)?.id||null;
 await sending;
 finishMenuAction(id);
}
function chooseSessionModel(value) {
 const m=interaction.byTask.get(sessionOptions.id)?.capability?.model,selected=m?.options.find(o=>o.value===value);if(!selected||interactionBusy(sessionOptions.id))return;
 if(value===m.current){closeSessionOptions();return;}
 sessionOptions.modelValue=value;sessionOptions.message='';sessionOptions.changed=false;sessionOptions.modelConfirmed=false;sessionOptions.modelConfirm=false;
 const efforts=modelEfforts(selected);
 if(!efforts.some(o=>o.value===sessionOptions.effortValue))sessionOptions.effortValue=efforts.find(o=>o.value===selected.default_effort)?.value??null;
 if(efforts.length&&!efforts.some(o=>o.value===sessionOptions.effortValue)){sessionOptions.kind='effort';sessionOptions.message='请选择推理强度';renderSessionOptions(true);positionSessionMenu();return;}
 return applyMenuModel();
}
function chooseModelEffort(value) {
 const m=interaction.byTask.get(sessionOptions.id)?.capability?.model;
 if(interactionBusy(sessionOptions.id)||!modelEfforts(m?.options.find(o=>o.value===sessionOptions.modelValue)).some(o=>o.value===value))return;
 if(sessionOptions.modelValue===m.current&&value===m.effort){closeSessionOptions();return;}
 sessionOptions.effortValue=value;sessionOptions.message='';sessionOptions.changed=false;sessionOptions.modelConfirmed=false;sessionOptions.modelConfirm=false;return applyMenuModel();
}
function chooseSessionPolicy(value) {
 const p=interaction.byTask.get(sessionOptions.id)?.capability?.policy;
 if(!p||interactionBusy(sessionOptions.id)||!p.options.some(o=>o.value===value))return;
 if(value===policyCurrent(p)){closeSessionOptions();return;}
 sessionOptions.value=value;sessionOptions.message='';sessionOptions.changed=false;sessionOptions.confirmed=false;sessionOptions.confirmValue=null;
 if(p.scope==='project-permissions'||value==='full-access'||value==='danger-full-access') {sessionOptions.confirmValue=value;renderSessionOptions(true);positionSessionMenu();return;}
 return applyMenuPolicy();
}
function cancelPermissionChoice() {if(interactionBusy(sessionOptions.id))return;const p=interaction.byTask.get(sessionOptions.id)?.capability?.policy;sessionOptions.confirmValue=null;sessionOptions.value=policyCurrent(p);renderSessionOptions(true);focusSessionMenu();}
function confirmPermissionChoice() {if(sessionOptions.confirmValue!==sessionOptions.value)return;sessionOptions.confirmed=true;return applyMenuPolicy();}
async function applyMenuPolicy() {
 const id=sessionOptions.id,p=interaction.byTask.get(id)?.capability?.policy;if(id!==workspace.selected||!p)return;
 const sending=sendPolicy({elements:{policy:{value:sessionOptions.value},confirmProject:{checked:sessionOptions.confirmed}}},sessionOptions.policyRevision);
 sessionOptions.commandId=interaction.pending.get(id)?.id||null;
 await sending;
 finishMenuAction(id);
}
function finishMenuAction(id) {
 if(sessionOptions.id!==id||!sessionOptions.commandId)return false;
 const receipt=interaction.byTask.get(id)?.commands?.find(c=>c.id===sessionOptions.commandId);
 if(['delivered','resolved'].includes(receipt?.state)){closeSessionOptions();return true;}
 return false;
}
function modelConfirmationHtml(disabled) {
 if(!sessionOptions.modelConfirm)return '';
 return `<div class="menu-confirm"><p>同时更新此会话和后续默认模型，与 DeepSeek 原应用一致。</p><div><button type="button" onclick="cancelModelChoice()"${disabled}>取消</button><button type="button" class="primary" onclick="confirmModelChoice()"${disabled}>确认切换</button></div></div>`;
}
function cancelModelChoice(){if(interactionBusy(sessionOptions.id))return;const m=interaction.byTask.get(sessionOptions.id)?.capability?.model;sessionOptions.modelConfirm=false;sessionOptions.modelConfirmed=false;sessionOptions.modelValue=m?.current;sessionOptions.effortValue=m?.effort;renderSessionOptions(true);focusSessionMenu();}
function confirmModelChoice(){if(!sessionOptions.modelConfirm)return;sessionOptions.modelConfirmed=true;return applyMenuModel();}
function updatePolicyApply() {}
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
 const id=sessionOptions.id;if(!id||finishMenuAction(id))return;
 const data=interaction.byTask.get(id),cap=data?.capability,kind=sessionOptions.kind,modelKind=kind==='model'||kind==='effort';
 const facts=modelKind?[cap?.model,cap?.settings?.model,cap?.settings?.reasoning]:kind==='permission'?[cap?.policy,cap?.settings?.permission,cap?.settings?.scope]:[cap?.create];
 const signature=JSON.stringify([data?.connected,cap?.tool,facts,interactionBusy(id)]),el=document.getElementById('session-options-body');if(!el)return;
 if(!force&&signature===sessionOptions.signature){const feedback=el.querySelector('.interaction-feedback');if(feedback)feedback.innerHTML=interactionFeedbackHtml(id,sessionOptionsActionKind());return;}
 const p=cap?.policy,current=policyCurrent(p);
 if(kind==='permission'&&p&&sessionOptions.policyRevision!==p.revision){sessionOptions.changed=Boolean(sessionOptions.policyRevision&&sessionOptions.value!==sessionOptions.policyCurrent&&sessionOptions.value!==current);sessionOptions.policyRevision=p.revision;sessionOptions.policyCurrent=current;sessionOptions.value=current;sessionOptions.confirmed=false;sessionOptions.confirmValue=null;}
 const m=cap?.model;
 if(modelKind&&m&&sessionOptions.modelRevision!==m.revision){const chosen=m.options.find(o=>o.value===sessionOptions.modelValue),dirty=sessionOptions.modelValue!==sessionOptions.modelCurrent||sessionOptions.effortValue!==sessionOptions.currentEffort,matches=m.current===sessionOptions.modelValue&&(!modelEfforts(chosen).length||m.effort===sessionOptions.effortValue);sessionOptions.changed=Boolean(sessionOptions.modelRevision&&dirty&&!matches);sessionOptions.modelRevision=m.revision;sessionOptions.modelCurrent=m.current;sessionOptions.currentEffort=m.effort;sessionOptions.modelValue=m.current;sessionOptions.effortValue=m.effort;sessionOptions.modelConfirm=false;sessionOptions.modelConfirmed=false;}
 sessionOptions.signature=signature;
 const title=kind==='create'?`新建 ${harnessName(cap)} 会话`:kind==='model'?'模型':kind==='effort'?'推理强度':p?.scope==='project-permissions'||cap?.settings?.scope==='project'?'项目权限':'权限',disabled=interactionBusy(id)?' disabled':'';
 const body=!data?.connected||!cap?'<p class="menu-note">交互连接暂时不可用</p>':kind==='create'?(cap.create?creationForm(cap,interactionDraft(id),disabled):'<p class="settings-note">原应用当前未提供新建入口。</p>'):kind==='model'?modelSettingsHtml(cap,disabled):kind==='effort'?effortSettingsHtml(cap,disabled):permissionSettingsHtml(cap,disabled);
 const active=el.contains(document.activeElement)?document.activeElement:null,focusId=active?.id,choice=active?.getAttribute?.('data-choice'),caret=active&&typeof active.selectionStart==='number'?[active.selectionStart,active.selectionEnd]:null;
 el.innerHTML=`<div class="${kind==='create'?'options-heading':'menu-heading'}"><h2 id="session-options-title">${title}</h2><button type="button" aria-label="关闭" onclick="closeSessionOptions()">×</button></div>${sessionOptions.changed&&kind!=='create'?'<p class="settings-change" role="status">原应用状态已更新，请重新选择。</p>':''}${body}${modelKind?modelConfirmationHtml(disabled):''}<div class="options-error" role="alert">${esc(sessionOptions.message||'')}</div><div class="interaction-feedback" aria-live="polite">${interactionFeedbackHtml(id,sessionOptionsActionKind())}</div>`;
 if(focusId){const input=document.getElementById(focusId);input?.focus({preventScroll:true});if(input&&caret)input.setSelectionRange(...caret);}else if(choice!=null){[...el.querySelectorAll('[data-choice]')].find(n=>n.getAttribute('data-choice')===choice)?.focus({preventScroll:true});}
 positionSessionMenu();
}
document.addEventListener?.('pointerdown',event=>{const dialog=document.getElementById('session-options');if(dialog?.open&&sessionOptions.kind!=='create'&&!dialog.contains(event.target)&&!event.target.closest?.('[data-setting-kind]'))closeSessionOptions();});
if(typeof window!=='undefined'){window.addEventListener('resize',positionSessionMenu);window.addEventListener('scroll',positionSessionMenu,true);}
