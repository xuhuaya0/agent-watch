// History reads never start a turn and never share the write command queue.
const histories = new Map();
const HISTORY_ERRORS = {history_compression_unavailable:'此设备需要更新历史读取依赖。',history_too_large:'记录过大，暂时无法在面板中展开，请在原应用查看。',history_unavailable:'此设备暂时无法读取历史，请确认采集端与桥接已更新并在线。',native_session_archived:'会话已在原应用中归档。',native_session_unavailable:'原会话已不可用。',history_cursor_expired:'历史记录已变更，请重新加载。',history_timeout:'设备读取超时，请重试。',control_paused:'设备交互连接已暂停。',history_busy:'正在读取其他会话，请稍后重试。',history_record_too_large:'部分原始记录过大，暂时无法读取这一页。'};
function historyState(id) {
 if(!histories.has(id))histories.set(id,{messages:[],loaded:false,busy:false,next:null,version:null,generation:0,error:'',older:false,updated:false,openedTools:new Set(),openedToolGroups:new Set()});
 return histories.get(id);
}
function historyControlsHtml(state) {
 const controls = [];
 if (state.error) controls.push(`<p class="history-notice" role="status">${esc(HISTORY_ERRORS[state.error] || '历史读取失败')} <button type="button" onclick="loadHistory(true)">重试</button></p>`);
 if (state.updated) controls.push('<button type="button" class="history-older" onclick="showNewHistory()">有新内容 ↓</button>');
 if (state.loaded && state.next) controls.push(`<button type="button" class="history-older" onclick="loadHistory(false)"${state.busy?' disabled':''}>${state.busy?'读取中…':'加载更早的记录'}</button>`);
 if (state.busy && !state.loaded) controls.push('<p class="capture-note" role="status">正在读取历史…</p>');
 return controls.join('');
}
function showNewHistory() {
 if (historyState(workspace.selected).busy) return;
 readerPane.openAtLatest = true;
 return loadHistory(true);
}
function historyMessageBody(message, sessionId) {
 const context={sessionId,attachments:message.attachments||[]};
 const body = `<div class="message-body">${markdown(message.text,context)}</div>${message.truncated?'<p class="capture-note">长消息仅显示前 48 KB。</p>':''}`;
 if (message.role === 'tool') return `<summary>${esc(message.label || '工具')}</summary>${body}`;
 if (message.role === 'assistant' && message.phase === 'commentary') return body;
 const label = message.label === '外部通知' ? '<small>原生系统通知</small>' : '';
 if (message.role === 'user') return label + body;
 return `${body}<div class="message-actions"><button type="button" data-history-id="${esc(message.id)}" data-history-session="${esc(sessionId)}" title="复制回复" aria-label="复制这条回复" onclick="copyHistoryReply(this)">${icon('copy')}</button></div>`;
}

function historyIsProcess(message) {
 return message.role==='tool' || (message.role==='assistant' && message.phase==='commentary');
}
function placeHistoryRows(parent, rows) {
 const wanted=new Set(rows);
 for(const row of Array.from(parent.children))if(!wanted.has(row))row.remove();
 let cursor=parent.firstElementChild;
 for(const row of rows) {
  if(row!==cursor)parent.insertBefore(row,cursor);
  cursor=row.nextElementSibling;
 }
}
function updateHistoryRows(list, state, sessionId) {
 // Remove records outside the incoming page first. Otherwise advancing the
 // window would detach and reinsert every surviving (possibly focused) row.
 const incoming=new Map(state.messages.map(message=>[message.id,message]));
 const previous=new Map(),groups=Array.from(list.children).filter(row=>row.historyProcessIds);
 // Native details updates .open before its queued toggle event. A poll that
 // arrives in between must honor the reader's latest click, not stale memory.
 for(const group of groups)rememberHistoryToolGroup(group);
 const oldRows=Array.from(list.children).flatMap(row=>row.historyProcessIds?Array.from(row.lastElementChild.children):[row]);
 for(const row of oldRows) {
  const id=row.dataset.historyId;
  if(incoming.get(id)?.role===row.historyMessage.role)previous.set(id,row);
  else row.remove();
 }
 const entries=[];
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
   }
  }
  if(message.role!=='tool')row.className=message.role==='user'?'message user-message':message.phase==='commentary'?'message history-commentary':'message';
  const old=row.historyMessage;
  if(!old || old.text!==message.text || old.label!==message.label || old.timestamp!==message.timestamp || old.truncated!==message.truncated || old.phase!==message.phase || JSON.stringify(old.attachments)!==JSON.stringify(message.attachments))
   row.innerHTML=historyMessageBody(message,sessionId);
  const author = message.label || {assistant:'Agent',user:'你',tool:'工具'}[message.role];
  row.title = message.timestamp ? author+' · '+new Date(message.timestamp).toLocaleString() : author;
  row.setAttribute('aria-label', author);
  row.historyMessage=message;
  if(historyIsProcess(message)) {
   if(!Array.isArray(entries.at(-1)))entries.push([]);
   entries.at(-1).push(row);
  }else entries.push(row);
 }
 const usedGroups=new Set(),rows=[];
 for(const entry of entries) {
  if(!Array.isArray(entry)){rows.push(entry);continue;}
  const ids=entry.map(row=>row.dataset.historyId),members=new Set(ids);
  // Match by surviving process records, not the first record in a page: an older page
  // may extend a run at its beginning, and a live window may trim that record.
  let group=null,best=0;
  for(const candidate of groups) {
   if(usedGroups.has(candidate))continue;
   const overlap=candidate.historyProcessIds.filter(id=>members.has(id)).length;
   if(overlap>best){group=candidate;best=overlap;}
  }
  if(!group) {
   group=document.createElement('details');group.className='history-tools';
   group.dataset.historySession=sessionId;
   const summary=document.createElement('summary'),body=document.createElement('div');
   body.className='history-tools-list';group.replaceChildren(summary,body);
   group.ontoggle=()=>rememberHistoryToolGroup(group);
  }
  usedGroups.add(group);group.historyProcessIds=ids;
  const count=entry.filter(row=>row.historyMessage.role==='tool').length;
  const summary=count?'工具调用 · '+count:'过程 · '+entry.length;
  if(group.firstElementChild.innerHTML!==summary)group.firstElementChild.innerHTML=summary;
  group.open=ids.some(id=>state.openedToolGroups.has(id));
  if(group.open)for(const id of ids)state.openedToolGroups.add(id);
  placeHistoryRows(group.lastElementChild,entry);rows.push(group);
 }
 placeHistoryRows(list,rows);
 list.historyMessages=state.messages;
}

// Measure immediately before a DOM update, so a response does not undo any
// scrolling or focus change that happened while the network request was out.
function historyReadingAnchor(pane, list, prepend=false) {
 // At the top a live refresh should keep the header visible. Loading an older
 // page explicitly should keep the first visible message in place instead.
 if(!pane.clientHeight || (pane.scrollTop<=0 && !prepend))return null;
 const view=pane.getBoundingClientRect();
 const visible=node=>{const rect=node.getBoundingClientRect();return rect.bottom>view.top && rect.top<view.bottom?{node,top:rect.top}:null;};
 for(const row of list.children) {
  if(row.historyProcessIds) {
   const header=visible(row.firstElementChild);
   if(header)return {...header,ids:row.historyProcessIds};
   if(row.open)for(const record of row.lastElementChild.children) {
    const anchor=visible(record);if(anchor)return {...anchor,ids:[record.dataset.historyId]};
   }
  }else {
   const anchor=visible(row);if(anchor)return {...anchor,ids:[row.dataset.historyId]};
  }
 }
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
 if(controls.historyHtml===controlsHtml && list.historyMessages===state.messages && (!latest || latest.hidden===(state.loaded && state.messages.length>0)))return;
 const anchor=historyReadingAnchor(pane,list,prepend);
 if(controls.historyHtml!==controlsHtml){controls.innerHTML=controlsHtml;controls.historyHtml=controlsHtml;}
 if(list.historyMessages!==state.messages)updateHistoryRows(list,state,id);
 if(latest)latest.hidden=state.loaded && state.messages.length>0;
 // A cached page can mount synchronously after renderInteraction. Reconcile
 // with the final visible sources now, without waiting for a control poll.
 if(typeof renderOutgoing==='function')renderOutgoing();
 if(anchor?.node.isConnected)pane.scrollTop+=anchor.node.getBoundingClientRect().top-anchor.top;
 if(typeof hydrateMessageAttachments==='function')hydrateMessageAttachments(el);
}
function rememberHistoryTool(element) {
 const s=histories.get(element.dataset.historySession),id=element.dataset.historyId;
 if(!s?.messages.some(m=>m.role==='tool'&&m.id===id))return;
 if(element.open)s.openedTools.add(id);else s.openedTools.delete(id);
}
function rememberHistoryToolGroup(element) {
 const s=histories.get(element.dataset.historySession);
 if(!s || !element.isConnected)return;
 const ids=new Set(s.messages.filter(historyIsProcess).map(message=>message.id));
 for(const id of element.historyProcessIds || [])if(ids.has(id)) {
  if(element.open)s.openedToolGroups.add(id);else s.openedToolGroups.delete(id);
 }
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
   const anchor=list&&historyReadingAnchor(document.getElementById('session-detail'),list,true);
   const visibleIds=anchor?.ids;
   // A live page may have moved past the record on screen. Let the reader
   // choose when to replace this page with the latest window.
   if(visibleIds?.length && !data.messages.some(message=>visibleIds.includes(message.id))){s.updated=true;return;}
  }
  s.messages=reset?data.messages:[...data.messages,...s.messages].filter((m,i,all)=>all.findIndex(x=>x.id===m.id)===i);
  s.next=data.next_cursor;s.loaded=true;s.older=!reset;s.updated=false;
  prepended=!reset;
 } catch(e) {s.error=e.message;}
 finally {s.busy=false;if(workspace.selected!==id&&!s.loaded)s.version=null;if(workspace.selected===id){renderHistory(prepended);
  if(typeof renderInteraction==='function')renderInteraction();
  if(workspace.page==='sessions' && (s.messages.length || ((s.loaded || s.error) && workspace.detail)) && typeof settleChatOpening==='function')settleChatOpening();
 }}
}
