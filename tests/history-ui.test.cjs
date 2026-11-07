const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('fs'),vm=require('vm');
class HistoryElement {
 constructor(tag='div') {this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.html='';this.open=false;}
 get innerHTML(){return this.html+this.children.map(node=>node.innerHTML).join('');}
 set innerHTML(value){this.replaceChildren();this.html=value;this.capturedNodes={};}
 addEventListener(){}
 setAttribute(name,value){this[name]=value;}
 querySelector(selector){
  const kind=selector.match(/^\[data-captured-kind="(user|notification)"\]$/)?.[1];
  if(kind&&this.html.includes(`data-captured-kind="${kind}"`)){
   this.capturedNodes ||= {};const node=this.capturedNodes[kind] ||= new HistoryElement('article');node.parentElement=this;return node;
  }
  return this.children.find(element=>'#'+element.id===selector)||null;
 }
 querySelectorAll(){return [];}
 get firstElementChild(){return this.children[0]||null;}
 get lastElementChild(){return this.children.at(-1)||null;}
 get nextElementSibling(){return this.parentElement?.children[this.parentElement.children.indexOf(this)+1]||null;}
 get tools(){return this.children.flatMap(node=>node.historyMessage?.role==='tool'?[node]:node.tools);}
 get toolGroups(){return this.children.flatMap(node=>node.historyProcessIds?[node]:node.toolGroups);}
 get isConnected(){return Boolean(this.parentElement?.isConnected||this.root);}
 getBoundingClientRect(){return {top:0,bottom:0};}
 contains(node){return node===this||this.children.some(child=>child.contains(node));}
 remove(){if(this.parentElement){this.parentElement.children.splice(this.parentElement.children.indexOf(this),1);this.parentElement=null;}}
 insertBefore(node,ref){node.remove();node.parentElement=this;this.children.splice(ref?this.children.indexOf(ref):this.children.length,0,node);}
 replaceChildren(...nodes){for(const child of [...this.children])child.remove();for(const child of nodes)this.insertBefore(child,null);}
}
function setup(){
 const elements=Object.fromEntries(['session-detail','session-history','latest-messages','chat-outgoing','session-control'].map(id=>[id,new HistoryElement()]));
 elements['chat-outgoing'].id='chat-outgoing';
 elements['session-control'].id='session-control';
 const pane=elements['session-detail'];pane.root=true;pane.scrollTop=20;pane.clientHeight=0;
 const detailView=new HistoryElement();detailView.root=true;
 detailView.replaceChildren(pane,elements['session-control']);
 pane.replaceChildren(elements['session-history'],elements['latest-messages'],elements['chat-outgoing']);
 const ctx=vm.createContext({workspace:{selected:'a',page:'sessions',tasks:[{ui_key:'a',tool:'codex',activity_at:1}]},document:{getElementById:id=>elements[id],createElement:tag=>new HistoryElement(tag)},taskId:t=>t.ui_key,activityAt:t=>t.activity_at,esc:s=>String(s??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;'),markdown:s=>String(s).replaceAll('<','&lt;'),icon:()=>'<svg aria-hidden="true"></svg>',setTimeout,clearTimeout,AbortController,Date,TextEncoder,showAuthView(){}});
 vm.runInContext(fs.readFileSync('static/history.js','utf8'),ctx);return{ctx,elements,run:s=>vm.runInContext(s,ctx)};
}
function combinedReader(tool='codex'){
 const result=setup(),{ctx,elements,run}=result,pane=elements['session-detail'];
 for(const id of ['session-info','detail-title','detail-context','detail-facts','detail-extra-actions','detail-subagents'])elements[id]=new HistoryElement();
 const descriptor=Object.getOwnPropertyDescriptor(HistoryElement.prototype,'innerHTML');
 Object.defineProperty(pane,'innerHTML',{get(){return descriptor.get.call(this)},set(value){
  if(!value.includes('id="session-history"'))return descriptor.set.call(this,value);
  for(const id of ['session-history','latest-messages','chat-outgoing']){elements[id]=new HistoryElement();elements[id].id=id;}
  this.replaceChildren(elements['session-history'],elements['latest-messages'],elements['chat-outgoing']);this.html='';this.scrollTop=0;
 }});
 Object.assign(ctx,{readerPane:pane,sessionInfo:elements['session-info'],ZH:{done:'已完成'},renderSubagents:()=>'',settingsSummaryHtml:()=>'',harnessName:()=>tool,jsArg:JSON.stringify});
 const source=fs.readFileSync('static/workspace.js','utf8');
 vm.runInContext(source.slice(source.indexOf('function setDetailHtml('),source.indexOf('function openProject(')),ctx);
 vm.runInContext(fs.readFileSync('static/control.js','utf8'),ctx);
 ctx.task={ui_key:'a',key:'a',tool,state:'done',title:'Synthetic chat',machine:'Test device',activity_at:20,extra:{latest_turn:tool==='antigravity'?{user:'Earlier request',notification:'Unique new reply'}:{user:'Unique new reply'}}};
 run(`Object.assign(workspace,{tasks:[task],detail:task,detailSignature:'detail-20',renderedSignature:'',scroll:new Map([['a',420]])});
 interaction.outgoing.set('a',{id:'command',text:'Unique new reply',state:'delivered',createdAt:1000,baselineAt:10,previousHistoryIds:[],notification:task.tool==='antigravity'});
 Object.assign(historyState('a'),{version:'20'})`);
 ctx.apiFetch=()=>{throw Error('This cached render must not perform a history request')};
 return result;
}
function visibleReplyCount(elements){
 const fallback=elements['latest-messages'];
 return ((fallback.hidden?'':fallback.innerHTML)+elements['chat-outgoing'].innerHTML+elements['session-history'].innerHTML).split('Unique new reply').length-1;
}
test('history escapes content and never renders a reasoning role',()=>{
 const {run}=setup();const html=run(`historyMessageBody({role:'user',label:'<img>',text:'<script>',id:'1',timestamp:''},'a')`);
 assert.doesNotMatch(html,/<img>|<script>/);assert.match(html,/&lt;script>/);
});
test('late history cannot overwrite the selected chat',async()=>{
 const {ctx,elements,run}=setup();let release;
 ctx.apiFetch=()=>new Promise(r=>release=r);const pending=run('loadHistory(true)');run('workspace.selected="b"');elements['session-history'].innerHTML='B';
 release({ok:true,json:async()=>({ready:true,messages:[{id:'1',role:'assistant',text:'A'}],next_cursor:null})});await pending;
 assert.equal(elements['session-history'].innerHTML,'B');
});
test('loading earlier pages deduplicates messages and retains order',async()=>{
 const {ctx,run}=setup();run(`Object.assign(historyState('a'),{messages:[{id:'2',role:'user',text:'two'}],next:'cursor',loaded:true})`);
 ctx.apiFetch=async()=>({ok:true,json:async()=>({ready:true,messages:[{id:'1',role:'user',text:'one'},{id:'2',role:'user',text:'two'}],next_cursor:null})});
 await run('loadHistory(false)');assert.equal(run(`historyState('a').messages.map(m=>m.id).join(',')`),'1,2');
});
test('unavailable history leaves the latest captured messages visible',async()=>{
 const {ctx,elements,run}=setup();ctx.apiFetch=async()=>({ok:false,json:async()=>({error:'history_unavailable'})});
 await run('loadHistory(true)');assert.equal(elements['latest-messages'].hidden,false);assert.match(elements['session-history'].innerHTML,/无法读取历史/);
});
test('delivered reply stays visible beside retained older history despite a matching collector summary',()=>{
 const {ctx,elements,run}=setup();
 vm.runInContext(fs.readFileSync('static/control.js','utf8'),ctx);
 run(`workspace.detail={ui_key:'a',activity_at:20,extra:{latest_turn:{user:'New reply'}}};
 Object.assign(historyState('a'),{messages:[{id:'earlier-answer',role:'assistant',text:'Earlier result',timestamp:'2026-01-01T00:00:00Z'}],loaded:true,older:true,version:'1'});
 interaction.outgoing.set('a',{id:'command',text:'New reply',state:'delivered',createdAt:1000,baselineAt:10,previousHistoryIds:['earlier-answer']});
 renderHistory()`);
 const pane=elements['session-detail'],list=elements['session-history'].lastElementChild;
 const earlier=list.firstElementChild;pane.scrollTop=420;
 assert.equal(elements['latest-messages'].hidden,true);
 assert.match(run(`outgoingHtml('a')`),/New reply/);
 ctx.apiFetch=()=>{throw Error('Older reading must not trigger a latest-page request')};
 run(`workspace.tasks[0].activity_at=20;ensureHistory()`);
 assert.equal(run(`historyState('a').older`),true);
 assert.equal(run(`historyState('a').updated`),true);
 assert.equal(list.firstElementChild,earlier);assert.equal(pane.scrollTop,420);
 assert.match(elements['session-history'].innerHTML,/有新内容/);
 assert.match(run(`outgoingHtml('a')`),/已送达/);
});
test('echo reconciliation requires the matching native message mounted in this conversation',()=>{
 const {ctx,elements,run}=setup();
 vm.runInContext(fs.readFileSync('static/control.js','utf8'),ctx);
 run(`Object.assign(historyState('a'),{messages:[{id:'old',role:'assistant',text:'Earlier answer'}],loaded:true});renderHistory();
 interaction.outgoing.set('a',{id:'command',text:'New reply',state:'delivered',createdAt:1000,previousHistoryIds:['old']});
 historyState('a').messages=[{id:'native-user',role:'user',text:'New reply',timestamp:'2026-01-01T00:00:01Z'}]`);
 // A newer model in memory is not yet the mounted history page.
 assert.match(run(`outgoingHtml('a')`),/New reply/);
 run('renderHistory()');assert.equal(run(`outgoingHtml('a')`),'');
 const root=elements['session-history'];root.hidden=true;
 assert.match(run(`outgoingHtml('a')`),/New reply/);root.hidden=false;
 root.dataset.session='other-chat';assert.match(run(`outgoingHtml('a')`),/New reply/);root.dataset.session='a';
 // Returning to an older page must not inherit a sticky reconciliation bit.
 run(`historyState('a').messages=[{id:'older',role:'assistant',text:'An earlier page'}];renderHistory()`);
 assert.match(run(`outgoingHtml('a')`),/New reply/);
 run(`historyState('a').messages=[{id:'native-user',role:'user',text:'New reply',timestamp:'2026-01-01T00:00:01Z'}];renderHistory()`);
 assert.equal(run(`outgoingHtml('a')`),'');
 run(`workspace.selected='other-chat'`);assert.match(run(`outgoingHtml('a')`),/New reply/);
});
test('actual fallback and outgoing echo show one delivered message when history is unavailable or empty for every harness',()=>{
 for(const tool of ['codex','antigravity','dsh'])for(const loaded of [false,true]){
  const {elements,run}=combinedReader(tool);
  run(`Object.assign(historyState('a'),{loaded:${loaded},messages:[],error:${loaded?"''":"'history_unavailable'"}});renderDetail()`);
  assert.equal(elements['latest-messages'].hidden,false);
  assert.equal(visibleReplyCount(elements),1,`${tool}: ${loaded?'empty':'unavailable'} history`);
  assert.equal(elements['session-detail'].scrollTop,420);
 }
});
test('cached history mounting reconciles the echo in the same renderDetail without waiting for a control poll',()=>{
 for(const matching of [false,true]){
  const {elements,run}=combinedReader();
  run(`Object.assign(historyState('a'),{loaded:true,older:true,messages:[${matching?"{id:'native-user',role:'user',text:'Unique new reply',timestamp:'2026-01-01T00:00:01Z'}":"{id:'old',role:'assistant',text:'Earlier result'}"}]});renderDetail()`);
  assert.equal(elements['latest-messages'].hidden,true);
  assert.equal(visibleReplyCount(elements),1,matching?'Native user mounted':'Only the older page mounted');
  assert.equal(elements['session-detail'].scrollTop,420);
 }
});
test('fallback reconciliation requires visible same-chat provenance and never hides failed or uncertain replies',()=>{
 for(const tool of ['codex','antigravity','dsh']){
  const {elements,run}=combinedReader(tool);
  run(`Object.assign(historyState('a'),{loaded:true,messages:[]});renderDetail()`);
  const region=elements['latest-messages'],node=region.querySelector(`[data-captured-kind="${tool==='antigravity'?'notification':'user'}"]`);
  assert.equal(run(`outgoingHtml('a')`),'');
  region.hidden=true;assert.match(run(`outgoingHtml('a')`),/Unique new reply/);region.hidden=false;
  node.hidden=true;assert.match(run(`outgoingHtml('a')`),/Unique new reply/);node.hidden=false;
  region.dataset.session='other-chat';assert.match(run(`outgoingHtml('a')`),/Unique new reply/);region.dataset.session='a';
  node.capturedMessage.session='other-chat';assert.match(run(`outgoingHtml('a')`),/Unique new reply/);node.capturedMessage.session='a';
  node.capturedMessage.at=10;assert.match(run(`outgoingHtml('a')`),/Unique new reply/);node.capturedMessage.at=20;
  node.capturedMessage.text='Different mounted text';assert.match(run(`outgoingHtml('a')`),/Unique new reply/);node.capturedMessage.text='Unique new reply';
  run(`workspace.selected='other-chat'`);assert.match(run(`outgoingHtml('a')`),/Unique new reply/);run(`workspace.selected='a'`);
  for(const [state,label] of [['rejected','发送失败'],['unknown','送达情况待确认']]){
   run(`interaction.outgoing.get('a').state='${state}';renderOutgoing()`);
   assert.match(elements['chat-outgoing'].innerHTML,new RegExp(label));
  }
 }
});
test('empty native history keeps the captured reply visible with a single source of empty-state text',async()=>{
 const {ctx,elements,run}=setup();
 run("workspace.detail={extra:{latest_turn:{assistant:'Captured reply'}}}");
 elements['latest-messages'].innerHTML='<article>Captured reply</article>';
 ctx.apiFetch=async()=>({ok:true,json:async()=>({ready:true,messages:[],next_cursor:null})});
 await run('loadHistory(true)');
 assert.equal(elements['latest-messages'].hidden,false);
 assert.match(elements['latest-messages'].innerHTML,/Captured reply/);
 assert.doesNotMatch(elements['session-history'].innerHTML,/暂无消息/);
});
test('empty history arriving ahead of the full detail waits for the captured conversation before settling',async()=>{
 const {ctx,run}=setup();
 let settledBeforeDetail=false;
 ctx.settleChatOpening=()=>{settledBeforeDetail=true;};
 ctx.apiFetch=async()=>({ok:true,json:async()=>({ready:true,messages:[],next_cursor:null})});
 await run('loadHistory(true)');
 assert.equal(settledBeforeDetail,false);
});
test('all three harnesses load older history through the same reader',async()=>{
 for(const tool of ['codex','antigravity','dsh']) {
  const {ctx,elements,run}=setup();run(`workspace.tasks[0].tool=${JSON.stringify(tool)}`);
  ctx.apiFetch=async()=>({ok:true,json:async()=>({ready:true,messages:Array.from({length:20},(_,i)=>({id:String(i),role:'assistant',text:'Earlier answer'})),next_cursor:'older'})});
  await run('loadHistory(true)');assert.equal(elements['latest-messages'].hidden,true);
  assert.match(elements['session-history'].innerHTML,/Earlier answer/);
  assert.match(elements['session-history'].innerHTML,/加载更早的记录/);
 }
});
test('every harness defaults process records to collapsed while preserving ordinary and final replies',async()=>{
 for(const tool of ['codex','antigravity','dsh']) {
  const {ctx,elements,run}=setup();run(`workspace.tasks[0].tool=${JSON.stringify(tool)}`);
  // These match the normalized provider shapes: Antigravity keeps its tool
  // explanation within one tool step; Codex/DeepSeek preserve commentary.
  const process=tool==='antigravity'?
   [{id:'step',role:'tool',label:'工具调用',text:'Inspecting the sample\nread_file'},
    {id:'result',role:'tool',label:'工具结果',text:'Sample contents'}]:
   [{id:'comment',role:'assistant',phase:'commentary',text:'Inspecting the sample'},
    {id:'step',role:'tool',label:'read_file',text:'sample.txt'},
    {id:'result',role:'tool',label:'工具结果',text:'Sample contents'}];
  const messages=[{id:'ordinary',role:'assistant',text:'An earlier ordinary reply'},
   {id:'request',role:'user',text:'Inspect the sample'},...process,
   {id:'answer',role:'assistant',text:'The sample is ready'}];
  ctx.apiFetch=async()=>({ok:true,json:async()=>({ready:true,messages,next_cursor:null})});
  await run('loadHistory(true)');
  const history=elements['session-history'],list=history.lastElementChild,group=history.toolGroups[0];
  assert.equal(group.open,false,tool);assert.equal(history.toolGroups.length,1,tool);
  assert.ok(group.tools.every(row=>!row.open),tool);
  assert.deepEqual(list.children.filter(row=>!row.historyProcessIds).map(row=>row.dataset.historyId),
   ['ordinary','request','answer'],tool);
  assert.equal(group.firstElementChild.innerHTML,'工具调用 · 2',tool);
  group.open=true;history.tools[0].open=true;
  messages[messages.length-1]={...messages.at(-1),text:'The sample is ready, with details'};
  await run('loadHistory(true)');
  assert.equal(history.toolGroups[0],group,tool);assert.equal(group.open,true,tool);
  assert.equal(history.tools[0].open,true,tool);
  assert.equal(history.lastElementChild.lastElementChild.dataset.historyId,'answer',tool);
 }
});
test('only consecutive tools share a single collapsed disclosure and keep separate readable records',()=>{
 const {elements,run}=setup();
 run(`historyState('a').messages=[
  {id:'request',role:'user',text:'Request'},
  {id:'t1',role:'tool',label:'Inspect',text:'First result'},
  {id:'t2',role:'tool',label:'Check',text:'Second result'},
  {id:'answer',role:'assistant',text:'Answer'},
  {id:'t3',role:'tool',text:'Final result'}];renderHistory()`);
 const list=elements['session-history'].lastElementChild,groups=list.toolGroups;
 assert.equal(list.children.length,4);assert.equal(groups.length,2);
 assert.equal(groups[0].firstElementChild.innerHTML,'工具调用 · 2');
 assert.equal(groups[1].firstElementChild.innerHTML,'工具调用 · 1');
 assert.ok(groups.every(group=>group.open===false));
 assert.deepEqual(groups[0].tools.map(tool=>tool.dataset.historyId),['t1','t2']);
 assert.ok(list.tools.every(tool=>tool.open===false));
 assert.match(groups[0].tools[0].innerHTML,/<summary>Inspect<\/summary>.*First result/);
 assert.match(groups[0].tools[1].innerHTML,/<summary>Check<\/summary>.*Second result/);
});
test('an expanded tool run and its message nodes survive polling, and closing stays closed after append',()=>{
 const {ctx,elements,run}=setup();
 run(`historyState('a').messages=[{id:'t1',role:'tool',text:'Initial result'}];renderHistory()`);
 const history=elements['session-history'],group=history.toolGroups[0],tool=history.tools[0];
 ctx.group=group;group.open=true;run('rememberHistoryToolGroup(group)');
 ctx.opened=tool;tool.open=true;run('rememberHistoryTool(opened)');
 run(`historyState('a').messages=[{id:'t1',role:'tool',text:'Updated result'},{id:'t2',role:'tool',text:'Next result'}];renderHistory()`);
 assert.equal(history.toolGroups[0],group);assert.equal(history.tools[0],tool);
 assert.equal(group.open,true);assert.equal(tool.open,true);
 assert.equal(group.firstElementChild.innerHTML,'工具调用 · 2');assert.match(tool.innerHTML,/Updated result/);
 group.open=false;run('rememberHistoryToolGroup(group)');
 run(`historyState('a').messages=[...historyState('a').messages,{id:'t3',role:'tool',text:'Final result'}];renderHistory()`);
 assert.equal(group.open,false);assert.equal(tool.open,true);
 assert.equal(group.firstElementChild.innerHTML,'工具调用 · 3');
});
test('commentary stays with tool progress while final answers remain outside the collapsed group',()=>{
 const {elements,run}=setup();
 run(`historyState('a').messages=[
  {id:'request',role:'user',text:'Request'},
  {id:'c1',role:'assistant',phase:'commentary',text:'Inspecting inputs'},
  {id:'t1',role:'tool',text:'First result'},
  {id:'c2',role:'assistant',phase:'commentary',text:'Checking result'},
  {id:'t2',role:'tool',text:'Second result'},
  {id:'answer',role:'assistant',phase:'final_answer',text:'Final answer'},
  {id:'c3',role:'assistant',phase:'commentary',text:'Additional progress'}];renderHistory()`);
 const list=elements['session-history'].lastElementChild,groups=list.toolGroups;
 assert.equal(list.children.length,4);assert.equal(groups.length,2);
 assert.equal(groups[0].firstElementChild.innerHTML,'工具调用 · 2');
 assert.deepEqual(groups[0].lastElementChild.children.map(row=>row.dataset.historyId),['c1','t1','c2','t2']);
 assert.equal(groups[1].firstElementChild.innerHTML,'过程 · 1');
 assert.ok(groups.every(group=>!group.open));
 assert.match(groups[0].innerHTML,/Inspecting inputs/);
 assert.doesNotMatch(groups[0].innerHTML,/复制这条回复/);
 assert.equal(list.children[2].dataset.historyId,'answer');
 assert.match(list.children[2].innerHTML,/Final answer.*复制这条回复/);
});
test('polling respects an immediate disclosure click before the native toggle event arrives',()=>{
 const {elements,run}=setup();
 run(`historyState('a').messages=[{id:'t1',role:'tool',text:'First'}];renderHistory()`);
 const group=elements['session-history'].toolGroups[0];
 group.open=true;
 run(`historyState('a').messages=[...historyState('a').messages,{id:'t2',role:'tool',text:'Second'}];renderHistory()`);
 assert.equal(group.open,true);
 group.open=false;
 run(`historyState('a').messages=[...historyState('a').messages,{id:'t3',role:'tool',text:'Third'}];renderHistory()`);
 assert.equal(group.open,false);
});
test('a phase correction moves the same assistant record between progress and final-answer surfaces',()=>{
 const {elements,run}=setup();
 run(`historyState('a').messages=[{id:'answer',role:'assistant',phase:'commentary',text:'Answer'}];renderHistory()`);
 const history=elements['session-history'],row=history.toolGroups[0].lastElementChild.firstElementChild;
 run(`historyState('a').messages=[{id:'answer',role:'assistant',phase:'final_answer',text:'Answer'}];renderHistory()`);
 assert.equal(history.lastElementChild.firstElementChild,row);
 assert.equal(history.toolGroups.length,0);assert.equal(row.className,'message');
 assert.match(row.innerHTML,/复制这条回复/);
});
test('opened tool records survive changed content and reordered history by message ID',()=>{
 const {ctx,elements,run}=setup();const history=elements['session-history'];
 run(`Object.assign(historyState('a'),{messages:[{id:'tool-1',role:'tool',text:'old result'},{id:'tool-2',role:'tool',text:'other result'}],loaded:true});renderHistory()`);
 ctx.opened=history.tools[0];ctx.opened.open=true;run('rememberHistoryTool(opened)');
 run(`historyState('a').messages=[{id:'earlier',role:'tool',text:'earlier record'},{id:'tool-2',role:'tool',text:'updated other result'},{id:'tool-1',role:'tool',text:'new result'}];historyState('a').busy=true;renderHistory()`);
 assert.equal(history.tools.find(n=>n.dataset.historyId==='tool-1').open,true);
 assert.equal(history.tools.find(n=>n.dataset.historyId==='tool-2').open,false);
 assert.equal(history.tools.find(n=>n.dataset.historyId==='earlier').open,false);
 ctx.opened=history.tools.find(n=>n.dataset.historyId==='tool-1');ctx.opened.open=false;run('rememberHistoryTool(opened);historyState("a").busy=false;renderHistory()');
 assert.equal(history.tools.find(n=>n.dataset.historyId==='tool-1').open,false);
});
test('tool expansion is scoped to its conversation even when message IDs coincide',()=>{
 const {ctx,elements,run}=setup();
 run(`historyState('a').messages=[{id:'same',role:'tool',text:'A'}];renderHistory()`);
 ctx.group=elements['session-history'].toolGroups[0];ctx.group.open=true;run('rememberHistoryToolGroup(group)');
 ctx.opened=elements['session-history'].tools[0];ctx.opened.open=true;run('rememberHistoryTool(opened)');
 run(`workspace.selected='b';historyState('b').messages=[{id:'same',role:'tool',text:'B'}];renderHistory()`);
 assert.equal(elements['session-history'].toolGroups[0].open,false);
 assert.equal(elements['session-history'].tools[0].open,false);
 run(`workspace.selected='a';renderHistory()`);
 assert.equal(elements['session-history'].toolGroups[0].open,true);assert.equal(elements['session-history'].tools[0].open,true);
});
test('older-page tools merge into the current run without resetting expansion or duplicating records',async()=>{
 const {ctx,elements,run}=setup();
 run(`Object.assign(historyState('a'),{messages:[{id:'t2',role:'tool',text:'Second'},{id:'answer',role:'assistant',text:'Answer'}],loaded:true,next:'older'});renderHistory()`);
 const history=elements['session-history'],group=history.toolGroups[0],tool=history.tools[0];
 ctx.group=group;group.open=true;run('rememberHistoryToolGroup(group)');
 ctx.opened=tool;tool.open=true;run('rememberHistoryTool(opened)');
 ctx.apiFetch=async()=>({ok:true,json:async()=>({ready:true,messages:[{id:'request',role:'user',text:'Request'},{id:'t1',role:'tool',text:'First'},{id:'t2',role:'tool',text:'Second'}],next_cursor:null})});
 await run('loadHistory(false)');
 assert.equal(history.toolGroups.length,1);assert.equal(history.toolGroups[0],group);
 assert.equal(group.open,true);assert.equal(history.tools[1],tool);assert.equal(tool.open,true);
 assert.deepEqual(history.tools.map(row=>row.dataset.historyId),['t1','t2']);
 assert.equal(group.firstElementChild.innerHTML,'工具调用 · 2');
 assert.equal(history.lastElementChild.children[0].dataset.historyId,'request');
 assert.equal(history.lastElementChild.children[2].dataset.historyId,'answer');
});
test('tool runs regroup around message boundaries while retaining each tool identity and expansion',()=>{
 const {ctx,elements,run}=setup();
 run(`historyState('a').messages=[{id:'t1',role:'tool',text:'First'},{id:'middle',role:'assistant',text:'Middle'},{id:'t2',role:'tool',text:'Second'}];renderHistory()`);
 const history=elements['session-history'],[t1,t2]=history.tools;
 ctx.group=history.toolGroups[1];ctx.group.open=true;run('rememberHistoryToolGroup(group)');
 ctx.opened=t2;t2.open=true;run('rememberHistoryTool(opened)');
 run(`historyState('a').messages=[{id:'t2',role:'tool',text:'Second updated'},{id:'t1',role:'tool',text:'First'}];renderHistory()`);
 assert.equal(history.toolGroups.length,1);assert.equal(history.toolGroups[0].open,true);
 assert.deepEqual(history.tools,[t2,t1]);assert.equal(t2.open,true);
 run(`historyState('a').messages=[{id:'t2',role:'tool',text:'Second updated'},{id:'middle',role:'assistant',text:'Middle'},{id:'t1',role:'tool',text:'First'}];renderHistory()`);
 assert.equal(history.toolGroups.length,2);assert.deepEqual(history.tools,[t2,t1]);
 assert.ok(history.toolGroups.every(group=>group.open));
 assert.equal(t2.open,true);assert.equal(t1.open,false);
});
test('loaded assistant replies keep copy controls and copy the exact selected body',async()=>{
 const {ctx,elements,run}=setup(),copied=[];ctx.copyText=text=>copied.push(text);
 ctx.apiFetch=async()=>({ok:true,json:async()=>({ready:true,messages:[
  {id:'user',role:'user',text:'Request'},
  {id:'answer-1',role:'assistant',text:'**Exact Markdown**\n\n```text\n<result>\n```'},
  {id:'answer-2',role:'assistant',text:'A different answer'},
  {id:'tool',role:'tool',text:'Tool record'}],next_cursor:null})});
 await run('loadHistory(true)');assert.equal(elements['latest-messages'].hidden,true);
 assert.equal((elements['session-history'].innerHTML.match(/aria-label="复制这条回复"/g)||[]).length,2);
 ctx.button={dataset:{historySession:'a',historyId:'answer-1'}};
 run('copyHistoryReply(button)');assert.deepEqual(copied,['**Exact Markdown**\n\n```text\n<result>\n```']);
 ctx.button.dataset.historyId='tool';run('copyHistoryReply(button)');assert.equal(copied.length,1);
 ctx.button.dataset.historyId='answer-2';run('workspace.selected="b";copyHistoryReply(button)');assert.equal(copied.length,1);
});

function readingLayout(elements) {
 const pane=elements['session-detail'],list=elements['session-history'].lastElementChild;
 pane.clientHeight=200;
 pane.getBoundingClientRect=()=>({top:0,bottom:200});
 const rectAt=index=>({top:100+index*100-pane.scrollTop,bottom:200+index*100-pane.scrollTop});
 for(const row of list.children)row.getBoundingClientRect=()=>rectAt(list.children.indexOf(row));
 list.getBoundingClientRect=()=>({top:rectAt(0).top,bottom:rectAt(list.children.length).top});
 elements['chat-outgoing'].getBoundingClientRect=()=>rectAt(list.children.length);
 elements['session-control'].getBoundingClientRect=()=>({top:200,bottom:280});
 return {pane,list};
}
function processReadingLayout(elements) {
 const pane=elements['session-detail'],list=elements['session-history'].lastElementChild;
 pane.clientHeight=200;pane.getBoundingClientRect=()=>({top:0,bottom:200});
 const recordHeight=row=>row.historyMessage.role==='tool'?(row.open?220:40):100;
 const height=row=>row.historyProcessIds?40+(row.open?row.lastElementChild.children.reduce((sum,record)=>sum+recordHeight(record),0):0):100;
 const offset=row=>100+list.children.slice(0,list.children.indexOf(row)).reduce((sum,item)=>sum+height(item),0)-pane.scrollTop;
 for(const row of list.children) {
  row.getBoundingClientRect=()=>({top:offset(row),bottom:offset(row)+height(row)});
  if(!row.historyProcessIds)continue;
  row.firstElementChild.getBoundingClientRect=()=>({top:offset(row),bottom:offset(row)+40});
  for(const record of row.lastElementChild.children)record.getBoundingClientRect=()=>{
   const group=record.parentElement.parentElement;
   const top=offset(group)+40+group.lastElementChild.children.slice(0,group.lastElementChild.children.indexOf(record)).reduce((sum,item)=>sum+recordHeight(item),0);
   return {top,bottom:top+recordHeight(record)};
  };
 }
 return {pane,list};
}
function expandedProcess(run) {
 run(`Object.assign(historyState('a'),{messages:[
  {id:'t1',role:'tool',text:'First output'},
  {id:'t2',role:'tool',text:'Second output'},
  {id:'t3',role:'tool',text:'Third output'},
  {id:'answer',role:'assistant',text:'Final answer'}],loaded:true,next:'older'});
  historyState('a').openedToolGroups.add('t1');
  for(const id of ['t1','t2','t3'])historyState('a').openedTools.add(id);renderHistory()`);
}
test('a delayed earlier page keeps the tool output visible at arrival at its exact position',async()=>{
 const {ctx,elements,run}=setup();expandedProcess(run);
 const {pane,list}=processReadingLayout(elements),reading=list.tools[1],group=list.toolGroups[0];
 pane.scrollTop=100;
 let respond;ctx.apiFetch=()=>new Promise(resolve=>respond=resolve);
 const loading=run('loadHistory(false)');
 pane.scrollTop=390; // Read the second expanded output while the page is pending.
 const before=reading.getBoundingClientRect().top;
 respond({ok:true,json:async()=>({ready:true,messages:[{id:'older-tool',role:'tool',text:'Earlier output'}],next_cursor:null})});
 await loading;
 assert.equal(list.toolGroups[0],group);assert.equal(list.tools[2],reading);
 assert.equal(group.open,true);assert.equal(reading.open,true);
 assert.equal(reading.getBoundingClientRect().top,before);assert.equal(pane.scrollTop,430);
});
test('a live window cannot discard the tool being read even when the surrounding process group survives',async()=>{
 const {ctx,elements,run}=setup();expandedProcess(run);
 const {pane,list}=processReadingLayout(elements),reading=list.tools[1];pane.scrollTop=390;
 const before=reading.getBoundingClientRect().top;
 ctx.apiFetch=async()=>({ok:true,json:async()=>({ready:true,messages:[{id:'t3',role:'tool',text:'Third output'},{id:'answer',role:'assistant',text:'Final answer'}],next_cursor:null})});
 await run('loadHistory(true,true)');
 assert.equal(list.tools[1],reading);assert.equal(reading.getBoundingClientRect().top,before);
 assert.equal(pane.scrollTop,390);assert.equal(run("historyState('a').updated"),true);
 assert.match(elements['session-history'].innerHTML,/有新内容/);
});
test('a collapsed process header survives a live-window trim by its remaining message identities',async()=>{
 const {ctx,elements,run}=setup();expandedProcess(run);
 const group=elements['session-history'].toolGroups[0];group.open=false;ctx.group=group;run('rememberHistoryToolGroup(group)');
 const {pane,list}=processReadingLayout(elements);pane.scrollTop=110;
 const before=group.firstElementChild.getBoundingClientRect().top;
 ctx.apiFetch=async()=>({ok:true,json:async()=>({ready:true,messages:[{id:'t2',role:'tool',text:'Second output'},{id:'t3',role:'tool',text:'Third output'},{id:'answer',role:'assistant',text:'Final answer'}],next_cursor:null})});
 await run('loadHistory(true,true)');
 assert.equal(list.toolGroups[0],group);assert.equal(group.open,false);
 assert.equal(group.firstElementChild.getBoundingClientRect().top,before);
 assert.equal(group.firstElementChild.innerHTML,'工具调用 · 2');
 assert.equal(run("historyState('a').updated"),false);
});
function initialMessages(run) {
 run(`Object.assign(historyState('a'),{messages:['a','b','c','d','e'].map(id=>({id,role:'assistant',text:id})),loaded:true,next:'cursor'});renderHistory()`);
}
test('explicit older pagination at the top preserves the first visible message',async()=>{
 const {ctx,elements,run}=setup();initialMessages(run);
 const {pane,list}=readingLayout(elements);pane.scrollTop=0;
 const firstVisible=list.firstElementChild,before=firstVisible.getBoundingClientRect().top;
 ctx.apiFetch=async()=>({ok:true,json:async()=>({ready:true,messages:[{id:'older',role:'assistant',text:'older'}],next_cursor:null})});
 await run('loadHistory(false)');
 assert.equal(list.children[1],firstVisible);
 assert.equal(firstVisible.getBoundingClientRect().top,before);
 assert.equal(pane.scrollTop,100);
});
test('automatic history replacement at the top keeps the header visible',async()=>{
 const {ctx,elements,run}=setup();initialMessages(run);
 const {pane,list}=readingLayout(elements);pane.scrollTop=0;
 const messages=['new','a','b','c','d','e'].map(id=>({id,role:'assistant',text:id}));
 ctx.apiFetch=async()=>({ok:true,json:async()=>({ready:true,messages,next_cursor:null})});
 await run('loadHistory(true,true)');
 assert.equal(list.firstElementChild.dataset.historyId,'new');assert.equal(pane.scrollTop,0);
});
test('a delayed older page anchors the message visible at arrival, preserving its DOM node',async()=>{
 const {ctx,elements,run}=setup();initialMessages(run);
 const {pane,list}=readingLayout(elements);
 pane.scrollTop=0;
 const reading=list.children[2];
 let respond;ctx.apiFetch=()=>new Promise(resolve=>respond=resolve);
 const loading=run('loadHistory(false)');
 pane.scrollTop=325; // User scrolled from the top to the third message while waiting.
 const before=reading.getBoundingClientRect().top;
 respond({ok:true,json:async()=>({ready:true,messages:[{id:'older',role:'assistant',text:'older'}],next_cursor:null})});
 await loading;
 assert.equal(list.children[3],reading);assert.equal(reading.getBoundingClientRect().top,before);
 assert.equal(pane.scrollTop,425);
});
test('live refresh retains a page when the visible message has left the latest native window',async()=>{
 const {ctx,elements,run}=setup();initialMessages(run);
 const {pane,list}=readingLayout(elements);pane.scrollTop=250;
 const reading=list.children[1];
 ctx.apiFetch=async()=>({ok:true,json:async()=>({ready:true,messages:[{id:'latest',role:'assistant',text:'latest'}],next_cursor:null})});
 await run('loadHistory(true,true)');
 assert.equal(list.children[1],reading);assert.equal(pane.scrollTop,250);
 assert.match(elements['session-history'].innerHTML,/有新内容/);
 ctx.readerPane=pane;
 await run('showNewHistory()');
 assert.equal(list.children[0].dataset.historyId,'latest');assert.equal(run("historyState('a').updated"),false);
});
test('typing in the fixed composer preserves its focus and the visible transcript row during a refresh',async()=>{
 const {ctx,elements,run}=setup();initialMessages(run);
 const {pane,list}=readingLayout(elements);pane.scrollTop=325;
 const reading=list.children[2],before=reading.getBoundingClientRect().top;
 const input=new HistoryElement('textarea');elements['session-control'].insertBefore(input,null);ctx.document.activeElement=input;
 input.value='Unsent reply';input.selectionStart=3;
 input.getBoundingClientRect=()=>({top:205,bottom:250});
 const messages=['new','a','b','c','d','e'].map(id=>({id,role:'assistant',text:id}));
 ctx.apiFetch=async()=>({ok:true,json:async()=>({ready:true,messages,next_cursor:null})});
 await run('loadHistory(true,true)');
 assert.equal(ctx.document.activeElement,input);assert.equal(input.selectionStart,3);assert.equal(input.value,'Unsent reply');
 assert.equal(reading.getBoundingClientRect().top,before);assert.equal(pane.scrollTop,425);
});

test('all harnesses page by collapsed visible entries and cross pure-process pages on one older click',async()=>{
 for(const tool of ['codex','antigravity','dsh']) {
  const {ctx,elements,run}=setup();run(`workspace.tasks[0].tool=${JSON.stringify(tool)};
   Object.assign(historyState('a'),{loaded:true,next:'p1',messages:[{id:'t90',role:'tool',text:'Last process'},{id:'a90',role:'assistant',text:'Current result'}]});renderHistory()`);
  const group=elements['session-history'].toolGroups[0];
  const process=(start,end)=>Array.from({length:end-start+1},(_,i)=>({id:'t'+(start+i),role:(start+i)%2?'tool':'assistant',phase:(start+i)%2?undefined:'commentary',text:'Process '+(start+i)}));
  const body=Array.from({length:20},(_,i)=>({id:'body'+i,role:i%2?'assistant':'user',text:'Earlier conversation '+i}));
  const pages={p1:{messages:process(60,90),next_cursor:'p2'},p2:{messages:process(30,60),next_cursor:'p3'},p3:{messages:body,next_cursor:'still-older'}};
  const requests=[];ctx.apiFetch=async url=>{const cursor=new URL('http://example.test'+url).searchParams.get('cursor');requests.push(cursor);return {ok:true,json:async()=>({ready:true,...pages[cursor]})};};
  await run('loadHistory(false)');
  assert.deepEqual(requests,['p1','p2','p3'],tool);
  assert.equal(run("historyState('a').messages.length"),82,tool);
  assert.equal(run("new Set(historyState('a').messages.map(m=>m.id)).size"),82,tool);
  assert.equal(run("historyState('a').next"),'still-older',tool);
  assert.equal(run("historyVisibleItems(historyState('a').messages)"),22,tool);
  assert.equal(elements['session-history'].toolGroups[0],group,tool);
  assert.equal(group.open,false,tool);assert.equal(group.historyProcessIds.length,61,tool);
 }
});
test('initial history uses the same visible budget and retains complete tool detail',async()=>{
 const {ctx,elements,run}=setup();let count=0;
 ctx.apiFetch=async()=>({ok:true,json:async()=>{
  count++;
  if(count<3)return {ready:true,messages:Array.from({length:30},(_,i)=>({id:`t${count}-${i}`,role:'tool',text:'Retained output'})),next_cursor:'p'+count};
  return {ready:true,messages:Array.from({length:20},(_,i)=>({id:'m'+i,role:i%2?'assistant':'user',text:'Readable '+i})),next_cursor:'remaining'};
 }});
 await run('loadHistory(true)');
 assert.equal(count,3);assert.equal(run("historyState('a').messages.length"),80);
 assert.equal(elements['session-history'].toolGroups.length,1);
 assert.equal(elements['session-history'].tools.length,60);
 assert.equal(elements['session-history'].toolGroups[0].open,false);
});
test('a leading process boundary is finished even after the visible item budget is reached',async()=>{
 const {ctx,run}=setup();let count=0;
 ctx.apiFetch=async()=>({ok:true,json:async()=>++count===1?{ready:true,messages:[
  {id:'leading',role:'tool',text:'Earlier process'},...Array.from({length:20},(_,i)=>({id:'m'+i,role:'assistant',text:'Result '+i}))],next_cursor:'boundary'}:
  {ready:true,messages:[{id:'request',role:'user',text:'Original request'}],next_cursor:'remaining'}});
 await run('loadHistory(true)');assert.equal(count,2);
 assert.equal(run("historyState('a').messages[0].id"),'request');
 assert.equal(run("historyState('a').next"),'remaining');
});
test('expanded process records never change the default collapsed pagination budget',async()=>{
 const {ctx,elements,run}=setup();run(`Object.assign(historyState('a'),{loaded:true,next:'p1',messages:[{id:'t90',role:'tool',text:'Output'},{id:'answer',role:'assistant',text:'Answer'}]});renderHistory()`);
 const group=elements['session-history'].toolGroups[0],record=elements['session-history'].tools[0];group.open=true;record.open=true;
 let count=0;ctx.apiFetch=async()=>({ok:true,json:async()=>++count===1?{ready:true,messages:[{id:'t89',role:'tool',text:'Previous output'}],next_cursor:'p2'}:
  {ready:true,messages:[{id:'question',role:'user',text:'Earlier question'}],next_cursor:null}});
 await run('loadHistory(false)');assert.equal(count,2);
 assert.equal(elements['session-history'].toolGroups[0],group);assert.equal(group.open,true);assert.equal(record.open,true);
});
test('filtered empty pages advance safely and repeated cursors stop without an infinite read',async()=>{
 const {ctx,elements,run}=setup();let count=0;
 ctx.apiFetch=async()=>({ok:true,json:async()=>{count++;return count===1?{ready:true,messages:[],next_cursor:'empty'}:
  {ready:true,messages:[{id:'m',role:'user',text:'Keep this record'}],next_cursor:'empty'};}});
 await run('loadHistory(true)');assert.equal(count,2);
 assert.equal(run("historyState('a').messages[0].id"),'m');
 assert.equal(run("historyState('a').error"),'history_no_progress');
 assert.equal(run("historyState('a').retryOlder"),false);
 assert.match(elements['session-history'].innerHTML,/历史分页未能继续向前/);
 assert.doesNotMatch(elements['session-history'].innerHTML,/onclick="loadHistory\(false\)"/);
});
test('long process-only history stops at the page bound and resumes from the retained cursor',async()=>{
 const {ctx,elements,run}=setup();let count=0;
 ctx.apiFetch=async()=>({ok:true,json:async()=>{count++;return {ready:true,messages:[{id:'t'+count,role:'tool',text:'Output'}],next_cursor:'p'+count};}});
 await run('loadHistory(true)');assert.equal(count,16);
 assert.equal(run("historyState('a').partial"),true);assert.equal(run("historyState('a').next"),'p16');
 assert.equal(run("historyState('a').messages.length"),16);
 assert.match(elements['session-history'].innerHTML,/继续读取更早对话/);
 const requested=[];ctx.apiFetch=async url=>{requested.push(url);return {ok:true,json:async()=>({ready:true,messages:[{id:'old-user',role:'user',text:'Start'}],next_cursor:null})};};
 await run('loadHistory(false)');assert.match(requested[0],/cursor=p16/);
 assert.equal(run("historyState('a').messages.length"),17);assert.equal(run("historyState('a').partial"),false);
});
test('a byte-limited batch keeps the first unread cursor and never drops the unaccepted page',async()=>{
 const {ctx,run}=setup();let count=0;
 const big=Array.from({length:30},(_,i)=>({id:'large'+i,role:'tool',text:'x'.repeat(42000)}));
 ctx.apiFetch=async()=>({ok:true,json:async()=>++count===1?{ready:true,messages:big,next_cursor:'unread'}:
  {ready:true,messages:big.map(m=>({...m,id:'older-'+m.id})),next_cursor:'too-far'}});
 await run('loadHistory(true)');assert.equal(count,2);
 assert.equal(run("historyState('a').messages.length"),30);
 assert.equal(run("historyState('a').next"),'unread');assert.equal(run("historyState('a').partial"),true);
});
test('the total action deadline bounds automatic page traversal and retains successful pages',async()=>{
 const {ctx,run}=setup();let now=0,calls=0;ctx.Date={now:()=>now};
 ctx.apiFetch=async()=>({ok:true,json:async()=>{now=31000;calls++;return {ready:true,messages:[{id:'t',role:'tool',text:'Output'}],next_cursor:'resume'};}});
 await run('loadHistory(true)');assert.equal(calls,1);
 assert.equal(run("historyState('a').partial"),true);assert.equal(run("historyState('a').next"),'resume');
});
test('a later page failure keeps earlier pages and retries the failed cursor instead of resetting history',async()=>{
 const {ctx,elements,run}=setup();run(`Object.assign(historyState('a'),{loaded:true,next:'p1',messages:[{id:'answer',role:'assistant',text:'Answer'}]})`);
 const requests=[];ctx.apiFetch=async url=>{const cursor=new URL('http://example.test'+url).searchParams.get('cursor');requests.push(cursor);return cursor==='p1'?{ok:true,json:async()=>({ready:true,messages:[{id:'t',role:'tool',text:'Output'}],next_cursor:'p2'})}:{ok:false,json:async()=>({error:'history_timeout'})};};
 await run('loadHistory(false)');assert.deepEqual(requests,['p1','p2']);
 assert.equal(run("historyState('a').messages.map(m=>m.id).join(',')"),'t,answer');assert.equal(run("historyState('a').next"),'p2');
 assert.equal(run("historyState('a').retryOlder"),true);assert.match(elements['session-history'].innerHTML,/onclick="loadHistory\(false\)">继续读取/);
 ctx.apiFetch=async url=>{assert.match(url,/cursor=p2/);return {ok:true,json:async()=>({ready:true,messages:[{id:'request',role:'user',text:'Question'}],next_cursor:null})};};
 await run('loadHistory(false)');assert.equal(run("historyState('a').messages.map(m=>m.id).join(',')"),'request,t,answer');assert.equal(run("historyState('a').error"),'');
});
test('switching conversations stops automatic continuation without overwriting the other view',async()=>{
 const {ctx,elements,run}=setup();let requests=0,release;
 ctx.apiFetch=()=>{requests++;return new Promise(resolve=>release=resolve);};
 const loading=run('loadHistory(true)');run("workspace.selected='b'");elements['session-history'].innerHTML='Other conversation';
 release({ok:true,json:async()=>({ready:true,messages:[{id:'t',role:'tool',text:'Output'}],next_cursor:'never-fetch'})});await loading;
 assert.equal(requests,1);assert.equal(elements['session-history'].innerHTML,'Other conversation');
 assert.equal(run("historyState('a').messages.length"),1);assert.equal(run("historyState('a').busy"),false);
});

test('the action deadline aborts a hung fetch and retains successful pages for retry',async()=>{
 const {ctx,run}=setup();let deadline,calls=0,waiting;
 ctx.setTimeout=(fn,delay)=>{assert.equal(delay,30000);deadline=fn;return 7;};
 const cleared=[];ctx.clearTimeout=id=>cleared.push(id);
 ctx.apiFetch=async(url,options)=>{
  if(++calls===1)return {ok:true,json:async()=>({ready:true,messages:[{id:'t',role:'tool',text:'Output'}],next_cursor:'hung'})};
  return new Promise((resolve,reject)=>{waiting=true;options.signal.addEventListener('abort',()=>reject(new Error('Aborted')),{once:true});});
 };
 const loading=run('loadHistory(true)');while(!waiting)await new Promise(resolve=>setImmediate(resolve));deadline();await loading;
 assert.equal(calls,2);assert.equal(run("historyState('a').messages.length"),1);
 assert.equal(run("historyState('a').next"),'hung');assert.equal(run("historyState('a').retryOlder"),true);
 assert.equal(run("historyState('a').error"),'history_timeout');assert.deepEqual(cleared,[7]);
});

test('navigation during a queued-read polling delay does not send another GET',async()=>{
 const {ctx,run}=setup();let poll,calls=0;
 ctx.setTimeout=(fn,delay)=>delay===750?(poll=fn,7):setTimeout(fn,delay);
 ctx.apiFetch=async()=>{calls++;return {ok:true,json:async()=>({ready:false})};};
 const loading=run('loadHistory(true)');while(!poll)await new Promise(resolve=>setImmediate(resolve));
 run("workspace.selected='b'");poll();await loading;
 assert.equal(calls,1);assert.equal(run("historyState('a').busy"),false);
});
