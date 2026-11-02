// Interactive state is separate from the reader so polling cannot replace drafts.
const interaction = {byTask:new Map(), drafts:new Map(), pending:new Map(), outgoing:new Map(), signatures:new Map(), serial:0, poll:null};
const CONTROL_STATES={queued:'等待设备接收',dispatched:'正在交给原应用',delivered:'原应用已接收',resolved:'原应用已结束该请求',rejected:'原应用未接收',expired:'已过期，未发送',unknown:'结果未知，请检查原应用',cancelled:'已取消，未发送'};
const CONTROL_ERRORS={control_offline:'设备交互连接已断开',request_expired:'该问题已失效，请查看最新状态',operation_in_progress:'上一项操作仍在处理中',command_id_conflict:'操作编号冲突，请刷新状态',invalid_selection:'请选择原问题提供的选项',empty_answer:'请先填写答案',reply_unavailable:'当前会话暂时不能接收回复',native_action_unsupported:'原应用尚不支持此项操作',native_message_starts_with_option:'此版本 Antigravity 接口暂不接受以短横线开头的消息，请改写首行'};
CONTROL_ERRORS.codex_resume_timeout='Codex 未能及时加载原会话，消息尚未发送；请确认桌面应用已登录后重试';
CONTROL_ERRORS.native_session_archived='会话已在原应用中归档，消息未发送';
CONTROL_ERRORS.control_paused='此设备的面板交互已暂停，可在设置中恢复';
CONTROL_ERRORS.policy_changed='原生权限或运行状态已变化，请刷新后重试';
CONTROL_ERRORS.invalid_policy='原应用不支持此权限选项';
CONTROL_ERRORS.create_unavailable='新建入口已变更或不可用，请刷新后重试';
let controlDeviceLoad=0;
async function loadControlDevices() {
 const el=document.getElementById('control-devices');if(!el)return;
 const serial=++controlDeviceLoad;
 try {
  const response=await apiFetch('/api/control/devices');
  if(!response.ok)throw new Error('unavailable');
  const data=await response.json();if(serial!==controlDeviceLoad)return;
  el.innerHTML=data.devices.map(d=>`<article class="control-device"><div><strong>${esc(d.name)}</strong><p>${!d.configured?'尚未接入原应用':!d.enabled?'交互已暂停':d.connected?'已连接':'桥接离线'}${d.connected?` · ${d.sessions} 个可交互会话`:''}</p><small>${Object.entries(d.harnesses).filter(([,n])=>n>0).map(([tool,n])=>esc(({antigravity:'Antigravity',codex:'Codex',dsh:'DeepSeek Harness'})[tool]||tool)+' '+n).join(' · ')||'采集在线与交互连接是独立状态'}</small></div>${d.configured?`<button type="button" onclick="setControlEnabled(${jsArg(d.device_id)},${!d.enabled},this)">${d.enabled?'暂停交互':'恢复交互'}</button>`:''}</article>`).join('')||'<p>尚未发现设备</p>';
 } catch (_) {if(serial===controlDeviceLoad)el.textContent='连接状态读取失败，请刷新重试';}
}
async function setControlEnabled(device,enabled,button) {
 button.disabled=true;
 try {
  const response=await apiFetch('/api/control/devices/'+encodeURIComponent(device),{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled})});
  if(!response.ok)throw new Error('unavailable');
  toast(enabled?'已恢复设备交互':'已暂停设备交互，未派发的操作已取消');
  await loadControlDevices();
 } catch (_) {button.disabled=false;toast('未能更新交互设置，请刷新状态确认');}
}
function interactionDraft(id) {if(!interaction.drafts.has(id))interaction.drafts.set(id,{text:'',createText:'',answers:{}});return interaction.drafts.get(id);}
function setReplyDraft(value) {interactionDraft(workspace.selected).text=value;}
function setAnswerDraft(requestId,questionId,field,value) {
 const d=interactionDraft(workspace.selected);const key=requestId+'\n'+questionId;
 const answer=d.answers[key] ||= {selected:[],custom:''};answer[field]=value;
}
function interactionBusy(id) {
 return interaction.pending.has(id)||interaction.byTask.get(id)?.commands?.some(c=>['queued','dispatched'].includes(c.state));
}
function capturedOutgoing(id) {
 const t=workspace.detail;
 if(!t || (t.ui_key||t.key)!==id)return null;
 return {turn:t.extra?.latest_turn,at:t.activity_at||t.last_event_at||0};
}
function outgoingHtml(id) {
 const message=interaction.outgoing.get(id);if(!message)return '';
 const captured=capturedOutgoing(id),turn=captured?.turn;
 // Equal text alone may be an earlier, identical message. Wait for a newer
 // native activity record as well as delivery before replacing the local echo.
 if(message.state==='delivered'&&captured?.at>message.baselineAt&&
    [turn?.notification,turn?.user].includes(message.text))return '';
 const labels={sending:'发送中',queued:'等待设备接收',dispatched:'正在送达',delivered:'已送达',rejected:'发送失败',expired:'已过期，未发送',cancelled:'已取消',unknown:'送达情况待确认'};
 const warning=['rejected','expired','cancelled','unknown'].includes(message.state);
 return `<article class="message user-message local-outgoing" data-command-id="${esc(message.id)}"><div class="message-label">你<span>${esc(new Date(message.createdAt).toLocaleTimeString())}</span><span class="outgoing-state${warning?' warning':''}" role="status">${esc(labels[message.state]||message.state)}</span></div><div class="message-body outgoing-text">${esc(message.text)}</div>${message.detail?`<p class="capture-note">${esc(CONTROL_ERRORS[message.detail]||message.detail)}</p>`:''}</article>`;
}
function updateOutgoing(id,receipt) {
 const message=interaction.outgoing.get(id);
 if(message&&receipt?.id===message.id)Object.assign(message,{state:receipt.state,detail:receipt.detail||''});
}
function createdSessionHtml(id,receipt) {
 const result=receipt?.result;if(!result)return '';
 const source=workspace.tasks?.find(t=>(t.ui_key||t.key)===id);
 const target=source&&workspace.tasks.find(t=>(t.machine_id?`id:${t.machine_id}`:`name:${t.machine}`)===(source.machine_id?`id:${source.machine_id}`:`name:${source.machine}`) && t.tool===result.tool && t.session_id===result.session_id);
 return target?`<button type="button" onclick="selectSession(${jsArg(target.ui_key||target.key)})">打开新会话 →</button>`:`<p class="capture-note">目标会话 ID：${esc(result.session_id)} · 等待设备同步，可在原应用核实。</p>`;
}
function interactionFeedbackHtml(id) {
 const data=interaction.byTask.get(id),local=interaction.pending.get(id);
 const latest=local?data?.commands?.find(c=>c.id===local.id):data?.commands?.[0];
 if(local?.uncertain)return '<p class="control-receipt warning" role="status">未收到服务器回执，正在核对；请勿重复发送。</p><button type="button" onclick="releaseUncertain()">已检查原应用，解除等待</button>';
 if(local&&!latest)return '<p class="control-receipt" role="status">正在发送…</p>';
 return latest?`<p class="control-receipt ${latest.state==='unknown'?'warning':''}" role="status">${esc(CONTROL_STATES[latest.state]||latest.state)}${latest.detail?' · '+esc(CONTROL_ERRORS[latest.detail]||latest.detail):''}</p>${createdSessionHtml(id,latest)}`:'';
}
function interactionHtml(id) {
 const data=interaction.byTask.get(id),cap=data?.capability,draft=interactionDraft(id);
 const outgoing=`<div class="outgoing-message">${outgoingHtml(id)}</div>`;
 if(!data)return outgoing+'<p class="capture-note">正在检查原应用交互连接…</p>';
 const pending=interactionBusy(id);
 const disabled=pending?' disabled':'';
 let body=outgoing;
 if(data.connected && cap) {
  for(const r of cap.requests||[]) {
   body+=`<form class="interaction-request" onsubmit="event.preventDefault();sendAnswer(${jsArg(r.id)},${jsArg(r.revision)})"><h3>${r.kind==='approval'?'待批准操作':'Agent 的问题'}</h3><div class="native-request-detail">${esc(r.prompt)}</div>`;
   if(r.kind==='approval') {
    for(const choice of r.choices)body+=`<button type="button"${disabled} class="${choice==='approve'?'primary':''}" onclick="sendApproval(${jsArg(r.id)},${jsArg(choice)},${jsArg(r.revision)})">${choice==='approve'?'仅批准这一次':'拒绝执行'}</button>`;
   }else{
    for(const q of r.questions) {
     const key=r.id+'\n'+q.id, answer=draft.answers[key]||{selected:[],custom:''};
     body+=`<fieldset${disabled}><legend>${esc(q.question)}</legend>`;
     q.options.forEach(o=>{body+=`<label class="answer-option"><input type="${q.multiple?'checkbox':'radio'}" name="q-${esc(r.id)}-${esc(q.id)}" value="${esc(o)}"${answer.selected.includes(o)?' checked':''} onchange="readAnswerOptions(this,${jsArg(r.id)},${jsArg(q.id)})"><span>${esc(o)}${q.option_details?.[o]?`<small>${esc(q.option_details[o])}</small>`:''}</span></label>`;});
     if(q.custom)body+=`<label class="answer-custom">${q.options.length?'其他答案':'你的回答'}<textarea rows="2" maxlength="4000" oninput="setAnswerDraft(${jsArg(r.id)},${jsArg(q.id)},'custom',this.value)">${esc(answer.custom)}</textarea></label>`;
     body+='</fieldset>';
    }
    body+=`<button class="primary"${disabled}>提交答案</button>`;
   }
   body+='</form>';
  }
  if(cap.reply)body+=`<form class="reply-composer" onsubmit="event.preventDefault();sendReply()"><label for="native-reply">回复原会话</label><textarea id="native-reply" rows="3" maxlength="16000" placeholder="给这个会话发送后续消息…" oninput="setReplyDraft(this.value)">${esc(draft.text)}</textarea><div class="composer-footer"><span>发送到上方标明的设备与 Harness</span><button class="primary"${disabled}>发送</button></div></form>`;
  if(cap.policy){
    const presets=cap.policy.scope==='session-permissions';
    const custom=presets&&cap.policy.current==='custom';
    body+=`<form class="reply-composer" onsubmit="event.preventDefault();sendPolicy(this,${jsArg(cap.policy.revision)})"><label for="native-policy">${presets?'会话权限预设':'审批策略'}</label><p class="capture-note">${presets?'仅此会话 · 同时切换沙盒与审批设置；不修改新会话默认值':'仅此会话的后续轮次 · 不改变沙盒权限或审批者'}</p><select id="native-policy" name="policy"${disabled}>${custom?'<option value="custom" selected disabled>当前：自定义组合</option>':''}${cap.policy.options.map(o=>`<option value="${esc(o.value)}"${o.value===cap.policy.current?' selected':''}>${esc(o.label)}</option>`).join('')}</select>${presets?cap.policy.options.map(o=>`<p class="capture-note">${esc(o.label)}${o.description?'：'+esc(o.description):''}</p>`).join(''):''}<button type="submit"${disabled}>应用到此会话</button></form>`;
  }
  if(cap.create)body+=`<details class="new-session"><summary>新建 DeepSeek 会话</summary><form class="reply-composer" onsubmit="event.preventDefault();sendCreate(${jsArg(cap.create.revision)})"><p class="capture-note">设备：${esc(workspace.detail?.machine||'当前设备')} · DeepSeek Harness<br>工作目录：${esc(cap.create.cwd)}<br>使用原应用默认 Agent 与权限配置。</p><label for="native-create">新会话的首条消息</label><textarea id="native-create" rows="3" maxlength="16000" oninput="interactionDraft(workspace.selected).createText=this.value">${esc(draft.createText)}</textarea><button class="primary"${disabled}>新建并发送</button></form></details>`;
  if(cap.reason)body+=`<p class="capture-note">${esc(cap.reason)}</p>`;
 }else body+=`<p class="capture-note">${esc(data.reason||'此会话的交互连接暂时不可用')}</p>`;
 return body+`<div class="interaction-feedback" aria-live="polite">${interactionFeedbackHtml(id)}</div>`;
}
function renderInteraction(force=false) {
 const id=workspace.selected,el=document.getElementById('session-control');if(!id||!el)return;
 const data=interaction.byTask.get(id),pending=interaction.pending.get(id);
 const signature=JSON.stringify([id,data,pending?.id,pending?.uncertain,interaction.outgoing.get(id),capturedOutgoing(id)]);
 const structure=JSON.stringify([id,data?.connected,data?.capability?.reply,data?.capability?.create,data?.capability?.policy,(data?.capability?.requests||[]).map(r=>[r.id,r.revision])]);
 if(!force && el.dataset.interactionSignature===signature)return;
 // Keep focused drafts and selection on ordinary polls. A resolved approval may
 // still remove its card; submission revalidates every ID at both Hub and worker.
 if(!force && el.contains(document.activeElement) && el.dataset.task===id && el.dataset.interactionStructure===structure) {
  // Status must advance even while the textarea or Send button retains focus.
  // Update controls in place so newer text and its caret are not replaced.
  const feedback=el.querySelector('.interaction-feedback');
  if(feedback)feedback.innerHTML=interactionFeedbackHtml(id);
  const outgoing=el.querySelector('.outgoing-message');
  if(outgoing)outgoing.innerHTML=outgoingHtml(id);
  for(const control of el.querySelectorAll('form button, form fieldset'))control.disabled=Boolean(interactionBusy(id));
  return;
 }
 el.innerHTML=interactionHtml(id);el.dataset.interactionSignature=signature;el.dataset.interactionStructure=structure;el.dataset.task=id;
}
function watchInteraction(id) {
 if(interaction.poll?.id===id)return;
 if(interaction.poll)clearInterval(interaction.poll.timer);
 const poll={id,until:Date.now()+30000,busy:false};interaction.poll=poll;
 poll.timer=setInterval(async()=>{
  if(workspace.page!=='sessions'||workspace.selected!==id||Date.now()>poll.until||!interactionBusy(id)) {
   clearInterval(poll.timer);if(interaction.poll===poll)interaction.poll=null;return;
  }
  if(poll.busy)return;poll.busy=true;
  try{await refreshInteraction();}finally{poll.busy=false;}
 },500);
}
async function refreshInteraction() {
 if(workspace.page!=='sessions'||!workspace.selected)return;
 const id=workspace.selected,serial=++interaction.serial;
 try {
  const r=await fetch('/api/control/tasks/'+encodeURIComponent(id));
  if(r.status===401){showAuthView(false);return;}
  if(!r.ok)throw new Error('connection');
  const data=await r.json();if(serial!==interaction.serial||workspace.selected!==id)return;
  interaction.byTask.set(id,data);
  const message=interaction.outgoing.get(id);
  if(message)updateOutgoing(id,data.commands?.find(c=>c.id===message.id));
  const local=interaction.pending.get(id),receipt=data.commands?.find(c=>c.id===local?.id);
  if(receipt && !['queued','dispatched'].includes(receipt.state)) {
   interaction.pending.delete(id);
   if(receipt.state==='delivered'&&local?.action.kind==='reply'&&interactionDraft(id).text===local.action.text) {
    interactionDraft(id).text='';
    const input=document.getElementById('native-reply');
    if(workspace.selected===id&&input?.value===local.action.text)input.value='';
   }
  }
  if(receipt?.state==='delivered'&&local?.action.kind==='create'&&interactionDraft(id).createText===local.action.text) {
   interactionDraft(id).createText='';const input=document.getElementById('native-create');
   if(workspace.selected===id&&input?.value===local.action.text)input.value='';
  }
  renderInteraction();
 }catch(_){if(workspace.selected===id){interaction.byTask.set(id,{connected:false,reason:'交互连接暂时不可用，草稿已保留',commands:interaction.byTask.get(id)?.commands||[]});renderInteraction();}}
}
function nativeRequest(id,kind) {
 const data=interaction.byTask.get(workspace.selected);
 return data?.connected?data.capability?.requests.find(r=>r.id===id&&r.kind===kind):null;
}
function newCommandId() {
 // crypto.randomUUID is unavailable on plain LAN HTTP in some browsers.
 const bytes=new Uint8Array(16);crypto.getRandomValues(bytes);bytes[6]=(bytes[6]&15)|64;bytes[8]=(bytes[8]&63)|128;
 const hex=[...bytes].map(b=>b.toString(16).padStart(2,'0')).join('');
 return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
}
async function submitNative(action) {
 const id=workspace.selected;if(!id||interaction.pending.has(id))return;
 const record={id:newCommandId(),action};interaction.pending.set(id,record);
 if(action.kind==='reply')interaction.outgoing.set(id,{id:record.id,text:action.text,createdAt:Date.now(),baselineAt:capturedOutgoing(id)?.at||0,state:'sending',detail:''});
 renderInteraction(true);
 if(action.kind==='reply')document.getElementById('session-control')?.querySelector('.local-outgoing')?.scrollIntoView({block:'nearest'});
 try{
  const r=await apiFetch('/api/control/tasks/'+encodeURIComponent(id),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(record)});
  if(r.status===401){interaction.pending.delete(id);updateOutgoing(id,{id:record.id,state:'rejected',detail:'登录已失效，请重新登录'});showAuthView(false);return;}
  const data=await r.json();
  if(!r.ok){interaction.pending.delete(id);updateOutgoing(id,{id:record.id,state:'rejected',detail:data.error||'操作未接收'});toast(CONTROL_ERRORS[data.error]||'操作未接收，请检查原应用状态');}
  else {interaction.byTask.set(id,{...interaction.byTask.get(id),commands:[data.command]});updateOutgoing(id,data.command);}
 }catch(_){record.uncertain=true;updateOutgoing(id,{id:record.id,state:'unknown',detail:'未收到服务器回执，请勿重复发送'});}
 if(workspace.selected===id){renderInteraction(true);await refreshInteraction();if(interactionBusy(id))watchInteraction(id);}
}
function sendReply(){const value=interactionDraft(workspace.selected).text;if(!value.trim()){toast('请输入回复内容');return;}return submitNative({kind:'reply',text:value});}
function sendApproval(id,decision,revision){const r=nativeRequest(id,'approval');if(!r||r.revision!==revision){toast('批准请求已失效');return;}return submitNative({kind:'approval',request_id:r.id,revision:r.revision,decision});}
function readAnswerOptions(input,requestId,questionId){const selected=[...input.closest('fieldset').querySelectorAll('input:checked')].map(n=>n.value);setAnswerDraft(requestId,questionId,'selected',selected);}
function sendAnswer(id,revision){
 const r=nativeRequest(id,'question');if(!r||r.revision!==revision){toast('问题已失效');return;}
 const draft=interactionDraft(workspace.selected),answers={};
 for(const q of r.questions){const a=draft.answers[r.id+'\n'+q.id]||{selected:[],custom:''};answers[q.id]={selected:!q.multiple&&a.custom.trim()?[]:a.selected,custom:a.custom};}
 return submitNative({kind:'answer',request_id:r.id,revision:r.revision,answers});
}

function releaseUncertain(){const id=workspace.selected;if(interaction.pending.get(id)?.uncertain){interaction.pending.delete(id);renderInteraction(true);}}

function sendCreate(revision) {
 const id=workspace.selected,data=interaction.byTask.get(id),value=interactionDraft(id).createText;
 if(!data?.connected||data.capability?.create?.revision!==revision){toast(CONTROL_ERRORS.create_unavailable);return;}
 if(!value.trim()){toast('请输入新会话的首条消息');return;}
 return submitNative({kind:'create',revision,text:value});
}

function sendPolicy(form,revision) {
 const data=interaction.byTask.get(workspace.selected),policy=data?.capability?.policy;
 if(!data?.connected||!policy||policy.revision!==revision){toast(CONTROL_ERRORS.policy_changed);return;}
 const value=form.elements.policy.value;
 if(!policy.options.some(o=>o.value===value)){toast(CONTROL_ERRORS.invalid_policy);return;}
 if(value===policy.current){toast('已使用该权限选项');return;}
 return submitNative({kind:'policy',revision,value});
}
