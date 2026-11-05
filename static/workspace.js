// Icons reference a local SVG sprite; no external assets or fonts are loaded.
function icon(name) { return `<svg class="icon" aria-hidden="true"><use href="#icon-${name}"></use></svg>`; }
// View state stays local; read receipts are stored on the Hub.
// Pin state is read from each Harness and cannot be changed independently.
const workspace = {
  page:'sessions', tasks:[], machines:{}, errors:{}, selected:null, detail:null,
  detailSignature:'', renderedSignature:'', detailRequest:0, loading:false,
  query:'', project:'', device:'', tool:'', state:'', listCollapsed:false,
  mobileDetail:false, selectedDevice:null, scroll:new Map(), prefsBusy:new Set(),
};
function deviceKey(t) { return t.machine_id ? `id:${t.machine_id}` : `legacy:${t.machine || ''}`; }
function taskId(t) { return t.ui_key || t.key; }
function normalizedPath(path) {
  let value=String(path || '').replace(/\\/g,'/').replace(/\/+$/,'');
  return /^[a-z]:/i.test(value) || value.startsWith('//') ? value.toLowerCase() : value;
}
function projectKey(t) {
  const cwd=normalizedPath(t.extra?.cwd);
  return JSON.stringify([deviceKey(t),cwd || t.tool+':'+(t.project || '未分类')]);
}
function projectGroups(tasks=workspace.tasks) {
  const result=new Map();
  for (const t of newestFirst(tasks)) {
    const key=projectKey(t);
    if (!result.has(key)) result.set(key,{key,name:t.project || '未分类',machine:t.machine,cwd:t.extra?.cwd || '',tasks:[]});
    result.get(key).tasks.push(t);
  }
  return [...result.values()];
}
function isUnread(t) { return activityAt(t) > Number(t.ui?.read_at || 0); }
function visibleTasks() {
  const q=workspace.query.trim().toLocaleLowerCase();
  return newestFirst(workspace.tasks.filter(t =>
    t.extra?.native_archived !== true &&
    (!workspace.project || projectKey(t)===workspace.project) &&
    (!workspace.device || deviceKey(t)===workspace.device) &&
    (!workspace.tool || t.tool===workspace.tool) &&
    (!workspace.state || (workspace.state==='unread' ? isUnread(t) : taskGroup(t)===workspace.state)) &&
    (!q || [t.title,t.project,t.machine,t.tool,t.prompt].join(' ').toLocaleLowerCase().includes(q))));
}
function isPinned(t) { return t.extra?.native_pin?.known === true && t.extra.native_pin.pinned === true; }
function splitList(tasks) {
  const parts={attention:[],running:[],done:[],pinned:[],recent:[]};
  for(const t of newestFirst(tasks)) {
    const group=taskGroup(t);
    parts[['attention','running','done'].includes(group)?group:isPinned(t)?'pinned':'recent'].push(t);
  }
  const pinned=parts.pinned;
  pinned.sort((a,b)=>{
    const groupA=deviceKey(a)+'/'+a.tool,groupB=deviceKey(b)+'/'+b.tool;
    if(groupA!==groupB)return groupA.localeCompare(groupB);
    const pa=a.extra.native_pin,pb=b.extra.native_pin;
    return Number.isFinite(pa.order)&&Number.isFinite(pb.order)?pa.order-pb.order:activityAt(b)-activityAt(a);
  });
  return parts;
}
function machineAbbr(t) {
  const shortName=name=>{
    const clean=(name || 'PC').replace(/\.local$/i,'').replace(/[^\p{L}\p{N}]/gu,'');
    return /mac/i.test(clean)?'Mac':clean.length<=6?clean:clean.slice(0,3);
  };
  let short=shortName(t.machine);
  if(workspace.tasks.some(x=>deviceKey(x)!==deviceKey(t) && shortName(x.machine)===short))
    short += (t.machine_id || t.machine || '').slice(-2);
  return short || 'PC';
}
function relativeTime(t) {
  const age=Math.max(0,Math.floor(Date.now()/1000-activityAt(t)));
  if (!activityAt(t)) return '时间未知';
  return age<60 ? '刚刚' : age<3600 ? Math.floor(age/60)+'m' : age<86400 ? Math.floor(age/3600)+'h' : Math.floor(age/86400)+'d';
}
function jsArg(value) { return esc(JSON.stringify(value)); }
function machineTone(t) {
  // Identity, rather than display order or IP, keeps a device's color stable.
  let hash=2166136261;
  for(const char of deviceKey(t)) hash=Math.imul(hash ^ char.codePointAt(0),16777619);
  return (hash >>> 0) % 6;
}
function rowHtml(t) {
  const harness={codex:['CX','Codex'],antigravity:['AG','Antigravity'],dsh:['DS','DeepSeek Harness']}[t.tool] || ['AI',t.tool];
  const label=[t.title,t.machine,harness[1],ZH[t.state]||t.state].join(' · ');
  return `<button class="session-row ${taskId(t)===workspace.selected?'selected':''}" data-session="${esc(taskId(t))}" aria-label="${esc(label)}" aria-pressed="${taskId(t)===workspace.selected}" onclick="selectSession(${jsArg(taskId(t))})">
    <span class="machine-badge machine-tone-${machineTone(t)}" title="主机：${esc(t.machine)} · ${esc(t.machine_id || '')}">${icon('device')}<span>${esc(machineAbbr(t))}</span></span>
    <span class="harness-badge harness-${esc(t.tool)}" title="Harness：${esc(harness[1])}">${harness[0]}</span>
    <span class="row-title" title="${esc(label)}"><span class="state-dot ${esc(t.state)}"></span>${esc(t.title)}</span>
    <span class="row-unread">${isUnread(t)?'<span class="unread-dot" title="有未读活动"></span>':''}</span>
    <span class="row-time">${relativeTime(t)}</span>
  </button>`;
}
function toast(message) {
  const el=document.getElementById('workspace-toast');el.textContent=message;el.style.display='block';
  clearTimeout(toast.timer);toast.timer=setTimeout(()=>el.style.display='none',4000);
}
// A returning reader may first see a short list summary. Direct input takes
// precedence over a saved position waiting for the full response.
const readerPane = document.getElementById('session-detail');
for (const type of ['wheel', 'touchstart', 'pointerdown', 'keydown', 'focusin']) {
  readerPane.addEventListener(type, () => { readerPane.pendingOffset = null; }, {passive:true});
}
function saveScroll() {
  const pane = document.getElementById('session-detail');
  if (workspace.page === 'sessions' && pane.clientHeight && pane.dataset.session === workspace.selected) {
    workspace.scroll.set(workspace.selected, pane.pendingOffset ?? pane.scrollTop);
  }
}
function navigate(page) {
  if(page!=='sessions'&&typeof closeSessionOptions==='function')closeSessionOptions();
  saveScroll();workspace.page=page;
  for (const name of ['sessions','projects','devices','settings']) document.getElementById(name+'-view').hidden=name!==page;
  document.querySelectorAll('.app-nav button').forEach(b=>{
    b.classList.toggle('active',b.dataset.page===page);
    b.setAttribute('aria-current',b.dataset.page===page?'page':'false');
  });
  if(page==='sessions') {renderList();renderDetail();}
  if(page==='projects') renderProjects();
  if(page==='devices') renderDevices();
}
function setFilter(name,value) {
  workspace[name]=value;
  renderList();
}
function clearFilters() {
  Object.assign(workspace,{query:'',project:'',device:'',tool:'',state:''});
  document.getElementById('session-search').value='';
  syncFilters();renderList();
}
function syncOptions(id,html,value) {
  const el=document.getElementById(id);
  if(el.innerHTML!==html) el.innerHTML=html;
  el.value=value;
}
function syncFilters() {
  const projects=projectGroups().sort((a,b)=>a.name.localeCompare(b.name));
  syncOptions('filter-project','<option value="">全部项目</option>'+projects.map(p=>`<option value="${esc(p.key)}">${esc(p.name)} · ${esc(p.machine)}</option>`).join(''),workspace.project);
  const devices=new Map(workspace.tasks.map(t=>[deviceKey(t),t.machine]));
  syncOptions('filter-device','<option value="">全部设备</option>'+[...devices].map(([key,name])=>`<option value="${esc(key)}">${esc(name)}</option>`).join(''),workspace.device);
  for(const name of ['tool','state']) document.getElementById('filter-'+name).value=workspace[name];
  const count=[workspace.project,workspace.device,workspace.tool,workspace.state].filter(Boolean).length;
  const toggle=document.getElementById('filter-toggle');
  toggle.textContent=count?'筛选 · '+count:'筛选';
  toggle.classList.toggle('has-filters',count>0);
}
function renderList() {
  syncFilters();
  const visible=visibleTasks();const parts=splitList(visible);
  document.getElementById('list-count').textContent=visible.length;
  const priority=[['attention','需介入','activity'],['running','运行中','activity'],['done','刚完成 · 2h 内','clock']]
    .filter(([key])=>parts[key].length)
    .map(([key,label,glyph])=>`<div class="list-label"><span>${icon(glyph)} ${label}</span><span>${parts[key].length}</span></div>`+parts[key].map(rowHtml).join('')).join('');
  let html=priority+`<div class="list-label"><span>${icon('pin')} Pinned</span><span title="原生置顶；活跃会话显示在上方状态分组">${parts.pinned.length}</span></div>`+
    (parts.pinned.length?parts.pinned.map(rowHtml).join(''):`<div class="pinned-empty">${visible.some(isPinned)?'置顶会话已在上方显示':'暂无置顶'}</div>`)+
    `<div class="list-label"><span>${icon('clock')} Recent</span><span>${parts.recent.length}</span></div>`+
    (parts.recent.length?parts.recent.map(rowHtml).join(''):visible.length?'':'<div class="list-empty">没有符合筛选条件的会话</div>');
  if(!visible.length){
    const filtered=Boolean(workspace.query||workspace.project||workspace.device||workspace.tool||workspace.state);
    html=filtered?'<div class="list-empty">没有符合筛选条件的会话<br><button onclick="clearFilters()">清除筛选</button></div>':'<div class="list-empty">尚未发现会话<br>请在设备页检查采集状态。</div>';
  }
  const el=document.getElementById('session-items');const top=el.scrollTop;
  const focused=el.contains(document.activeElement)?document.activeElement?.dataset.session:null;
  el.innerHTML=html;el.scrollTop=top;
  if(focused) [...el.querySelectorAll('[data-session]')].find(n=>n.dataset.session===focused)?.focus({preventScroll:true});
}
function applyListState() {
  const el=document.getElementById('sessions-view');
  el.classList.toggle('list-collapsed',workspace.listCollapsed);
  el.classList.toggle('mobile-detail',workspace.mobileDetail);
}
function toggleList() {workspace.listCollapsed=!workspace.listCollapsed;applyListState();}
function showList() {saveScroll();workspace.listCollapsed=false;workspace.mobileDetail=false;applyListState();}
function selectSession(id) {
  if(typeof closeSessionOptions==='function')closeSessionOptions();
  if (workspace.selected !== id) {
    saveScroll();
    workspace.selected = id;
    workspace.detail = null;
    workspace.detailSignature = '';
    workspace.renderedSignature = '';
  }
  workspace.mobileDetail=true;applyListState();navigate('sessions');
  refreshDetail();if(typeof refreshInteraction==='function')refreshInteraction();
}
async function updatePreference(id,patch) {
  const task=workspace.tasks.find(t=>taskId(t)===id);if(!task)return;
  const response=await apiFetch('/api/workspace/tasks/'+encodeURIComponent(id),{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(patch)});
  if(response.status===401){showAuthView(false);return;}
  if(!response.ok) throw new Error('保存失败，请重试');
  const result=await response.json();
  task.ui=result.ui;
  // Polls may have replaced the array while the request was in flight.
  const current=workspace.tasks.find(t=>taskId(t)===id);if(current)current.ui=result.ui;
  renderList();renderDetail();return result.ui;
}
async function markRead(task) {
  const id=taskId(task);if(!isUnread(task)||workspace.prefsBusy.has(id))return;
  workspace.prefsBusy.add(id);
  try {await updatePreference(id,{read_at:activityAt(task)});}
  catch(error){toast(error.message);}
  finally{workspace.prefsBusy.delete(id);}
}
function detailVersion(task) {
  return JSON.stringify([task.key,activityAt(task),task.state,task.title,task.prompt,task.detail]);
}
async function refreshDetail() {
  if(workspace.page!=='sessions'||!workspace.selected)return;
  const task=workspace.tasks.find(t=>taskId(t)===workspace.selected);if(!task){renderDetail();return;}
  const version=detailVersion(task);
  if(version===workspace.detailSignature){renderDetail();return;}
  const id=workspace.selected,requestId=++workspace.detailRequest;
  try {
    const response=await fetch('/api/tasks/'+encodeURIComponent(task.key));
    if(response.status===401){showAuthView(false);return;}
    if(!response.ok)throw new Error('会话详情暂时不可用');
    const result=await response.json();
    if(id!==workspace.selected||requestId!==workspace.detailRequest)return;
    workspace.detail=result.task;workspace.detailSignature=version;renderDetail();
    await markRead(task);
  } catch(error){if(id===workspace.selected)toast(error.message);}
}
function inlineMarkdown(text) {
  return esc(text).replace(/`([^`]+)`/g,'<code>$1</code>').replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>');
}
function markdown(text) {
  // Escape all raw HTML. No remote images, embedded HTML or executable links.
  const blocks=String(text || '').split(/```[^\n]*\n([\s\S]*?)(?:```|$)/g);
  return blocks.map((block,index)=>index%2 ? `<div class="code-wrap"><button onclick="copyCode(this)">复制代码</button><pre><code>${esc(block)}</code></pre></div>` :
    block.split(/\n\s*\n/).filter(x=>x.trim()).map(p=>{
      const h=p.match(/^(#{1,3}) (.+)$/);
      if(h)return `<h${h[1].length+1}>${inlineMarkdown(h[2])}</h${h[1].length+1}>`;
      if(p.split('\n').every(line=>/^\s*[-*] /.test(line)))return '<ul>'+p.split('\n').map(line=>'<li>'+inlineMarkdown(line.replace(/^\s*[-*] /,''))+'</li>').join('')+'</ul>';
      return '<p>'+inlineMarkdown(p).replace(/\n/g,'<br>')+'</p>';
    }).join('')).join('');
}
async function copyText(text) {
  try {
    if(navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(text);
    else {
      const area=document.createElement('textarea');area.value=text;area.style.position='fixed';area.style.left='-10000px';document.body.append(area);area.select();
      try {if(!document.execCommand('copy'))throw new Error('copy unavailable');}finally{area.remove();}
    }
    toast('已复制');
  }
  catch(_){toast('浏览器未允许复制，请选中文字复制');}
}
function copyCode(button) {copyText(button.parentElement.querySelector('code').textContent);}
function copyReply() {const t=workspace.detail;if(t)copyText(t.extra?.latest_turn?.assistant || t.detail || '');}
// Keep history and reply controls mounted while a poll changes surrounding facts.
function setDetailHtml(id, html) {
  const region = document.getElementById(id);
  if (region.renderedHtml === html) return;
  region.innerHTML = html;
  region.renderedHtml = html;
}
function detailAnchor(pane) {
  if (!pane.scrollTop || !pane.clientHeight) return null;
  const viewport = pane.getBoundingClientRect();
  for (const region of pane.children) {
    const rect = region.getBoundingClientRect();
    if (rect.height && rect.bottom > viewport.top && rect.top < viewport.bottom) {
      return {region, top:rect.top};
    }
  }
  return null;
}
function renderDetail() {
  if(workspace.page!=='sessions')return;
  const el=document.getElementById('session-detail');const brief=workspace.tasks.find(t=>taskId(t)===workspace.selected);
  document.getElementById('detail-location').textContent=brief?[brief.project,brief.machine].filter(Boolean).join(' / '):'';
  if(!brief){
    saveScroll();
    delete el.dataset.session;
    el.pendingOffset = null;
    el.innerHTML='<div class="blank-state">'+icon('conversations')+'<h2>'+ (workspace.selected?'会话当前未上报':'选择一个会话')+'</h2><p>'+(workspace.selected?'选择仍保留，等待该设备下次上报，或从列表打开其他会话。':'从列表查看最近消息与结果。')+'</p></div>';
    workspace.renderedSignature='';if(typeof renderSessionActions==='function')renderSessionActions();return;
  }
  const t=workspace.detail?{...workspace.detail,...brief,extra:{...workspace.detail.extra,...brief.extra}}:brief;
  const signature=JSON.stringify([workspace.selected,workspace.detailSignature,brief.extra?.native_pin,brief.acked,brief.machine_online]);
  const duration=fmt(Math.max(0,Math.floor(Date.now()/1000-(t.turn_started_at || t.state_since || Date.now()/1000))));
  if(signature===workspace.renderedSignature){if(typeof ensureHistory==='function')ensureHistory();const age=document.getElementById('detail-duration');if(age)age.textContent=duration;return;}
  const opening = el.dataset.session !== workspace.selected;
  const anchor = opening ? null : detailAnchor(el);
  const hasHistory = ['codex','antigravity','dsh'].includes(t.tool);
  if (opening) {
    el.dataset.session = workspace.selected;
    el.pendingOffset = null;
    el.innerHTML = `<div id="detail-intro"></div>
      ${hasHistory ? '<section id="session-history" aria-label="会话记录"></section>' : ''}
      <section id="latest-messages"></section>
      <p id="detail-pin" class="native-pin-note"></p><div id="detail-subagents"></div>
      <section id="session-control" class="session-control" aria-label="原会话交互"></section>
      <p id="detail-capture" class="capture-note"></p>`;
  }
  const pin=t.extra?.native_pin;
  const turn=t.extra?.latest_turn;const user=turn?.user ?? t.prompt;const reply=turn?.assistant ?? t.detail;
  const live=['running','stalled'].includes(t.state);
  setDetailHtml('detail-intro', `<div class="conversation-head"><h1>${esc(t.title)}</h1>
    <div class="conversation-facts"><span class="status-tag ${esc(t.state)}">${esc(ZH[t.state] || t.state)}</span><span class="fact">${esc(t.machine)}</span><span class="fact">${esc(t.tool_short || t.tool)}</span><span>${t.machine_online===false?'设备离线':'设备在线'} · ${esc((t.machine_ips || []).join(' / '))}</span></div>
    <div class="conversation-facts" style="margin-top:9px"><span>${activityAt(t)?'最近活动 '+esc(new Date(activityAt(t)*1000).toLocaleString()):'活动时间未知'}</span>${t.extra?.cwd?`<span title="${esc(t.extra.cwd)}">· ${esc(t.extra.cwd)}</span>`:''}</div>
    <div class="conversation-actions">${t.state!=='running'&&t.state!=='idle'&&!t.acked?`<button onclick="ack(${jsArg(t.key)})">知道了</button>`:''}</div></div>
    ${live?`<div class="conversation-facts" style="margin-bottom:20px">已运行 <span id="detail-duration">${duration}</span></div>`:''}
    ${t.detail&&live?`<div class="activity-note ${t.state==='stalled'?'warning':''}">${esc(t.detail)}</div>`:''}`);
  setDetailHtml('latest-messages', `
    <article class="message user-message"><div class="message-label">${icon('user')}最近用户消息</div><div class="message-body">${user?markdown(user):'<p>未采集到用户消息</p>'}</div></article>
    ${turn?.notification?`<article class="message user-message"><div class="message-label">${icon('activity')}最近外部消息 · 原生系统通知</div><div class="message-body">${markdown(turn.notification)}</div></article>`:''}
    <article class="message"><div class="message-label">${icon('activity')}${turn?'最新回复':'最新结果摘要'}${reply?'<button onclick="copyReply()">'+icon('copy')+'复制</button>':''}</div><div class="message-body">${reply?markdown(reply):'<p>'+(live?'Agent 正在处理，结果到达后会更新。':'这条会话尚未采集到回复正文。')+'</p>'}</div></article>`);
  document.getElementById('detail-pin').textContent = pin?.known ? (pin.pinned ? '已在原 Harness 中置顶 · 修改请前往原应用' : '未在原 Harness 中置顶') : '此采集端尚未提供原 Harness 的置顶状态';
  const subs = document.getElementById('detail-subagents');
  const subagentHtml = renderSubagents(t.extra?.subagents, taskId(t));
  if (subs.renderedHtml !== subagentHtml) {
    const opened = new Set([...subs.querySelectorAll('.subagents[open]')].map(n => n.dataset.taskKey));
    setDetailHtml('detail-subagents', subagentHtml);
    for (const item of subs.querySelectorAll('.subagents')) item.open = opened.has(item.dataset.taskKey);
  }
  document.getElementById('detail-capture').textContent = `${workspace.detail?'':'正在读取详情… '}${t.extra?.metadata_only?'仅有原应用的会话索引，本机暂时没有正文日志。':turn?(turn.truncated?'内容较长，当前显示已采集的部分。':'显示当前采集到的最近一轮内容。'):'该采集端提供摘要，更新采集端后可查看较完整的回复。'} ${hasHistory?'历史记录通过原应用按需读取；连接状态和可用内容见会话记录。':''}`;
  workspace.renderedSignature = signature;
  if(typeof renderInteraction==='function')renderInteraction();
  // Adjust only for layout above the reader. History owns its own subsequent updates.
  if (anchor) el.scrollTop += anchor.region.getBoundingClientRect().top - anchor.top;
  if(typeof ensureHistory==='function')ensureHistory();
  if (opening || (workspace.detail && el.pendingOffset != null)) {
    const target = opening ? (workspace.scroll.get(workspace.selected) || 0) : el.pendingOffset;
    el.scrollTop = target;
    el.pendingOffset = !workspace.detail && target > el.scrollTop + 1 ? target : null;
  }
}
function openProject(key) {workspace.project=key;workspace.device='';workspace.query='';workspace.tool='';workspace.state='';document.getElementById('session-search').value='';navigate('sessions');showList();}
function openDeviceTasks(key) {workspace.device=key;workspace.project='';workspace.query='';workspace.tool='';workspace.state='';document.getElementById('session-search').value='';navigate('sessions');showList();}
function replaceOverview(el,html) {
  if(el.innerHTML===html)return;
  const active=el.contains(document.activeElement)?document.activeElement:null;
  const focusKey=active?.dataset.focusKey,top=el.scrollTop;
  el.innerHTML=html;
  if(focusKey)for(const button of el.querySelectorAll('[data-focus-key]')) {
    if(button.dataset.focusKey===focusKey){button.focus({preventScroll:true});break;}
  }
  el.scrollTop=top;
}
function renderProjects() {
  const groups=projectGroups();
  replaceOverview(document.getElementById('projects-view'),`<div class="overview-heading"><div><h1>项目</h1><p>按设备和工作目录整理 · ${groups.length} 个项目位置</p></div></div><div class="overview-grid">${groups.map(p=>`<article class="overview-card"><h2>${esc(p.name)}</h2><p>${esc(p.machine)}</p><p>${esc(p.cwd || '采集端提供的项目名称，尚无完整路径')}</p><p>${p.tasks.length} 个会话 · ${p.tasks.filter(t=>t.state==='running').length} 个进行中</p><button data-focus-key="${esc('project:'+p.key)}" onclick="openProject(${jsArg(p.key)})">查看项目会话 →</button></article>`).join('') || '<div class="list-empty">尚未发现项目</div>'}</div>`);
}
function renderDevices() {
  const entries=Object.entries(workspace.machines);
  const device=entries.find(([id])=>id===workspace.selectedDevice);
  replaceOverview(document.getElementById('devices-view'),`<div class="overview-heading"><div><h1>设备</h1><p>${entries.length} 台已知设备 · 点击查看连接信息和会话</p></div></div><div class="overview-grid">${entries.map(([id,m])=>`<article class="overview-card"><h2><span class="state-dot ${m.online?'done':''}"></span>${esc(m.machine || id)}</h2><p>${m.online?'在线':'离线'} · ${m.tasks || 0} 个会话</p><p>${esc((m.ips || []).join(' / ') || '未上报地址')}</p><button data-focus-key="${esc('device:'+id)}" onclick="showDevice(${jsArg(id)})">查看设备</button></article>`).join('')}</div>${device?deviceDetail(...device):''}`);
}
function showDevice(key) {workspace.selectedDevice=key;renderDevices();document.getElementById('device-detail')?.scrollIntoView({block:'nearest',behavior:'smooth'});}
function deviceDetail(id,m) {
  const key=m.machine_id?'id:'+m.machine_id:'legacy:'+(m.machine || id);
  return `<section id="device-detail" class="device-detail"><h2>${esc(m.machine || id)}</h2><p>状态：${m.online?'在线':'离线'} · 最后连接：${m.last_seen?esc(new Date(m.last_seen*1000).toLocaleString()):'未知'}</p><p>设备 ID：${esc(m.machine_id || '旧版客户端')}</p><p>连接地址：${esc((m.ips || []).join(' / ') || '未上报')}</p><button data-focus-key="${esc('sessions:'+key)}" onclick="openDeviceTasks(${jsArg(key)})">查看该设备会话</button>${!m.local?` <button data-focus-key="${esc('remove:'+id)}" onclick="deleteMachine(${jsArg(id)})">移除设备记录</button>`:''}</section>`;
}
async function load() {
  if(workspace.loading)return;workspace.loading=true;
  try {
    const response=await fetch('/api/tasks?all=1');
    if(response.status===401){showAuthView(false);return;}
    if(!response.ok)throw new Error('连接中断，正在重试');
    const data=await response.json();workspace.tasks=data.tasks || [];workspace.machines=data.machines || {};workspace.errors=data.errors || {};
    for(const [id,name] of [['c-att','attention'],['c-done','done'],['c-run','running']])document.getElementById(id).textContent=data.counts[name];
    document.getElementById('updated').textContent='扫描于 '+new Date(data.scanned_at*1000).toLocaleTimeString();
    document.getElementById('connection-status').textContent=Object.keys(workspace.errors).length?'部分采集失败：'+Object.entries(workspace.errors).map(([k,v])=>k+': '+v).join('；'):'';
    if(workspace.page==='sessions'){renderList();await refreshDetail();if(typeof refreshInteraction==='function')await refreshInteraction();}
    if(workspace.page==='projects')renderProjects();
    if(workspace.page==='devices')renderDevices();
    if(!document.querySelector('.app-nav button.active'))navigate(workspace.page);
  }catch(error){document.getElementById('connection-status').textContent=error.message || '连接中断，正在重试';}
  finally{workspace.loading=false;}
}
