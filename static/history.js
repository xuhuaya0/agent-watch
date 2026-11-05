// History reads never start a turn and never share the write command queue.
const histories = new Map();
const HISTORY_ERRORS = {history_compression_unavailable:'此设备需要更新历史读取依赖。',history_too_large:'记录过大，暂时无法在面板中展开，请在原应用查看。',history_unavailable:'此设备暂时无法读取历史，请确认采集端与桥接已更新并在线。',native_session_archived:'会话已在原应用中归档。',native_session_unavailable:'原会话已不可用。',history_cursor_expired:'历史记录已变更，请重新加载。',history_timeout:'设备读取超时，请重试。',control_paused:'设备交互连接已暂停。',history_busy:'正在读取其他会话，请稍后重试。',history_record_too_large:'部分原始记录过大，暂时无法读取这一页。'};
function historyState(id) {
 if(!histories.has(id))histories.set(id,{messages:[],loaded:false,busy:false,next:null,version:null,generation:0,error:'',older:false,updated:false,openedTools:new Set()});
 return histories.get(id);
}
function historyControlsHtml(s) {
 let html=`<div class="history-toolbar"><strong>会话记录${s.updated?' · 有新内容':''}</strong><button type="button" onclick="loadHistory(true)"${s.busy?' disabled':''}>刷新</button></div>`;
 if(s.error)html+=`<p class="capture-note warning" role="status">${esc(HISTORY_ERRORS[s.error]||'历史读取失败，请重试。')}</p>`;
 if(s.loaded && s.next)html+=`<button type="button" class="history-older" onclick="loadHistory(false)"${s.busy?' disabled':''}>${s.busy?'正在读取…':'加载更早的记录'}</button>`;
 else if(s.busy && !s.loaded)html+='<p class="capture-note" role="status">正在从设备读取历史…</p>';
 if(s.loaded&&!s.messages.length&&!s.next)html+='<p class="capture-note">没有可显示的消息。</p>';
 return html;
}

function historyMessageBody(m, sessionId) {
 const label=`${esc(m.label||({user:'你',assistant:'Agent',tool:'工具'}[m.role]))}${m.timestamp?` <time>${esc(new Date(m.timestamp).toLocaleString())}</time>`:''}`;
 const body=`<div class="message-body">${markdown(m.text)}</div>${m.truncated?'<p class="capture-note">此条内容过长，显示前 48 KB；完整内容请在原应用查看。</p>':''}`;
 if(m.role==='tool')return `<summary>${label}</summary>${body}`;
 const copy=m.role==='assistant'?`<button type="button" data-history-id="${esc(m.id)}" data-history-session="${esc(sessionId)}" aria-label="复制这条回复" onclick="copyHistoryReply(this)">${icon('copy')}复制</button>`:'';
 return `<div class="message-label">${label}${copy}</div>${body}`;
}

function updateHistoryRows(list, state, sessionId) {
 // Remove records outside the incoming page first. Otherwise advancing the
 // window would detach and reinsert every surviving (possibly focused) row.
 const incoming=new Map(state.messages.map(message=>[message.id,message]));
 const previous=new Map();
 for(const row of Array.from(list.children)) {
  const id=row.dataset.historyId;
  if(incoming.get(id)?.role===row.historyMessage.role)previous.set(id,row);
  else row.remove();
 }
 let cursor=list.firstElementChild;
 for(const message of state.messages) {
  let row=previous.get(message.id);
  if(!row) {
   row=document.createElement(message.role==='tool'?'details':'article');
   row.dataset.historyId=message.id;
   row.dataset.historySession=sessionId;
   if(message.role==='tool') {
    row.className='history-tool';
    row.ontoggle=()=>rememberHistoryTool(row);
    row.open=state.openedTools.has(message.id);
   }else row.className=message.role==='user'?'message user-message':'message';
  }
  const old=row.historyMessage;
  if(!old || old.text!==message.text || old.label!==message.label || old.timestamp!==message.timestamp || old.truncated!==message.truncated)
   row.innerHTML=historyMessageBody(message,sessionId);
  row.historyMessage=message;
  if(row!==cursor)list.insertBefore(row,cursor);
  cursor=row.nextElementSibling;
 }
 list.historyMessages=state.messages;
}

// Measure immediately before a DOM update, so a response does not undo any
// scrolling or focus change that happened while the network request was out.
function historyReadingAnchor(pane, list, prepend=false) {
 // At the top a live refresh should keep the header visible. Loading an older
 // page explicitly should keep the first visible message in place instead.
 if(!pane.clientHeight || (pane.scrollTop<=0 && !prepend))return null;
 const view=pane.getBoundingClientRect();
 const control=document.getElementById('session-control');
 const focused=document.activeElement;
 if(control?.contains(focused)) {
  const rect=focused.getBoundingClientRect();
  if(rect.bottom>view.top && rect.top<view.bottom)return {node:focused,top:rect.top};
 }
 for(const row of list.children) {
  const rect=row.getBoundingClientRect();
  if(rect.bottom>view.top && rect.top<view.bottom)return {node:row,top:rect.top};
 }
 if(control && list.getBoundingClientRect().bottom<=view.top)return {node:control,top:control.getBoundingClientRect().top};
 return null;
}

function renderHistory(prepend=false) {
 const id=workspace.selected,el=document.getElementById('session-history');
 if(!el||!id||workspace.page!=='sessions')return;
 const state=historyState(id),pane=document.getElementById('session-detail');
 if(el.dataset.session!==id) {
  el.replaceChildren(document.createElement('div'),document.createElement('div'));
  el.dataset.session=id;
 }
 const [controls,list]=el.children;
 const controlsHtml=historyControlsHtml(state);
 const latest=document.getElementById('latest-messages');
 if(controls.historyHtml===controlsHtml && list.historyMessages===state.messages && (!latest || latest.hidden===state.loaded))return;
 const anchor=historyReadingAnchor(pane,list,prepend);
 if(controls.historyHtml!==controlsHtml){controls.innerHTML=controlsHtml;controls.historyHtml=controlsHtml;}
 if(list.historyMessages!==state.messages)updateHistoryRows(list,state,id);
 if(latest)latest.hidden=state.loaded;
 if(anchor?.node.isConnected)pane.scrollTop+=anchor.node.getBoundingClientRect().top-anchor.top;
}
function rememberHistoryTool(element) {
 const s=histories.get(element.dataset.historySession),id=element.dataset.historyId;
 if(!s?.messages.some(m=>m.role==='tool'&&m.id===id))return;
 if(element.open)s.openedTools.add(id);else s.openedTools.delete(id);
}
function copyHistoryReply(button) {
 const id=button.dataset.historySession;if(id!==workspace.selected||workspace.page!=='sessions')return;
 const message=histories.get(id)?.messages.find(m=>m.id===button.dataset.historyId&&m.role==='assistant');
 if(message)return copyText(message.text);
}
function ensureHistory() {
 const task=workspace.tasks.find(t=>taskId(t)===workspace.selected);
 if(!['codex','antigravity','dsh'].includes(task?.tool))return;
 const s=historyState(workspace.selected),version=String(activityAt(task));
 renderHistory();
 if(!s.busy&&s.version!==version){s.version=version;if(s.loaded&&(s.older||s.updated)){s.updated=true;renderHistory();}else loadHistory(true,true);}
}
async function loadHistory(reset=true,background=false) {
 const id=workspace.selected,task=workspace.tasks.find(t=>taskId(t)===id);
 if(!['codex','antigravity','dsh'].includes(task?.tool))return;
 const s=historyState(id);if(s.busy)return;
 const cursor=reset?null:s.next;if(!reset&&!cursor)return;
 s.busy=true;s.error='';const generation=++s.generation;renderHistory();
 const started=Date.now();
 let prepended=false;
 try {
  let data;
  for(;;) {
   const response=await apiFetch('/api/history/tasks/'+encodeURIComponent(id)+(cursor?'?cursor='+encodeURIComponent(cursor):''));
   if(response.status===401){showAuthView(false);return;}
   data=await response.json();if(!response.ok||data.error)throw new Error(data.error||'history_unavailable');
   if(data.ready)break;
   if(Date.now()-started>30000)throw new Error('history_timeout');
   if(workspace.selected!==id||workspace.page!=='sessions')return;
   await new Promise(resolve=>setTimeout(resolve,750));
  }
  if(s.generation!==generation)return;
  if(!Array.isArray(data.messages))throw new Error('history_unavailable');
  if(background && s.loaded && workspace.selected===id && workspace.page==='sessions') {
   const list=document.getElementById('session-history')?.lastElementChild;
   const anchor=list&&historyReadingAnchor(document.getElementById('session-detail'),list);
   const visibleId=anchor && anchor.node.parentElement===list ? anchor.node.dataset.historyId : null;
   // A live page may have moved past the record on screen. Let the reader
   // choose when to replace this page with the latest window.
   if(visibleId && !data.messages.some(message=>message.id===visibleId)){s.updated=true;return;}
  }
  s.messages=reset?data.messages:[...data.messages,...s.messages].filter((m,i,all)=>all.findIndex(x=>x.id===m.id)===i);
  s.next=data.next_cursor;s.loaded=true;s.older=!reset;s.updated=false;
  prepended=!reset;
 } catch(e) {s.error=e.message;}
 finally {s.busy=false;if(workspace.selected!==id&&!s.loaded)s.version=null;if(workspace.selected===id)renderHistory(prepended);}
}
