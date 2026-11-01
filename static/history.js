// History reads never start a turn and never share the write command queue.
const histories = new Map();
const HISTORY_ERRORS = {history_compression_unavailable:'此设备需要更新历史读取依赖。',history_too_large:'记录过大，暂时无法在面板中展开，请在原应用查看。',history_unavailable:'此设备暂时无法读取历史，请确认采集端与桥接已更新并在线。',native_session_archived:'会话已在原应用中归档。',native_session_unavailable:'原会话已不可用。',history_cursor_expired:'历史记录已变更，请重新加载。',history_timeout:'设备读取超时，请重试。',control_paused:'设备交互连接已暂停。',history_busy:'正在读取其他会话，请稍后重试。',history_record_too_large:'部分原始记录过大，暂时无法读取这一页。'};
function historyState(id) {
 if(!histories.has(id))histories.set(id,{messages:[],loaded:false,busy:false,next:null,version:null,generation:0,error:'',older:false,updated:false});
 return histories.get(id);
}
function historyHtml(s) {
 let html=`<div class="history-toolbar"><strong>会话记录${s.updated?' · 有新内容':''}</strong><button type="button" onclick="loadHistory(true)"${s.busy?' disabled':''}>刷新</button></div>`;
 if(s.error)html+=`<p class="capture-note warning" role="status">${esc(HISTORY_ERRORS[s.error]||'历史读取失败，请重试。')}</p>`;
 if(s.loaded && s.next)html+=`<button type="button" class="history-older" onclick="loadHistory(false)"${s.busy?' disabled':''}>${s.busy?'正在读取…':'加载更早的记录'}</button>`;
 else if(s.busy)html+='<p class="capture-note" role="status">正在从设备读取历史…</p>';
 for(const m of s.messages) {
  const label=`${esc(m.label||({user:'你',assistant:'Agent',tool:'工具'}[m.role]))}${m.timestamp?` <time>${esc(new Date(m.timestamp).toLocaleString())}</time>`:''}`;
  const body=`<div class="message-body">${markdown(m.text)}</div>${m.truncated?'<p class="capture-note">此条内容过长，显示前 48 KB；完整内容请在原应用查看。</p>':''}`;
  html+=m.role==='tool'?`<details class="history-tool"><summary>${label}</summary>${body}</details>`:`<article class="message ${m.role==='user'?'user-message':''}" data-history-id="${esc(m.id)}"><div class="message-label">${label}</div>${body}</article>`;
 }
 if(s.loaded&&!s.messages.length&&!s.next)html+='<p class="capture-note">没有可显示的消息。</p>';
 return html;
}
function renderHistory() {
 const id=workspace.selected,el=document.getElementById('session-history');if(!el||!id)return;
 const s=historyState(id),html=historyHtml(s);if(el.dataset.historyHtml!==html){el.innerHTML=html;el.dataset.historyHtml=html;}
 const latest=document.getElementById('latest-messages');if(latest)latest.hidden=s.loaded;
}
function ensureHistory() {
 const task=workspace.tasks.find(t=>taskId(t)===workspace.selected);
 if(!['codex','antigravity','dsh'].includes(task?.tool))return;
 const s=historyState(workspace.selected),version=String(activityAt(task));
 renderHistory();
 if(!s.busy&&s.version!==version){s.version=version;if(s.loaded&&s.older){s.updated=true;renderHistory();}else loadHistory(true);}
}
async function loadHistory(reset=true) {
 const id=workspace.selected,task=workspace.tasks.find(t=>taskId(t)===id);
 if(!['codex','antigravity','dsh'].includes(task?.tool))return;
 const s=historyState(id);if(s.busy)return;
 const cursor=reset?null:s.next;if(!reset&&!cursor)return;
 s.busy=true;s.error='';const generation=++s.generation;renderHistory();
 const started=Date.now();
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
  const pane=document.getElementById('session-detail'),height=pane?.scrollHeight||0,top=pane?.scrollTop||0;
  s.messages=reset?data.messages:[...data.messages,...s.messages].filter((m,i,all)=>all.findIndex(x=>x.id===m.id)===i);
  s.next=data.next_cursor;s.loaded=true;s.busy=false;s.older=!reset;s.updated=false;
  if(workspace.selected===id){renderHistory();if(!reset&&pane)pane.scrollTop=top+pane.scrollHeight-height;}
 } catch(e) {s.error=e.message;}
 finally {s.busy=false;if(workspace.selected!==id&&!s.loaded)s.version=null;if(workspace.selected===id)renderHistory();}
}
