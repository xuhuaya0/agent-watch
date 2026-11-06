const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const crypto=require('node:crypto').webcrypto;
function setup(){
 const input={value:''},pane={dataset:{},querySelector(){return null},querySelectorAll(){return []}};
 const historyRoot={dataset:{},isConnected:true,hidden:false,lastElementChild:{children:[]}};
 const ctx=vm.createContext({crypto,console,Date,historyRoot,setInterval(){return 1},clearInterval(){},
  document:{getElementById(id){return id==='session-history'?historyRoot:id==='native-reply'?input:id==='session-control'?pane:null}},
  workspace:{selected:'chat',page:'sessions'},histories:new Map(),
  esc:value=>String(value??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;'),
  jsArg:value=>JSON.stringify(value),toast(){},showAuthView(){},harnessName:()=> 'Harness',settingsSummaryHtml:()=>''});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/control.js'),'utf8'),ctx);
 vm.runInContext('renderInteraction=()=>{}',ctx);
 ctx.data={connected:true,capability:{tool:'codex',reply:true,requests:[],delivery:{state:'running',steer:true,revision:'native-turn'}},commands:[]};
 const run=code=>vm.runInContext(code,ctx);
 run('interaction.byTask.set("chat",data)');
 ctx.fetch=async()=>({ok:true,json:async()=>ctx.data});
 return {ctx,input,run};
}
function mountHistory(ctx){
 const messages=ctx.histories.get('chat').messages;
 ctx.historyRoot.dataset.session='chat';
 ctx.historyRoot.lastElementChild={historyMessages:messages,children:messages.map(historyMessage=>({historyMessage,isConnected:true,hidden:false}))};
}

test('running Send persists one wait, clears accepted draft, and accepts a second distinct queue item',async()=>{
 const {ctx,run}=setup();const posts=[];let finish;
 ctx.apiFetch=(url,options)=>{posts.push(JSON.parse(options.body));return new Promise(resolve=>{finish=resolve})};
 run('setReplyDraft("First queued message")');
 const first=run('sendReply()');run('sendReply()');
 assert.equal(posts.length,1);assert.equal(posts[0].action.mode,'after-turn');
 const command={id:posts[0].id,state:'waiting',kind:'reply',text:posts[0].action.text,created:10};
 ctx.data.commands=[command];finish({ok:true,json:async()=>({command})});await first;
 assert.equal(run('interactionDraft("chat").text'),'');
 assert.equal(run('interactionBusy("chat")'),false);
 assert.equal(run('outgoingHtml("chat")'),'');
 assert.match(run('replyQueueHtml("chat")'),/First queued message/);
 run('setReplyDraft("Second queued message")');const second=run('sendReply()');
 assert.equal(posts.length,2);assert.notEqual(posts[0].id,posts[1].id);
 const secondCommand={id:posts[1].id,state:'waiting',kind:'reply',text:posts[1].action.text,created:11};
 ctx.data.commands=[secondCommand,command];finish({ok:true,json:async()=>({command:secondCommand})});await second;
 const html=run('replyQueueHtml("chat")');
 assert.ok(html.indexOf('First queued message')<html.indexOf('Second queued message'));
});

test('queue insertion mutates the same ID only once while a click is pending',async()=>{
 const {ctx,run}=setup();const requests=[];let finish;
 ctx.data.commands=[{id:'original',kind:'reply',state:'waiting',text:'Hello'}];
 ctx.apiFetch=(url,options)=>{requests.push({method:options.method,data:JSON.parse(options.body)});return new Promise(resolve=>{finish=resolve})};
 const first=run('manageQueuedReply("original","steer","native-turn")');
 run('manageQueuedReply("original","steer","native-turn")');
 assert.equal(requests.length,1);
 assert.deepEqual(requests[0],{method:'PATCH',data:{id:'original',operation:'steer',revision:'native-turn'}});
 ctx.data.commands=[{id:'original',kind:'reply',state:'dispatched'}];finish({ok:true,json:async()=>({ok:true})});await first;
 assert.equal(run('replyQueueHtml("chat")'),'');
});

test('unsupported insertion stays absent but cancellation remains available while offline',()=>{
 const {ctx,run}=setup();ctx.data.connected=false;ctx.data.capability.delivery={state:'unknown',steer:false};
 ctx.data.commands=[{id:'queued',kind:'reply',state:'waiting',text:'<img src=x>'}];
 const html=run('replyQueueHtml("chat")');
 assert.doesNotMatch(html,/立即插入|<img/);assert.match(html,/取消/);assert.match(html,/&lt;img/);
 assert.equal(run('interactionBusy("chat")'),false);
});

test('queued work does not lock model or permission controls and pending dispatch still does',()=>{
 const {ctx,run}=setup();ctx.data.commands=[{id:'one',kind:'reply',state:'waiting'}];
 assert.equal(run('interactionBusy("chat")'),false);
 ctx.data.commands.push({id:'two',kind:'model',state:'dispatched'});
 assert.equal(run('interactionBusy("chat")'),true);
});

test('outgoing echo reconciles a new native message despite browser clock skew',()=>{
 const {ctx,run}=setup();
 ctx.histories.set('chat',{loaded:true,messages:[{id:'old',role:'user',text:'Same reply',timestamp:'2026-01-01T10:00:00Z'},
  {id:'new',role:'user',text:'Same reply\n',timestamp:'2026-01-01T10:00:04Z'}]});
 mountHistory(ctx);
 run(`interaction.outgoing.set('chat',{id:'command',text:'Same reply',state:'delivered',createdAt:Date.parse('2026-01-01T11:00:00Z'),previousHistoryIds:['old'],historyBaselineAt:Date.parse('2026-01-01T10:00:00Z')})`);
 assert.equal(run('outgoingHtml("chat")'),'');
 assert.equal(run('historyContainsOutgoing("chat",interaction.outgoing.get("chat"))'),true);
});

test('an old identical user message or unconfirmed dispatch cannot hide the local reply',()=>{
 const {ctx,run}=setup();ctx.histories.set('chat',{loaded:true,messages:[{id:'old',role:'user',text:'Same reply',timestamp:'2026-01-01T10:00:00Z'}]});
 mountHistory(ctx);
 run(`interaction.outgoing.set('chat',{id:'command',text:'Same reply',state:'delivered',createdAt:Date.parse('2026-01-01T10:01:00Z'),previousHistoryIds:['old'],historyBaselineAt:Date.parse('2026-01-01T10:00:00Z')})`);
 assert.match(run('outgoingHtml("chat")'),/local-outgoing/);
 ctx.histories.get('chat').messages.push({id:'new',role:'user',text:'Same reply',timestamp:'2026-01-01T10:01:02Z'});
 mountHistory(ctx);
 run('interaction.outgoing.get("chat").state="unknown"');
 assert.match(run('outgoingHtml("chat")'),/local-outgoing/);
});

test('composer labels running submission as queue for all Harnesses',()=>{
 const {ctx,run}=setup();
 for(const tool of ['codex','antigravity','dsh']){
  ctx.data.capability.tool=tool;
  assert.match(run('interactionHtml("chat")'),/>排队<\/span>/);
 }
 ctx.data.capability.delivery={state:'idle',steer:false};
 assert.match(run('interactionHtml("chat")'),/>发送<\/span>/);
});

test('receipt feedback does not repeat identical waiting or cancellation labels',()=>{
 const {run}=setup();
 assert.equal(run('receiptDetail({state:"cancelled",detail:"已取消，未发送"})'),'');
 assert.equal(run('receiptDetail({state:"waiting",detail:"等待当前运行结束"})'),'');
 assert.equal(run('receiptDetail({state:"cancelled",detail:"设备控制连接已撤销"})'),'设备控制连接已撤销');
 assert.equal(run('receiptDetail({state:"rejected",detail:"native_connection_changed"})'),'原生会话连接已变化，消息未发送；请核对原会话后重新提交');
});
