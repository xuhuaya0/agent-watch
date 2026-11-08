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
  const displayName=name=>(name || 'PC').replace(/\.local$/i,'');
  const shortName=name=>{
    const clean=displayName(name).replace(/[^\p{L}\p{N}]/gu,'');
    for(const [model,label] of [['macbookair','Air'],['macbookpro','Pro'],['macmini','Mini'],
      ['macstudio','Studio'],['macpro','Mac Pro'],['imac','iMac'],['macbook','Book']])
      if(clean.toLowerCase().includes(model))return label;
    return /mac/i.test(clean)?'Mac':clean.length<=6?clean:clean.slice(0,3);
  };
  const short=shortName(t.machine);
  if(workspace.tasks.some(x=>deviceKey(x)!==deviceKey(t) && shortName(x.machine)===short))
    return displayName(t.machine);
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
const sessionInfo = document.getElementById('session-info');
function closeChatInfo() {
  sessionInfo.open = false;
  sessionInfo.querySelector('summary').focus({preventScroll:true});
}
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && sessionInfo.open) { event.preventDefault(); closeChatInfo(); }
});
document.addEventListener('click', event => {
  if (sessionInfo.open && !sessionInfo.contains(event.target)) sessionInfo.open = false;
});
for (const type of ['wheel', 'touchstart', 'pointerdown', 'keydown', 'focusin']) {
  readerPane.addEventListener(type, () => { readerPane.pendingOffset = null; readerPane.openAtLatest = false; }, {passive:true});
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
function markdownDestination(text,start) {
  if(text[start]!=='(')return null;
  let pos=start+1,target='',end;
  if(text[pos]==='<') {
    end=text.indexOf('>',pos+1);if(end<0)return null;
    target=text.slice(pos+1,end);pos=end+1;
  }else {
    let depth=0;
    for(;pos<text.length;pos++) {
      const c=text[pos];
      if(c==='\\' && /[()\\]/.test(text[pos+1]||'')){target+=text[++pos];continue;}
      if(c==='('){depth++;target+=c;continue;}
      if(c===')'){if(!depth)break;depth--;target+=c;continue;}
      if(/\s/.test(c)&&!depth)break;
      target+=c;
    }
  }
  while(/\s/.test(text[pos]||'')&&pos<text.length)pos++;
  if(text[pos]==='"'||text[pos]==="'") {
    const quote=text[pos++];end=text.indexOf(quote,pos);if(end<0)return null;pos=end+1;
    while(/\s/.test(text[pos]||'')&&pos<text.length)pos++;
  }
  return text[pos]===')'&&target&&target.length<=4096?{target,end:pos+1}:null;
}
function safeMessageUrl(value,image=false) {
  if(typeof value!=='string'||!/^https?:\/\//i.test(value)||/[\u0000-\u0020\u007f\\]/.test(value))return null;
  try {const url=new URL(value);return (url.protocol==='https:'||!image&&url.protocol==='http:')&&!url.username&&!url.password?url.href:null;}catch(_){return null;}
}
function messageFileChip(label,source,image=false,error='') {
  const name=label||source.split(/[\\/]/).pop()||'附件';
  const kind=image?'图片':'文件';
  const reason=({attachment_too_large:kind+'超过预览上限',attachment_changed:kind+'已变化，请刷新会话',attachment_unsupported:'此'+kind+'格式暂不支持',attachment_unavailable:'原设备'+kind+'暂不可用'})[error];
  return `<span class="message-file" title="${esc(source)}"><span>${esc(name)}</span><small>${esc(reason||(image?'本地图片 · 需原设备提供':'原设备文件'))}</small></span>`;
}
function messageImage(label,target,context) {
  const url=safeMessageUrl(target,true);
  if(url&&!/\.svg(?:$|[?#])/i.test(url))return `<a class="message-image-link message-external-image" href="${esc(url)}" target="_blank" rel="noopener noreferrer"><img class="message-image" src="${esc(url)}" alt="${esc(label)}" loading="lazy" decoding="async" referrerpolicy="no-referrer" onerror="messageImageFailed(this)"></a>`;
  const item=context?.attachments?.find(value=>value.source===target);
  if(!item?.id||!context?.sessionId)return messageFileChip(label,target,true,item?.error);
  if(!/^[A-Za-z0-9_-]{32}$/.test(item.id)||!Number.isInteger(item.width)||!Number.isInteger(item.height))return messageFileChip(label,target,true);
  return `<span class="message-attachment" data-attachment-id="${esc(item.id)}" data-attachment-session="${esc(context.sessionId)}" style="aspect-ratio:${item.width}/${item.height}"><a class="message-image-link" target="_blank" rel="noopener noreferrer" hidden><img class="message-image" alt="${esc(label||item.name)}" width="${item.width}" height="${item.height}" decoding="async"></a><button type="button" class="attachment-load" onclick="loadMessageAttachment(this)">${esc(label||item.name)} · 加载图片</button><span class="attachment-status" role="status"></span></span>`;
}
function messageHtml(label,target,context) {
  const item=context?.attachments?.find(value=>value.source===target);
  if(!item?.id||item.mime!=='text/html'||!context?.sessionId||!/^[A-Za-z0-9_-]{32}$/.test(item.id))return messageFileChip(label,target,false,item?.error);
  return `<span class="message-attachment message-html" data-attachment-kind="html" data-attachment-id="${esc(item.id)}" data-attachment-session="${esc(context.sessionId)}"><button type="button" class="attachment-load" onclick="toggleMessageHtml(this)" aria-expanded="false">${esc(label||item.name)} · 预览网页</button><span class="attachment-status" role="status"></span><iframe sandbox="" referrerpolicy="no-referrer" title="${esc(label||item.name)} · 静态网页预览" hidden></iframe></span>`;
}
function toggleMessageHtml(button) {
  const node=button.closest('.message-attachment'),frame=node?.querySelector('iframe');if(!frame)return;
  if(node.dataset.attachmentState==='loaded') {
    frame.hidden=!frame.hidden;button.setAttribute('aria-expanded',String(!frame.hidden));return;
  }
  loadMessageAttachment(button);
}
function inlineMarkdown(text,context={},depth=0) {
  text=String(text||'');let out='',plain='';
  const flush=()=>{out+=esc(plain).replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>');plain='';};
  for(let pos=0;pos<text.length;) {
    if(text[pos]==='\\'&&/[\\`*_[\]()!]/.test(text[pos+1]||'')){plain+=text[pos+1];pos+=2;continue;}
    if(text[pos]==='`') {
      const end=text.indexOf('`',pos+1);
      if(end>pos+1){flush();out+='<code>'+esc(text.slice(pos+1,end))+'</code>';pos=end+1;continue;}
    }
    if(text.startsWith('<image',pos)) {
      const tag=text.slice(pos).match(/^<image\b[^>]{0,2500}>/),source=tag?.[0].match(/\bpath=(["'])(.*?)\1/);
      if(source&&context?.attachments?.some(item=>item.source===source[2])) {
        flush();out+=messageImage('图片附件',source[2],context);pos+=tag[0].length;continue;
      }
    }
    const image=text.startsWith('![',pos),link=text[pos]==='[';
    if((image||link)&&depth<2) {
      const begin=pos+(image?2:1),offset=text.slice(begin,begin+501).indexOf(']'),close=offset<0?-1:begin+offset;
      const dest=close>=0?markdownDestination(text,close+1):null;
      if(dest) {
        flush();const label=text.slice(begin,close),url=safeMessageUrl(dest.target);
        if(image)out+=messageImage(label,dest.target,context);
        else if(url)out+=`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${inlineMarkdown(label,{},depth+1)}</a>`;
        else if(/\.html?$/i.test(dest.target))out+=messageHtml(label,dest.target,context);
        else out+=messageFileChip(label,dest.target);
        pos=dest.end;continue;
      }
    }
    plain+=text[pos++];
  }
  flush();return out;
}
function markdown(text,context={}) {
  // Only generated elements are HTML; raw HTML and code remain escaped.
  const blocks=String(text || '').split(/```[^\n]*\n([\s\S]*?)(?:```|$)/g);
  return blocks.map((block,index)=>index%2 ? `<div class="code-wrap"><button onclick="copyCode(this)">复制代码</button><pre><code>${esc(block)}</code></pre></div>` :
    block.split(/\n\s*\n/).filter(x=>x.trim()).map(p=>{
      const h=p.match(/^(#{1,3}) (.+)$/);
      if(h)return `<h${h[1].length+1}>${inlineMarkdown(h[2],context)}</h${h[1].length+1}>`;
      if(p.split('\n').every(line=>/^\s*[-*] /.test(line)))return '<ul>'+p.split('\n').map(line=>'<li>'+inlineMarkdown(line.replace(/^\s*[-*] /,''),context)+'</li>').join('')+'</ul>';
      return '<p>'+inlineMarkdown(p,context).replace(/\n/g,'<br>')+'</p>';
    }).join('')).join('');
}
const messageMedia={active:0,queue:[],cache:new Map(),observer:null};
function discardMessageMedia(key,entry) {
  for(const node of entry.nodes||[])if(node.isConnected) {
    node.dataset.attachmentState='error';
    const link=node.querySelector('a'),image=node.querySelector('img'),button=node.querySelector('button'),frame=node.querySelector('iframe');
    if(frame){frame.hidden=true;frame.removeAttribute('srcdoc');button.setAttribute('aria-expanded','false');}
    else {link.hidden=true;link.removeAttribute('href');image.removeAttribute('src');}
    button.hidden=false;button.disabled=false;
    node.querySelector('.attachment-status').textContent='预览已释放，可重新加载';
  }
  if(entry.url)URL.revokeObjectURL(entry.url);messageMedia.cache.delete(key);
}
function messageImageFailed(image) {
  const note=document.createElement('span');note.className='message-file';note.textContent=(image.alt||'图片')+' · 图片暂不可用';image.replaceWith(note);
}
function hydrateMessageAttachments(root) {
  if(!root?.querySelectorAll)return;
  for(const [key,entry] of messageMedia.cache)if(entry.session!==workspace.selected)discardMessageMedia(key,entry);
  if(!messageMedia.observer&&typeof IntersectionObserver!=='undefined')messageMedia.observer=new IntersectionObserver(entries=>{
    for(const entry of entries)if(entry.isIntersecting){messageMedia.observer.unobserve(entry.target);loadMessageAttachment(entry.target);}
  },{root:document.getElementById('session-detail'),rootMargin:'150px'});
  for(const node of root.querySelectorAll('.message-attachment')) {
    if(node.dataset.attachmentKind==='html')continue; // HTML previews are explicitly opened.
    if(node.dataset.attachmentState)continue;
    node.dataset.attachmentState='observed';
    if(messageMedia.observer)messageMedia.observer.observe(node);else loadMessageAttachment(node);
  }
}
function loadMessageAttachment(trigger) {
  const node=trigger.closest('.message-attachment');if(!node||['loading','loaded','queued'].includes(node.dataset.attachmentState))return;
  node.dataset.attachmentState='queued';messageMedia.queue.push(node);pumpMessageAttachments();
}
function pumpMessageAttachments() {
  while(messageMedia.active<2&&messageMedia.queue.length) {
    const node=messageMedia.queue.shift();if(!node.isConnected)continue;
    messageMedia.active++;fetchMessageAttachment(node).finally(()=>{messageMedia.active--;pumpMessageAttachments();});
  }
}
async function fetchMessageAttachment(node) {
  const session=node.dataset.attachmentSession,id=node.dataset.attachmentId,key=session+':'+id;
  const button=node.querySelector('button'),status=node.querySelector('.attachment-status');
  node.dataset.attachmentState='loading';button.disabled=true;status.textContent='正在从原设备读取…';
  try {
    let cached=messageMedia.cache.get(key);
    for(let attempt=0;!cached&&attempt<35;attempt++) {
      if(!node.isConnected||workspace.selected!==session)return;
      const response=await fetch('/api/attachments/tasks/'+encodeURIComponent(session)+'?id='+encodeURIComponent(id),{credentials:'same-origin',cache:'no-store'});
      if(!response.ok)throw new Error(response.status===401?'请重新登录':'原设备附件暂不可用');
      const mime=response.headers.get('Content-Type')||'',html=node.dataset.attachmentKind==='html';
      if((html&&/^text\/html(?:;|$)/.test(mime))||(!html&&/^image\/(png|jpeg|gif|webp)(?:;|$)/.test(mime))) {
        const blob=await response.blob();if(blob.size>4*1024*1024)throw new Error('附件超过预览上限');
        cached={...(html?{html:await blob.text()}:{url:URL.createObjectURL(blob)}),session,nodes:new Set()};
        if(messageMedia.cache.size>=8){const [old,entry]=messageMedia.cache.entries().next().value;discardMessageMedia(old,entry);}
        messageMedia.cache.set(key,cached);break;
      }
      const result=await response.json();
      if(result.ready)throw new Error(({attachment_changed:'附件已变化，请刷新会话',attachment_too_large:'附件超过预览上限',attachment_unsupported:'此附件格式暂不支持'})[result.error]||'原设备附件暂不可用');
      await new Promise(resolve=>setTimeout(resolve,800));
    }
    if(!cached)throw new Error('原设备读取超时，请重试');
    if(!node.isConnected||workspace.selected!==session)return;
    cached.nodes.add(node);
    if(node.dataset.attachmentKind==='html') {
      const frame=node.querySelector('iframe');frame.srcdoc=cached.html;frame.hidden=false;
      node.dataset.attachmentState='loaded';button.disabled=false;button.setAttribute('aria-expanded','true');
      status.textContent='静态预览 · 脚本与外部资源已禁用';return;
    }
    const link=node.querySelector('a'),image=node.querySelector('img');
    image.onload=()=>{node.dataset.attachmentState='loaded';button.hidden=true;status.textContent='';link.hidden=false;};
    image.onerror=()=>{node.dataset.attachmentState='error';button.disabled=false;status.textContent='图片无法显示，请重试';};
    link.href=cached.url;image.src=cached.url;
  }catch(error){if(node.isConnected){node.dataset.attachmentState='error';button.disabled=false;status.textContent=error.message;}}
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
function copyReply() {
  const task = workspace.detail || workspace.tasks.find(item => taskId(item) === workspace.selected);
  if (task) copyText(task.extra?.latest_turn?.assistant ?? task.detail ?? '');
}
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
function updateChatHeader(task, hasHistory) {
  const harness = {codex:'Codex', antigravity:'Antigravity', dsh:'DeepSeek Harness'}[task.tool] || task.tool;
  document.getElementById('detail-title').textContent = task.title;
  document.getElementById('detail-title').title = task.title;
  const status = task.machine_online === false ? '设备离线' : ZH[task.state] || task.state;
  const context = document.getElementById('detail-context');
  context.textContent = context.title = [task.machine, harness, status].filter(Boolean).join(' · ');
  const pin = task.extra?.native_pin;
  const facts = [
    ['设备', task.machine], ['Harness', harness], ['项目', task.project], ['目录', task.extra?.cwd], ['设备地址', task.machine_ips?.join(' / ')],
    ['最近活动', activityAt(task) ? new Date(activityAt(task)*1000).toLocaleString() : ''],
    ['置顶', pin?.known ? (pin.pinned ? '已置顶' : '未置顶') : ''],
  ];
  if (task.machine_online === false) facts.push(['会话状态', ZH[task.state] || task.state]);
  const live = ['running','stalled'].includes(task.state);
  if (live && task.detail) facts.push(['当前活动',task.detail]);
  const started = live && Number(task.turn_started_at || task.state_since);
  const elapsed = started ? `<dt>已运行</dt><dd id="detail-duration" data-started="${started}"></dd>` : '';
  setDetailHtml('detail-facts', facts.filter(([,value]) => value).map(([label,value]) =>
    `<dt>${esc(label)}</dt><dd>${esc(value)}</dd>`).join('') + elapsed);
  const actions = [];
  if (hasHistory) actions.push('<button type="button" onclick="closeChatInfo();loadHistory(true)">刷新记录</button>');
  if (!['running','idle'].includes(task.state) && !task.acked) {
    actions.push(`<button type="button" onclick="closeChatInfo();ack(${jsArg(task.key)})">知道了</button>`);
  }
  setDetailHtml('detail-extra-actions',actions.join(''));
  const subs = document.getElementById('detail-subagents');
  const content = renderSubagents(task.extra?.subagents, taskId(task));
  if (subs.renderedHtml !== content) {
    const opened = new Set([...subs.querySelectorAll('.subagents[open]')].map(n => n.dataset.taskKey));
    setDetailHtml('detail-subagents',content);
    for (const item of subs.querySelectorAll('.subagents')) item.open = opened.has(item.dataset.taskKey);
  }
}
function capturedChatHtml(task, loaded) {
  const turn = task.extra?.latest_turn;
  const user = turn?.user ?? task.prompt;
  const reply = turn?.assistant ?? task.detail;
  const messages = [];
  if (user) messages.push(`<article class="message user-message" data-captured-kind="user" aria-label="你"><div class="message-body">${markdown(user)}</div></article>`);
  if (turn?.notification) messages.push(`<article class="message user-message" data-captured-kind="notification"><small>原生系统通知</small><div class="message-body">${markdown(turn.notification)}</div></article>`);
  if (reply && turn?.assistant_phase === 'commentary') {
    messages.push(`<details class="history-tools" data-captured-process><summary>过程</summary><div class="history-tools-list"><article class="message history-commentary"><div class="message-body">${markdown(reply)}</div></article></div></details>`);
  } else if (reply) messages.push(`<article class="message" aria-label="${turn?'最新回复':'最新结果摘要'}">${turn?'':'<small>结果摘要</small>'}<div class="message-body">${markdown(reply)}</div><div class="message-actions"><button type="button" title="复制回复" aria-label="复制回复" onclick="copyReply()">${icon('copy')}</button></div></article>`);
  if (turn?.truncated) messages.push('<p class="capture-note">仅显示已采集的部分。</p>');
  if (!messages.length) {
    const empty = !loaded ? '正在读取…' : task.extra?.metadata_only ? '原应用仅提供了会话索引。' : task.state === 'running' ? '正在处理…' : '暂无消息';
    messages.push(`<p class="capture-note">${empty}</p>`);
  }
  return messages.join('');
}
function updateCapturedChat(task, loaded) {
  const region = document.getElementById('latest-messages');
  const turn = JSON.stringify([taskId(task),task.turn_started_at,task.extra?.latest_turn?.user]);
  const expanded = region.capturedTurn === turn && region.querySelector('[data-captured-process]')?.open;
  setDetailHtml('latest-messages', capturedChatHtml(task, loaded));
  region.dataset.session = taskId(task);
  const sources = {user:task.extra?.latest_turn?.user ?? task.prompt,notification:task.extra?.latest_turn?.notification};
  for (const [kind,text] of Object.entries(sources)) {
    const record = region.querySelector(`[data-captured-kind="${kind}"]`);
    if (record) record.capturedMessage = {session:taskId(task),at:activityAt(task),text};
  }
  region.capturedTurn = turn;
  const process = region.querySelector('[data-captured-process]');
  if (process) process.open = Boolean(expanded);
}
function settleChatOpening() {
  if (!readerPane.openAtLatest) return;
  readerPane.scrollTop = readerPane.scrollHeight;
  readerPane.openAtLatest = false;
}
function updateChatDuration() {
  if (!sessionInfo.open) return;
  const elapsed = document.getElementById('detail-duration');
  if (elapsed) elapsed.textContent = fmt(Math.max(0, Math.floor(Date.now()/1000 - Number(elapsed.dataset.started))));
}
sessionInfo.addEventListener('toggle', updateChatDuration);
function renderDetail() {
  if (workspace.page !== 'sessions') return;
  const pane = readerPane;
  const brief = workspace.tasks.find(t => taskId(t) === workspace.selected);
  const info = document.getElementById('session-info');
  info.hidden = !brief;
  document.getElementById('session-control').hidden = !brief;
  if (!brief) {
    saveScroll();
    delete pane.dataset.session;
    pane.pendingOffset = null;
    pane.openAtLatest = false;
    info.open = false;
    document.getElementById('detail-title').textContent = '';
    document.getElementById('detail-context').textContent = '';
    pane.innerHTML = `<div class="blank-state">${icon('conversations')}<h2>${workspace.selected?'等待设备上报':'选择一个会话'}</h2></div>`;
    workspace.renderedSignature = '';
    if (typeof renderSessionActions === 'function') renderSessionActions();
    return;
  }
  const task = workspace.detail ? {...workspace.detail,...brief,extra:{...workspace.detail.extra,...brief.extra}} : brief;
  const signature = JSON.stringify([workspace.selected,workspace.detailSignature,brief.extra?.native_pin,brief.acked,brief.machine_online,brief.machine]);
  if (signature === workspace.renderedSignature) {
    updateChatDuration();
    if (typeof ensureHistory === 'function') ensureHistory();
    return;
  }
  const opening = pane.dataset.session !== workspace.selected;
  const anchor = opening ? null : detailAnchor(pane);
  const hasHistory = ['codex','antigravity','dsh'].includes(task.tool);
  if (opening) {
    pane.dataset.session = workspace.selected;
    pane.pendingOffset = null;
    pane.openAtLatest = !workspace.scroll.has(workspace.selected);
    info.open = false;
    pane.innerHTML = `${hasHistory ? '<section id="session-history" aria-label="会话记录"></section>' : ''}
      <section id="latest-messages"></section><div id="chat-outgoing"></div>`;
  }
  updateChatHeader(task, hasHistory);
  updateChatDuration();
  updateCapturedChat(task, Boolean(workspace.detail));
  workspace.renderedSignature = signature;
  if (typeof renderInteraction === 'function') renderInteraction();
  if (anchor) pane.scrollTop += anchor.region.getBoundingClientRect().top - anchor.top;
  if (typeof ensureHistory === 'function') ensureHistory();
  if ((opening && workspace.scroll.has(workspace.selected)) || (workspace.detail && pane.pendingOffset != null)) {
    const target = opening ? workspace.scroll.get(workspace.selected) : pane.pendingOffset;
    pane.scrollTop = target;
    pane.pendingOffset = !workspace.detail && target > pane.scrollTop + 1 ? target : null;
  } else if (opening) {
    pane.scrollTop = pane.scrollHeight || 0;
  }
  const history = hasHistory && typeof historyState === 'function' && historyState(workspace.selected);
  if (workspace.detail && (!hasHistory || history?.loaded || history?.error)) settleChatOpening();
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
