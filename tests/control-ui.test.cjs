const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const crypto=require('node:crypto').webcrypto;
function setup(){
 const feedback={innerHTML:''},outgoing={innerHTML:''},button={disabled:false},input={value:''},timers=new Map();let timerId=0;
 const node={dataset:{},innerHTML:'',contains(){return false},querySelector(selector){return selector==='.outgoing-message'?outgoing:selector==='.local-outgoing'?{scrollIntoView(){}}:feedback},querySelectorAll(){return [button]}};
 const ctx=vm.createContext({console,crypto,setInterval(fn,ms){timers.set(++timerId,{fn,ms});return timerId},clearInterval(id){timers.delete(id)},document:{getElementById(id){return id==='native-reply'?input:node},activeElement:null},workspace:{selected:'a',page:'sessions'},esc:s=>String(s??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;'),jsArg:s=>JSON.stringify(s),markdown:s=>String(s).replaceAll('<','&lt;'),toast(){},showAuthView(){}});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/control.js'),'utf8'),ctx);return {ctx,node,feedback,outgoing,button,input,timers};
}
function run(ctx,s){return vm.runInContext(s,ctx)}
const cap={connected:true,capability:{reply:true,requests:[]},commands:[]};
test('late device responses cannot restore stale pause controls',async()=>{
 const {ctx,node}=setup();let first;
 const device={name:'<img src=x>',device_id:'pc',configured:true,enabled:true,connected:true,sessions:1,harnesses:{antigravity:1}};
 ctx.apiFetch=()=>new Promise(resolve=>first=resolve);
 const stale=run(ctx,'loadControlDevices()');
 ctx.apiFetch=async()=>({ok:true,json:async()=>({devices:[{...device,enabled:false}]})});
 await run(ctx,'loadControlDevices()');
 assert.match(node.innerHTML,/恢复交互/);assert.doesNotMatch(node.innerHTML,/<img/);
 first({ok:true,json:async()=>({devices:[device]})});await stale;
 assert.match(node.innerHTML,/恢复交互/);assert.doesNotMatch(node.innerHTML,/暂停交互<\/button>/);
});
test('failed pause does not report success or leave its button disabled',async()=>{
 const {ctx,button}=setup();const messages=[];ctx.toast=message=>messages.push(message);
 ctx.button=button;ctx.apiFetch=async()=>({ok:false,status:403});
 await run(ctx,'setControlEnabled("pc",false,button)');
 assert.equal(button.disabled,false);assert.deepEqual(messages,['未能更新交互设置，请刷新状态确认']);
});
test('reply drafts survive native polls and selection changes',()=>{
 const {ctx}=setup();ctx.data=cap;run(ctx,'interaction.byTask.set("a",data);setReplyDraft("My unsent reply");workspace.selected="b";setReplyDraft("Other");workspace.selected="a"');
 assert.match(run(ctx,'interactionHtml("a")'),/My unsent reply/);assert.doesNotMatch(run(ctx,'interactionHtml("a")'),/>Other</);
});
test('no unconnected composer or synthetic approval buttons',()=>{
 const {ctx}=setup();ctx.data={connected:false,reason:'Not connected',commands:[]};run(ctx,'interaction.byTask.set("a",data)');
 const html=run(ctx,'interactionHtml("a")');assert.match(html,/Not connected/);assert.doesNotMatch(html,/textarea|sendApproval|发送/);
});
test('native option and reason markup is escaped',()=>{
 const {ctx}=setup();ctx.data={...cap,capability:{reply:false,requests:[{id:'r',revision:'v',kind:'question',prompt:'<img src=x>',questions:[{id:'q',question:'<script>',options:['<svg>'],custom:true}]}]}};
 run(ctx,'interaction.byTask.set("a",data)');const html=run(ctx,'interactionHtml("a")');assert.doesNotMatch(html,/<img|<script|<svg/);assert.match(html,/&lt;script>/);
});
test('one-shot approvals contain exact native id and revision',async()=>{
 const {ctx}=setup();ctx.data={...cap,capability:{reply:false,requests:[{id:'r',revision:'v7',kind:'approval',prompt:'Command',choices:['approve','reject']}]}};
 run(ctx,'interaction.byTask.set("a",data)');let body;
 ctx.apiFetch=async(u,o)=>{body=JSON.parse(o.body);return {ok:true,status:200,json:async()=>({command:{id:body.id,state:'queued'}})}};
 ctx.fetch=async()=>({ok:true,status:200,json:async()=>({...ctx.data,commands:[{id:body.id,state:'queued'}]})});
 await run(ctx,'sendApproval("r","reject","v7")');assert.deepEqual(body.action,{kind:'approval',request_id:'r',revision:'v7',decision:'reject'});
 assert.match(body.id,/^[a-f0-9-]{36}$/);assert.match(run(ctx,'interactionHtml("a")'),/disabled/);
});
test('network uncertainty keeps draft and never auto-resends',async()=>{
 const {ctx}=setup();ctx.data=cap;run(ctx,'interaction.byTask.set("a",data);setReplyDraft("hello")');let calls=0;
 ctx.apiFetch=async()=>{calls++;throw new Error('network')};ctx.fetch=async()=>{throw new Error('offline')};
 await run(ctx,'sendReply()');await run(ctx,'sendReply()');assert.equal(calls,1);
 assert.equal(run(ctx,'interactionDraft("a").text'),'hello');assert.match(run(ctx,'interactionHtml("a")'),/未收到服务器回执/);
});
test('delivered receipt clears only the original draft, not a newer edit',async()=>{
 const {ctx}=setup();ctx.data={...cap,commands:[{id:'c',state:'delivered'}]};ctx.fetch=async()=>({ok:true,status:200,json:async()=>ctx.data});
 run(ctx,'interaction.pending.set("a",{id:"c",action:{kind:"reply",text:"old"}});setReplyDraft("new edit")');
 await run(ctx,'refreshInteraction()');assert.equal(run(ctx,'interactionDraft("a").text'),'new edit');
 run(ctx,'interaction.pending.set("a",{id:"c",action:{kind:"reply",text:"new edit"}})');await run(ctx,'refreshInteraction()');assert.equal(run(ctx,'interactionDraft("a").text'),'');
});
test('a stale visible approval cannot approve a changed native request',async()=>{
 const {ctx}=setup();ctx.data={...cap,capability:{reply:false,requests:[{id:'r',revision:'new',kind:'approval',prompt:'Changed command',choices:['approve','reject']}]}};
 run(ctx,'interaction.byTask.set("a",data)');let calls=0;ctx.apiFetch=async()=>{calls++};
 await run(ctx,'sendApproval("r","approve","old")');assert.equal(calls,0);
});
test('sending feedback is immediate and does not show the previous receipt',async()=>{
 const {ctx,node}=setup();ctx.data={...cap,commands:[{id:'old',state:'delivered'}]};
 run(ctx,'interaction.byTask.set("a",data);setReplyDraft("hello")');let release;
 ctx.apiFetch=()=>new Promise(resolve=>release=resolve);
 ctx.fetch=async()=>({ok:true,status:200,json:async()=>cap});
 const sending=run(ctx,'sendReply()');assert.match(node.innerHTML,/正在发送/);assert.doesNotMatch(node.innerHTML,/原应用已接收/);
 release({ok:false,status:409,json:async()=>({error:'control_offline'})});await sending;
});
test('focused composer updates receipts and controls without replacing newer edits',async()=>{
 const {ctx,node,feedback,button,input}=setup();ctx.data={...cap,commands:[{id:'c',state:'queued'}]};
 run(ctx,'interaction.byTask.set("a",data);interaction.pending.set("a",{id:"c",action:{kind:"reply",text:"sent"}});setReplyDraft("new edit");renderInteraction(true)');
 node.contains=()=>true;input.value='new edit';const original=node.innerHTML;
 ctx.data={...cap,commands:[{id:'c',state:'dispatched'}]};run(ctx,'interaction.byTask.set("a",data);renderInteraction()');assert.match(feedback.innerHTML,/正在交给原应用/);assert.equal(button.disabled,true);
 ctx.data={...cap,commands:[{id:'c',state:'delivered'}]};ctx.fetch=async()=>({ok:true,status:200,json:async()=>ctx.data});
 await run(ctx,'refreshInteraction()');assert.match(feedback.innerHTML,/原应用已接收/);assert.equal(button.disabled,false);
 assert.equal(node.innerHTML,original);assert.equal(input.value,'new edit');assert.equal(run(ctx,'interactionDraft("a").text'),'new edit');
});
test('fast receipt polling stops after delivery and never resubmits',async()=>{
 const {ctx,timers,input}=setup();ctx.data=cap;run(ctx,'interaction.byTask.set("a",data);setReplyDraft("hello")');input.value='hello';let posts=0,command;
 ctx.apiFetch=async(u,o)=>{posts++;command={id:JSON.parse(o.body).id,state:'queued'};return {ok:true,status:200,json:async()=>({command})}};
 ctx.fetch=async()=>({ok:true,status:200,json:async()=>({...cap,commands:[command]})});
 await run(ctx,'sendReply()');assert.equal(timers.size,1);const timer=[...timers.values()][0];assert.equal(timer.ms,500);
 command={...command,state:'delivered'};await timer.fn();await timer.fn();assert.equal(timers.size,0);assert.equal(posts,1);assert.equal(input.value,'');
});
test('fast polling stops when navigating away from the session',async()=>{
 const {ctx,timers}=setup();run(ctx,'interaction.pending.set("a",{id:"c"});watchInteraction("a");workspace.page="projects"');
 await [...timers.values()][0].fn();assert.equal(timers.size,0);
});
test('message body appears before the HTTP response and remains on failed delivery',async()=>{
 const {ctx,node}=setup();ctx.data=cap;ctx.message='<img src=x>\nMy message';run(ctx,'interaction.byTask.set("a",data);setReplyDraft(message)');let release;
 ctx.apiFetch=()=>new Promise(resolve=>release=resolve);ctx.fetch=async()=>({ok:true,status:200,json:async()=>cap});
 const sending=run(ctx,'sendReply()');assert.match(node.innerHTML,/local-outgoing/);assert.match(node.innerHTML,/&lt;img src=x>\nMy message/);assert.doesNotMatch(node.innerHTML,/<img/);
 assert.match(run(ctx,'outgoingHtml("a")'),/发送中/);assert.equal(run(ctx,'outgoingHtml("b")'),'');
 release({ok:false,status:409,json:async()=>({error:'control_offline'})});await sending;
 assert.match(run(ctx,'outgoingHtml("a")'),/发送失败/);assert.match(run(ctx,'outgoingHtml("a")'),/My message/);
 assert.equal(run(ctx,'interactionDraft("a").text'),'<img src=x>\nMy message');
});
test('uncertain delivery keeps the visible message without claiming success',async()=>{
 const {ctx}=setup();ctx.data=cap;run(ctx,'interaction.byTask.set("a",data);setReplyDraft("Visible even offline")');
 ctx.apiFetch=async()=>{throw Error('offline')};ctx.fetch=async()=>{throw Error('offline')};await run(ctx,'sendReply()');
 const html=run(ctx,'outgoingHtml("a")');assert.match(html,/Visible even offline/);assert.match(html,/送达情况待确认/);assert.doesNotMatch(html,/已送达/);
});
test('newer matching native capture replaces the delivered echo, not an old identical message',()=>{
 const {ctx}=setup();run(ctx,'workspace.detail={key:"a",activity_at:10,extra:{latest_turn:{notification:"Same message"}}};interaction.outgoing.set("a",{id:"c",text:"Same message",createdAt:1000,baselineAt:10,state:"delivered"})');
 assert.match(run(ctx,'outgoingHtml("a")'),/Same message/);
 run(ctx,'workspace.detail.activity_at=11');assert.equal(run(ctx,'outgoingHtml("a")'),'');
 run(ctx,'workspace.detail.extra.latest_turn.notification="Different message"');assert.match(run(ctx,'outgoingHtml("a")'),/Same message/);
 run(ctx,'workspace.detail={key:"b",activity_at:12,extra:{latest_turn:{notification:"Same message"}}}');assert.match(run(ctx,'outgoingHtml("a")'),/Same message/);
});

test('resolved question leaves no stale controls even while its form has focus',async()=>{
 const {ctx,node}=setup();ctx.data={...cap,capability:{reply:false,requests:[{id:'r',revision:'v',kind:'question',prompt:'Question',questions:[{id:'q',question:'Color?',options:['Blue'],custom:true}]}]}};
 run(ctx,'interaction.byTask.set("a",data);setReplyDraft("Saved next message");renderInteraction(true)');
 assert.match(node.innerHTML,/Color\?/);node.contains=()=>true;
 ctx.data=cap;ctx.fetch=async()=>({ok:true,status:200,json:async()=>ctx.data});
 await run(ctx,'refreshInteraction()');
 assert.doesNotMatch(node.innerHTML,/sendAnswer|Color\?/);assert.match(node.innerHTML,/Saved next message/);
});
test('creation shows device directory and submits a distinct action with revision',async()=>{
 const {ctx}=setup();ctx.data={...cap,capability:{...cap.capability,create:{cwd:'C:/Example/<project>',revision:'v1'}}};
 run(ctx,'interaction.byTask.set("a",data);interactionDraft("a").createText="New chat"');
 const html=run(ctx,'interactionHtml("a")');assert.match(html,/新建并发送/);assert.match(html,/&lt;project>/);assert.doesNotMatch(html,/<project>/);
 let body;ctx.apiFetch=async(u,o)=>{body=JSON.parse(o.body);return {ok:true,json:async()=>({command:{id:body.id,state:'queued'}})}};
 ctx.fetch=async()=>({ok:true,json:async()=>({...ctx.data,commands:[{id:body.id,state:'queued'}]})});
 await run(ctx,'sendCreate("v1")');assert.deepEqual(body.action,{kind:'create',revision:'v1',text:'New chat'});
 assert.equal(run(ctx,'interaction.outgoing.has("a")'),false);
});
test('policy only exposes native choices and rejects a changed revision',async()=>{
 const {ctx}=setup();ctx.data={...cap,capability:{...cap.capability,policy:{current:'on-request',revision:'v2',scope:'session-next-turn',options:[{value:'on-request',label:'Ask'},{value:'never',label:'Reject required approvals'}]}}};
 run(ctx,'interaction.byTask.set("a",data)');assert.match(run(ctx,'interactionHtml("a")'),/后续轮次/);
 let calls=0;ctx.apiFetch=async()=>{calls++};ctx.form={elements:{policy:{value:'never'}}};
 await run(ctx,'sendPolicy(form,"v1")');assert.equal(calls,0);
 ctx.form.elements.policy.value='full-access';await run(ctx,'sendPolicy(form,"v2")');assert.equal(calls,0);
});
test('new conversation links never cross devices',()=>{
 const {ctx}=setup();run(ctx,`workspace.tasks=[{ui_key:'a',machine_id:'one'},{ui_key:'other',machine_id:'two',tool:'dsh',session_id:'new'}]`);
 const receipt={result:{tool:'dsh',session_id:'new'}};ctx.receipt=receipt;
 assert.doesNotMatch(run(ctx,'createdSessionHtml("a",receipt)'),/selectSession/);
 run(ctx,`workspace.tasks.push({ui_key:'new-chat',machine_id:'one',tool:'dsh',session_id:'new'})`);
 assert.match(run(ctx,'createdSessionHtml("a",receipt)'),/打开新会话/);
});

test('DeepSeek presets disclose sandbox changes and keep custom display-only',()=>{
 const {ctx}=setup();ctx.data={...cap,capability:{...cap.capability,policy:{current:'custom',revision:'preset-v1',scope:'session-permissions',options:[{value:'workspace-write',label:'Workspace',description:'<native description>'}]}}};
 run(ctx,'interaction.byTask.set("a",data)');const html=run(ctx,'interactionHtml("a")');
 assert.match(html,/同时切换沙盒与审批设置/);
 assert.match(html,/value="custom" selected disabled/);
 assert.match(html,/&lt;native description>/);
 assert.doesNotMatch(html,/不改变沙盒权限或审批者/);
});

test('Codex desktop draft shows the native submit step; created rollout links by thread ID',()=>{
 const {ctx}=setup();ctx.data={...cap,capability:{...cap.capability,tool:'codex',create:{cwd:'/project',revision:'v1',mode:'draft'}}};
 run(ctx,'interaction.byTask.set("a",data)');
 const html=run(ctx,'interactionHtml("a")');
 assert.match(html,/在 Codex 打开草稿/);assert.doesNotMatch(html,/新建并发送/);
 run(ctx,`workspace.tasks=[{ui_key:'a',machine_id:'one',tool:'codex'},{ui_key:'new',machine_id:'one',tool:'codex',session_id:'rollout-file',extra:{thread_id:'native-id'}}]`);
 assert.match(run(ctx,'createdSessionHtml("a",{result:{tool:"codex",session_id:"native-id"}})'),/打开新会话/);
});

test('Antigravity displays its bound creation project and requires confirmation for a project policy',async()=>{
 const {ctx}=setup();ctx.data={...cap,capability:{...cap.capability,tool:'antigravity',create:{location:'native-project',project_id:'project-id',project_name:'Example <project>',revision:'c1'},policy:{current:'ask',scope:'project-permissions',project_name:'Example <project>',options:[{value:'ask',label:'Ask'},{value:'review',label:'Review'}],revision:'p1'}}};
 run(ctx,'interaction.byTask.set("a",data)');const html=run(ctx,'interactionHtml("a")');
 assert.match(html,/新建 Antigravity 会话/);assert.match(html,/Example &lt;project>/);assert.match(html,/应用到整个项目/);
 ctx.form={elements:{policy:{value:'review'},confirmProject:{checked:false}}};
 let calls=0;ctx.apiFetch=()=>{calls++};await run(ctx,'sendPolicy(form,"p1")');assert.equal(calls,0);
});
