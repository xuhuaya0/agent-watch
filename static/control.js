// Interactive state is separate from the reader so polling cannot replace drafts.
const interaction = {byTask:new Map(), drafts:new Map(), pending:new Map(), outgoing:new Map(), queueMutations:new Set(), signatures:new Map(), serial:0, poll:null};
const CONTROL_STATES={queued:'等待设备接收',dispatched:'正在交给原应用',delivered:'原应用已接收',resolved:'原应用已结束该请求',rejected:'原应用未接收',expired:'已过期，未发送',unknown:'结果未知，请检查原应用',cancelled:'已取消，未发送'};
const CONTROL_ERRORS={control_offline:'设备交互连接已断开',request_expired:'该问题已失效，请查看最新状态',operation_in_progress:'上一项操作仍在处理中',command_id_conflict:'操作编号冲突，请刷新状态',invalid_selection:'请选择原问题提供的选项',empty_answer:'请先填写答案',reply_unavailable:'当前会话暂时不能接收回复',native_action_unsupported:'原应用尚不支持此项操作',native_message_starts_with_option:'此版本 Antigravity 接口暂不接受以短横线开头的消息，请改写首行'};
CONTROL_ERRORS.codex_resume_timeout='Codex 未能及时加载原会话，消息尚未发送；请确认桌面应用已登录后重试';
CONTROL_ERRORS.native_session_archived='会话已在原应用中归档，消息未发送';
CONTROL_ERRORS.native_connection_changed='原生会话连接已变化，消息未发送；请核对原会话后重新提交';
CONTROL_ERRORS.control_paused='此设备的面板交互已暂停，可在设置中恢复';
CONTROL_ERRORS.policy_changed='原生权限或运行状态已变化，请刷新后重试';
CONTROL_ERRORS.invalid_policy='原应用不支持此权限选项';
CONTROL_ERRORS.model_changed='原生模型或运行状态已变化，请刷新后重试';
CONTROL_ERRORS.invalid_model='请选择原应用提供的模型和推理强度';
CONTROL_ERRORS.create_unavailable='新建入口已变更或不可用，请刷新后重试';
CONTROL_STATES.waiting='等待当前运行结束';
Object.assign(CONTROL_ERRORS,{reply_queue_changed:'这条消息已离开等待列表，请核对最新状态',reply_turn_changed:'当前运行已变化，未插入；请核对等待列表',reply_queue_unavailable:'设备尚未支持等待发送，请更新交互桥接',reply_queue_full:'等待列表已满，请先发送或取消已有消息',reply_waiting:'等待当前运行结束'});
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
function setReplyDraft(value) {
 interactionDraft(workspace.selected).text=value;
 const send=document.getElementById('composer-send');
 if(send)send.disabled=!value.trim() || Boolean(interactionBusy(workspace.selected));
}
function composerKeydown(event) {
 if(event.key!=='Enter' || event.shiftKey || event.altKey)return;
 const input=event.target;
 // Some IMEs end composition before their confirming Enter keydown. Keep
 // that key separate from an intentional send, including Safari's 229 case.
 if(event.isComposing || event.keyCode===229 || input.composerComposing) {
  input.composerImeEnter=true;return;
 }
 event.preventDefault();
 if(event.repeat || input.composerImeEnter || (input.composerCompositionEndedAt!==undefined&&Date.now()-input.composerCompositionEndedAt<200))return;
 const button=input.form?.querySelector('.composer-send');
 if(button&&!button.disabled&&!interactionBusy(workspace.selected)&&interactionDraft(workspace.selected).text.trim())input.form.requestSubmit(button);
}
function composerComposition(event) {
 event.target.composerComposing=event.type==='compositionstart';
 if(event.type==='compositionend')event.target.composerCompositionEndedAt=Date.now();
}
function composerKeyup(event) {
 if(event.key==='Enter') {
  event.target.composerImeEnter=false;
  delete event.target.composerCompositionEndedAt;
 }
}
function setAnswerDraft(requestId,questionId,field,value) {
 const d=interactionDraft(workspace.selected);const key=requestId+'\n'+questionId;
 const answer=d.answers[key] ||= {selected:[],custom:''};answer[field]=value;
}
function interactionBusy(id) {
 return interaction.pending.has(id)||interaction.byTask.get(id)?.commands?.some(c=>['queued','dispatched'].includes(c.state));
}
function replyWaiting(id) {
 const data=interaction.byTask.get(id),delivery=data?.capability?.delivery;
 return Boolean(delivery&&!delivery.native_queue&&(delivery.state!=='idle'||!data.capability.reply)||data?.commands?.some(c=>c.state==='waiting'));
}
function replyQueueHtml(id) {
 const data=interaction.byTask.get(id),delivery=data?.capability?.delivery;
 const rows=(data?.commands||[]).filter(c=>c.state==='waiting'&&c.kind==='reply').slice().reverse();
 if(!rows.length)return '';
 return `<div class="reply-queue" aria-label="待发送消息"><span class="capture-note">等待当前运行结束 · ${rows.length}</span>${rows.map(row=>`<div class="queued-reply" data-command-id="${esc(row.id)}"><span class="queued-reply-text" title="${esc(row.text||'')}">${esc(row.text||'待发送消息')}</span><div class="queued-reply-actions">${data.connected&&delivery?.steer?`<button type="button" onclick="manageQueuedReply(${jsArg(row.id)},'steer',${jsArg(delivery.revision)})"${interaction.queueMutations.has(row.id)?' disabled':''}>立即插入</button>`:''}<button type="button" aria-label="取消待发送消息" onclick="manageQueuedReply(${jsArg(row.id)},'cancel')"${interaction.queueMutations.has(row.id)?' disabled':''}>取消</button></div></div>`).join('')}</div>`;
}
async function manageQueuedReply(commandId,operation,revision) {
 const id=workspace.selected;if(interaction.queueMutations.has(commandId))return;
 interaction.queueMutations.add(commandId);renderInteraction();
 try {
  const response=await apiFetch('/api/control/tasks/'+encodeURIComponent(id),{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:commandId,operation,...(revision?{revision}:{})})});
  const data=await response.json();
  if(!response.ok)controlNotice(CONTROL_ERRORS[data.error]||'未能更新等待消息，请核对状态',id,'reply');
 }catch(_){controlNotice('未收到操作回执，正在核对等待列表；不会自动重发',id,'reply');}
 finally {interaction.queueMutations.delete(commandId);if(workspace.selected===id){await refreshInteraction();watchInteraction(id);}}
}
function capturedOutgoing(id) {
 const t=workspace.detail;
 if(!t || (t.ui_key||t.key)!==id)return null;
 return {turn:t.extra?.latest_turn,at:t.activity_at||t.last_event_at||0};
}
function receiptDetail(receipt) {
 const detail=receipt.detail||'';
 if(detail===CONTROL_STATES[receipt.state])return '';
 if(receipt.state==='delivered' && (detail==='原应用已接收' || detail==='原应用已接收；不代表 Agent 已完成执行'))return '';
 if(receipt.state==='resolved' && detail==='原应用已结束该请求；请查看原应用的最终结果')return '请在原应用查看最终结果';
 return CONTROL_ERRORS[detail]||detail;
}
function historyContainsOutgoing(id,message) {
 const history=typeof histories!=='undefined'?histories.get(id):null;
 if(!history?.loaded||!Array.isArray(message.previousHistoryIds))return false;
 const submittedSecond=Math.floor((message.serverCreatedAt||message.createdAt/1000));
 // Native history removes surrounding whitespace from user messages. Keep
 // internal whitespace exact, and still require a new identity and timestamp.
 const submittedText=message.text.trim();
 return history.messages.some(row=>row.role==='user'&&typeof row.text==='string'&&row.text.trim()===submittedText&&
  row.id&&!message.previousHistoryIds.includes(row.id)&&
  (message.historyBaselineAt?Date.parse(row.timestamp)>message.historyBaselineAt:Math.floor(Date.parse(row.timestamp)/1000)>=submittedSecond));
}
function outgoingHtml(id) {
 const message=interaction.outgoing.get(id);if(!message)return '';
 if(message.state==='waiting')return '';
 const captured=capturedOutgoing(id),turn=captured?.turn;
 // Equal text alone may be an earlier, identical message. Wait for a newer
 // native activity record as well as delivery before replacing the local echo.
 if(message.state==='delivered'&&captured?.at>message.baselineAt&&
    [turn?.notification,turn?.user].includes(message.text))return '';
 // The native message may arrive before its control receipt. Once a new native
 // identity/time/text matches, render that message just once; the receipt is
 // separate evidence and must not hold a duplicate optimistic bubble on screen.
 if(['sending','queued','dispatched','delivered'].includes(message.state)&&(message.confirmedInHistory||historyContainsOutgoing(id,message))) {
  message.confirmedInHistory=true;return '';
 }
 const labels={sending:'发送中',queued:'等待设备接收',dispatched:'正在送达',delivered:'已送达',rejected:'发送失败',expired:'已过期，未发送',cancelled:'已取消',unknown:'送达情况待确认'};
 const warning=['rejected','expired','cancelled','unknown'].includes(message.state);
 const detail=receiptDetail(message);
 return `<article class="message user-message local-outgoing" data-command-id="${esc(message.id)}"><div class="message-label">${message.notification?'Agent Watch · 系统通知':'你'}<span>${esc(new Date(message.createdAt).toLocaleTimeString())}</span><span class="outgoing-state${warning?' warning':''}" role="status">${esc(labels[message.state]||message.state)}</span></div><div class="message-body outgoing-text">${esc(message.text)}</div>${detail?`<p class="capture-note">${esc(detail)}</p>`:''}</article>`;
}
function updateOutgoing(id,receipt) {
 const message=interaction.outgoing.get(id);
 if(message&&receipt?.id===message.id)Object.assign(message,{state:receipt.state,detail:receipt.detail||'',...(receipt.created?{serverCreatedAt:receipt.created}:{})});
}
function createdSessionHtml(id,receipt) {
 const result=receipt?.result;if(!result)return '';
 const source=workspace.tasks?.find(t=>(t.ui_key||t.key)===id);
 const target=source&&workspace.tasks.find(t=>(t.machine_id?`id:${t.machine_id}`:`name:${t.machine}`)===(source.machine_id?`id:${source.machine_id}`:`name:${source.machine}`) && t.tool===result.tool && (t.tool==='codex'?(t.extra?.thread_id||t.session_id):t.session_id)===result.session_id);
 return target?`<button type="button" onclick="selectSession(${jsArg(target.ui_key||target.key)})">打开新会话 →</button>`:`<p class="capture-note">目标会话 ID：${esc(result.session_id)} · 等待设备同步，可在原应用核实。</p>`;
}
function interactionFeedbackHtml(id,kind=null) {
 const data=interaction.byTask.get(id),pending=interaction.pending.get(id);
 const matches=actionKind=>kind?kind===actionKind:!['policy','model','create'].includes(actionKind);
 const local=pending&&matches(pending.action?.kind)?pending:null;
 const latest=local?data?.commands?.find(c=>c.id===local.id):data?.commands?.find(c=>matches(c.kind));
 if(!kind&&latest?.kind==='reply'&&latest.state==='waiting')return '';
 if((kind?local:pending)?.uncertain)return '<p class="control-receipt warning" role="status">未收到服务器回执，正在核对；请勿重复发送。</p><button type="button" onclick="releaseUncertain()">已检查原应用，解除等待</button>';
 // A local reply carries its delivery receipt on the outgoing message itself.
 const outgoing=interaction.outgoing.get(id);
 if(!kind && outgoing && outgoing.id===(local?.id||latest?.id))return '';
 // A rejected HTTP request never enters the broker's receipt list.
 if(!kind && !local && outgoing?.previousReceiptIds?.includes(latest?.id))return '';
 if(local&&!latest)return '<p class="control-receipt" role="status">正在发送…</p>';
 const actionKind=local?.action?.kind||latest?.kind;
 const label=({reply:'回复',answer:'回答问题',approval:'单次审批',create:'新建会话',policy:'权限设置',model:'模型设置'})[actionKind]||'操作';
 const detail=latest?receiptDetail(latest):'';
 const when=latest?.created?' · '+new Date(latest.created*1000).toLocaleTimeString():'';
 const settingConfirmed=latest?.state==='resolved'&&['model','policy'].includes(actionKind);
 const state=settingConfirmed?'原应用已确认':CONTROL_STATES[latest?.state]||latest?.state;
 const status=settingConfirmed&&detail.startsWith('原应用已确认')?detail:state+(detail?' · '+detail:'');
 return latest?`<p class="control-receipt ${latest.state==='unknown'?'warning':''}" role="status">最近${label}${esc(when)}：${esc(status)}</p>${createdSessionHtml(id,latest)}`:'';
}
function controlNotice(message,id=workspace.selected,kind=null) {
 const dialog=document.getElementById('session-options');
 if(typeof sessionOptions!=='undefined'&&dialog?.open&&sessionOptions.id===id&&
    (!kind||kind===sessionOptionsActionKind())) {
  sessionOptions.message=message;renderSessionOptions(true);
 }else toast(message);
}
function interactionHtml(id) {
 const data=interaction.byTask.get(id),cap=data?.capability,draft=interactionDraft(id);
 if(!data)return '<p class="capture-note" role="status">正在连接原应用…</p>';
 const pending=interactionBusy(id);
 const disabled=pending?' disabled':'';
 let body='';
 if(data.connected && cap) {
  if(cap.requests?.length)body+='<div class="composer-requests">';
  for(const r of cap.requests||[]) {
   const prompt=r.prompt?.trim();
   const shownByLegend=r.kind==='question' && r.questions.some(question=>question.question?.trim()===prompt);
   body+=`<form class="interaction-request" onsubmit="event.preventDefault();sendAnswer(${jsArg(r.id)},${jsArg(r.revision)})"><h3>${r.kind==='approval'?'待批准操作':'Agent 的问题'}</h3>${prompt&&!shownByLegend?`<div class="native-request-detail">${esc(r.prompt)}</div>`:''}`;
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
  if(cap.requests?.length)body+='</div>';
  const settings=`<div class="settings-summary-slot">${settingsSummaryHtml(cap)}</div>`;
  if(cap.reply||cap.delivery) {
   const placeholder=cap.tool==='antigravity'?'向 Antigravity 发送系统通知…':`回复 ${harnessName(cap)}…`;
   body+=`<div class="reply-queue-slot">${replyQueueHtml(id)}</div><form class="reply-composer" onsubmit="event.preventDefault();sendReply()">
    <label for="native-reply" class="sr-only">回复原会话</label>
    <textarea id="native-reply" rows="2" maxlength="16000" placeholder="${esc(placeholder)}" aria-description="Enter 发送，Shift+Enter 换行" onkeydown="composerKeydown(event)" onkeyup="composerKeyup(event)" oncompositionstart="composerComposition(event)" oncompositionend="composerComposition(event)" oninput="setReplyDraft(this.value)">${esc(draft.text)}</textarea>
    <div class="composer-footer">${settings}<button type="submit" id="composer-send" class="primary composer-send" aria-keyshortcuts="Enter Control+Enter Meta+Enter" title="${replyWaiting(id)?'等待当前运行结束后发送':'发送'}（Enter；Shift+Enter 换行）"${pending||!draft.text.trim()?' disabled':''}><span class="composer-send-label">${replyWaiting(id)?'排队':'发送'}</span><svg class="icon" aria-hidden="true"><use href="#icon-arrow"></use></svg></button></div></form>`;
  }else {
   if(cap.reason || !cap.requests?.length)body+=`<p class="capture-note">${esc(cap.reason||'当前会话暂时无法回复。')}</p>`;
   body+=settings;
  }
 }else body+=`<p class="capture-note">${esc(data.reason||'此会话的交互连接暂时不可用')}</p>`;
 return body+`<div class="interaction-feedback" aria-live="polite">${interactionFeedbackHtml(id)}</div>`;
}
function renderInteraction(force=false) {
 if(typeof renderSessionActions==='function')renderSessionActions();
 const id=workspace.selected,el=document.getElementById('session-control');if(!id||!el)return;
 const data=interaction.byTask.get(id),pending=interaction.pending.get(id);
 const outgoing=document.getElementById('chat-outgoing'),markup=outgoingHtml(id);
 if(outgoing && outgoing.renderedEcho!==markup){outgoing.innerHTML=markup;outgoing.renderedEcho=markup;}
 const signature=JSON.stringify([id,data,pending?.id,pending?.uncertain,interaction.outgoing.get(id),capturedOutgoing(id),[...interaction.queueMutations]]);
 const structure=JSON.stringify([id,data?.connected,data?.reason,data?.capability?.reply,Boolean(data?.capability?.delivery),data?.capability?.reason,(data?.capability?.requests||[]).map(r=>[r.id,r.revision])]);
 if(!force && el.dataset.interactionSignature===signature)return;
 // Keep focused drafts and selection on ordinary polls. A resolved approval may
 // still remove its card; submission revalidates every ID at both Hub and worker.
 if(el.dataset.task===id && el.dataset.interactionStructure===structure) {
  // Status must advance even while the textarea or Send button retains focus.
  // Update controls in place so newer text and its caret are not replaced.
  const feedback=el.querySelector('.interaction-feedback');
  if(feedback){
   const receipt=interactionFeedbackHtml(id);
   if(feedback.renderedReceipt!==receipt){feedback.innerHTML=receipt;feedback.renderedReceipt=receipt;}
  }
  const summary=el.querySelector('.settings-summary-slot');
  if(summary){
   const markup=settingsSummaryHtml(data?.capability||{});
   if(summary.renderedSettings!==markup){summary.innerHTML=markup;summary.renderedSettings=markup;}
  }
  for(const control of el.querySelectorAll('.interaction-request button,.interaction-request fieldset'))control.disabled=Boolean(interactionBusy(id));
  const send=document.getElementById('composer-send');
  if(send){send.disabled=Boolean(interactionBusy(id))||!interactionDraft(id).text.trim();const label=send.querySelector?.('.composer-send-label');if(label)label.textContent=replyWaiting(id)?'排队':'发送';send.title=(replyWaiting(id)?'等待当前运行结束后发送':'发送')+'（Enter；Shift+Enter 换行）';}
  const queue=el.querySelector('.reply-queue-slot');
  if(queue){const markup=replyQueueHtml(id);if(queue.renderedQueue!==markup){queue.innerHTML=markup;queue.renderedQueue=markup;}}
  el.dataset.interactionSignature=signature;
  return;
 }
 el.innerHTML=interactionHtml(id);el.dataset.interactionSignature=signature;el.dataset.interactionStructure=structure;el.dataset.task=id;
 const summary=el.querySelector('.settings-summary-slot');
 if(summary)summary.renderedSettings=settingsSummaryHtml(data?.capability||{});
 const feedback=el.querySelector('.interaction-feedback');
 if(feedback)feedback.renderedReceipt=interactionFeedbackHtml(id);
}
function watchInteraction(id) {
 if(interaction.poll?.id===id)return;
 if(interaction.poll)clearInterval(interaction.poll.timer);
 const poll={id,until:Date.now()+30000,busy:false};interaction.poll=poll;
 poll.timer=setInterval(async()=>{
  if(workspace.page!=='sessions'||workspace.selected!==id||Date.now()>poll.until||!interactionBusy(id)&&!replyWaiting(id)) {
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
  if(serial!==interaction.serial||workspace.selected!==id)return;
  if(r.status===401){showAuthView(false);return;}
  if(!r.ok)throw new Error('connection');
  const data=await r.json();if(serial!==interaction.serial||workspace.selected!==id)return;
  interaction.byTask.set(id,data);
  const message=interaction.outgoing.get(id);
  if(message)updateOutgoing(id,data.commands?.find(c=>c.id===message.id));
  const local=interaction.pending.get(id),receipt=data.commands?.find(c=>c.id===local?.id);
  if(receipt && !['queued','dispatched'].includes(receipt.state)) {
   interaction.pending.delete(id);
   if(['delivered','waiting'].includes(receipt.state)&&local?.action.kind==='reply'&&interactionDraft(id).text===local.action.text) {
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
 }catch(_){if(serial===interaction.serial&&workspace.selected===id){interaction.byTask.set(id,{connected:false,reason:'交互连接暂时不可用，草稿已保留',commands:interaction.byTask.get(id)?.commands||[]});renderInteraction();}}
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
 if(typeof sessionOptions!=='undefined'&&sessionOptions.id===id)sessionOptions.message='';
 const record={id:newCommandId(),action};interaction.pending.set(id,record);
 if(action.kind==='reply'){
  const before=interaction.byTask.get(id);
  interaction.outgoing.set(id,{
   id:record.id,text:action.text,createdAt:Date.now(),baselineAt:capturedOutgoing(id)?.at||0,
   previousReceiptIds:(before?.commands||[]).map(command=>command.id),
   previousHistoryIds:typeof histories!=='undefined'?(histories.get(id)?.messages||[]).map(message=>message.id):[],
   historyBaselineAt:typeof histories!=='undefined'?Math.max(0,...(histories.get(id)?.messages||[]).map(message=>Date.parse(message.timestamp)||0)):0,
   state:'sending',detail:'',notification:before?.capability?.tool==='antigravity'
  });
 }
 renderInteraction(true);
 if(action.kind==='reply')document.getElementById('chat-outgoing')?.querySelector('.local-outgoing')?.scrollIntoView({block:'nearest'});
 try{
  const r=await apiFetch('/api/control/tasks/'+encodeURIComponent(id),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(record)});
  if(r.status===401){interaction.pending.delete(id);updateOutgoing(id,{id:record.id,state:'rejected',detail:'登录已失效，请重新登录'});showAuthView(false);return;}
  const data=await r.json();
  if(!r.ok){interaction.pending.delete(id);updateOutgoing(id,{id:record.id,state:'rejected',detail:data.error||'操作未接收'});controlNotice(CONTROL_ERRORS[data.error]||'操作未接收，请检查原应用状态',id,action.kind);}
  else {const before=interaction.byTask.get(id);interaction.byTask.set(id,{...before,commands:[{...data.command,...(data.command.state==='waiting'?{text:action.text}:{})},...(before?.commands||[]).filter(command=>command.id!==data.command.id)]});updateOutgoing(id,data.command);}
 }catch(_){record.uncertain=true;updateOutgoing(id,{id:record.id,state:'unknown',detail:'未收到服务器回执，请勿重复发送'});}
 if(workspace.selected===id){renderInteraction(true);await refreshInteraction();if(interactionBusy(id))watchInteraction(id);}
}
function sendReply(){const value=interactionDraft(workspace.selected).text;if(!value.trim()){toast('请输入回复内容');return;}const cap=interaction.byTask.get(workspace.selected)?.capability;return submitNative({kind:'reply',text:value,...(cap?.delivery?{mode:'after-turn'}:{})});}
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
 if(!data?.connected||data.capability?.create?.revision!==revision){controlNotice(CONTROL_ERRORS.create_unavailable);return;}
 if(!value.trim()){controlNotice('请输入新会话的首条消息');return;}
 const project=data.capability.create.location==='native-project'?{project_id:data.capability.create.project_id}:{};
 return submitNative({kind:'create',revision,text:value,...project});
}

function sendPolicy(form,revision) {
 const data=interaction.byTask.get(workspace.selected),policy=data?.capability?.policy;
 if(!data?.connected||!policy||policy.revision!==revision){controlNotice(CONTROL_ERRORS.policy_changed);return;}
 if(policy.scope==='project-permissions'&&!form.elements.confirmProject?.checked){controlNotice('请先确认目标项目');return;}
 const value=form.elements.policy.value;
 if(!policy.options.some(o=>o.value===value)){controlNotice(CONTROL_ERRORS.invalid_policy);return;}
 if(value===(policy.current_option??policy.current)){controlNotice('已使用该权限选项');return;}
 const project=policy.scope==='project-permissions'?{project_id:policy.project_id}:{};
 return submitNative({kind:'policy',revision,value,...project});
}

function sendModel(form,revision) {
 const id=workspace.selected,data=interaction.byTask.get(id),model=data?.capability?.model;
 if(!data?.connected||!model||model.revision!==revision){controlNotice(CONTROL_ERRORS.model_changed,id,'model');return;}
 if(interactionBusy(id)){controlNotice(CONTROL_ERRORS.operation_in_progress,id,'model');return;}
 const value=form.elements.model?.value,selected=model.options.find(option=>option.value===value);
 const efforts=modelEfforts(selected),effort=form.elements.effort?.value;
 if(!selected||(efforts.length?!efforts.some(option=>option.value===effort):Boolean(effort))){controlNotice(CONTROL_ERRORS.invalid_model,id,'model');return;}
 if(value===model.current&&(!efforts.length||effort===model.effort)){controlNotice('已使用该模型设置',id,'model');return;}
 return submitNative({kind:'model',revision,model:value,...(efforts.length?{effort}:{})});
}
