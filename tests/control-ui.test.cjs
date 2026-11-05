const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const crypto=require('node:crypto').webcrypto;
function setup(){
 const feedback={innerHTML:''},summary={innerHTML:''},outgoing={innerHTML:'',querySelector(){return null}},button={disabled:false},input={value:''},timers=new Map();let timerId=0;
 const node={dataset:{},innerHTML:'',contains(){return false},querySelector(selector){return selector==='.settings-summary-slot'?summary:selector==='.outgoing-message'?outgoing:selector==='.local-outgoing'?{scrollIntoView(){}}:feedback},querySelectorAll(){return [button]}};
 const body={...node,dataset:{},innerHTML:''},actions={innerHTML:''},dialog={open:false,showModal(){this.open=true},close(){this.open=false}};
 const ctx=vm.createContext({console,crypto,setInterval(fn,ms){timers.set(++timerId,{fn,ms});return timerId},clearInterval(id){timers.delete(id)},document:{getElementById(id){return ({'chat-outgoing':outgoing,'composer-send':button,'native-reply':input,'session-options-body':body,'session-options':dialog,'session-actions':actions})[id]||node},querySelector(){return null},activeElement:null},workspace:{selected:'a',page:'sessions'},esc:s=>String(s??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;'),jsArg:s=>JSON.stringify(s),markdown:s=>String(s).replaceAll('<','&lt;'),toast(){},showAuthView(){}});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/control.js'),'utf8'),ctx);vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/session-options.js'),'utf8'),ctx);return {ctx,node,body,actions,dialog,feedback,summary,outgoing,button,input,timers};
}
function run(ctx,s){return vm.runInContext(s,ctx)}
const cap={connected:true,capability:{reply:true,requests:[]},commands:[]};
test('modal Tab wraps visible controls but leaves interior navigation and Escape native',()=>{
 const {ctx,dialog}=setup();dialog.open=true;
 const node=(options={})=>({tabIndex:0,type:'button',name:'',form:null,checked:false,
  matches(){return !!this.disabled},closest(){return this.hidden?{}:null},
  getClientRects(){return this.collapsed?[]:[{}]},focus(){ctx.document.activeElement=this},...options});
 const close=node(),summary=node({type:'summary'}),text=node({type:'textarea'}),select=node({type:'select'});
 const unavailable=[node({hidden:true}),node({disabled:true}),node({collapsed:true}),node({tabIndex:-1})];
 dialog.querySelectorAll=()=>[close,summary,text,select,...unavailable];
 const key=(active,key='Tab',shiftKey=false)=>{
  ctx.document.activeElement=active;let prevented=false;ctx.keyEvent={key,shiftKey,preventDefault(){prevented=true}};
  run(ctx,'trapSessionOptionsFocus(keyEvent)');return prevented;
 };
 assert.equal(key(close,'Tab',true),true);assert.equal(ctx.document.activeElement,select);
 assert.equal(key(select),true);assert.equal(ctx.document.activeElement,close);
 for(const middle of [summary,text]){assert.equal(key(middle),false);assert.equal(ctx.document.activeElement,middle);}
 assert.equal(key(close,'Escape'),false);assert.equal(ctx.document.activeElement,close);
 assert.equal(key(null),true);assert.equal(ctx.document.activeElement,close);
 dialog.open=false;assert.equal(key(select),false);
});
test('modal Tab boundary uses the checked radio in each native group',()=>{
 const {ctx,dialog}=setup();dialog.open=true;
 const form={},node=options=>({tabIndex:0,type:'radio',name:'policy',form,
  matches(){return false},closest(){return null},getClientRects(){return [{}]},
  focus(){ctx.document.activeElement=this},...options});
 const close=node({type:'button',name:''}),first=node({checked:true}),second=node({checked:false});
 dialog.querySelectorAll=()=>[close,first,second];
 let prevented=0;ctx.keyEvent={key:'Tab',shiftKey:true,preventDefault(){prevented++}};
 ctx.document.activeElement=close;run(ctx,'trapSessionOptionsFocus(keyEvent)');
 assert.equal(ctx.document.activeElement,first);assert.equal(prevented,1);
 ctx.keyEvent.shiftKey=false;run(ctx,'trapSessionOptionsFocus(keyEvent)');
 assert.equal(ctx.document.activeElement,close);assert.equal(prevented,2);
 first.checked=false;second.checked=true;ctx.keyEvent.shiftKey=true;
 run(ctx,'trapSessionOptionsFocus(keyEvent)');assert.equal(ctx.document.activeElement,second);
});
test('collapsed details excludes radios even when the browser retains layout rectangles',()=>{
 const {ctx,dialog}=setup();dialog.open=true;
 const node=options=>({tabIndex:0,type:'button',name:'',form:null,
  matches(){return false},closest(){return null},getClientRects(){return [{}]},contains(){return false},
  focus(){ctx.document.activeElement=this},...options});
 const close=node(),details={tagName:'DETAILS',open:false,parentElement:dialog};
 const summary=node({tagName:'SUMMARY',parentElement:details});
 const wrapper={tagName:'FIELDSET',parentElement:details};
 const radio=node({type:'radio',name:'policy',checked:true,parentElement:wrapper});
 const secondSummary=node({tagName:'SUMMARY',parentElement:details});
 const summaryLink=node({parentElement:summary});summary.contains=el=>el===summaryLink;
 details.children=[summary,wrapper,secondSummary];
 dialog.querySelectorAll=()=>[close,summary,summaryLink,radio,secondSummary];
 let prevented=0;ctx.keyEvent={key:'Tab',shiftKey:true,preventDefault(){prevented++}};
 ctx.document.activeElement=close;run(ctx,'trapSessionOptionsFocus(keyEvent)');
 assert.equal(ctx.document.activeElement,summaryLink);assert.equal(prevented,1);
 ctx.keyEvent.shiftKey=false;run(ctx,'trapSessionOptionsFocus(keyEvent)');
 assert.equal(ctx.document.activeElement,close);assert.equal(prevented,2);
 details.open=true;ctx.keyEvent.shiftKey=true;run(ctx,'trapSessionOptionsFocus(keyEvent)');
 assert.equal(ctx.document.activeElement,secondSummary);
});
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
test('a rejected reply hides prior receipts while later operations still report their own result',async()=>{
 const {ctx,node,outgoing}=setup();const old={id:'old',kind:'approval',state:'delivered'};
 ctx.data={...cap,commands:[old]};
 run(ctx,'interaction.byTask.set("a",data);setReplyDraft("hello")');let release;
 ctx.apiFetch=()=>new Promise(resolve=>release=resolve);
 ctx.fetch=async()=>({ok:true,status:200,json:async()=>ctx.data});
 const sending=run(ctx,'sendReply()');assert.match(outgoing.innerHTML,/发送中/);assert.doesNotMatch(node.innerHTML,/原应用已接收/);
 release({ok:false,status:409,json:async()=>({error:'control_offline'})});await sending;
 assert.match(outgoing.innerHTML,/发送失败[\s\S]*设备交互连接已断开/);
 assert.equal(run(ctx,'interactionFeedbackHtml("a")'),'');
 ctx.data={...cap,commands:[{id:'later',kind:'answer',state:'delivered'},old]};
 await run(ctx,'refreshInteraction()');
 assert.match(run(ctx,'interactionFeedbackHtml("a")'),/最近回答问题.*原应用已接收/);
 assert.match(outgoing.innerHTML,/发送失败/);
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
 const {ctx,node,outgoing}=setup();ctx.data=cap;ctx.message='<img src=x>\nMy message';run(ctx,'interaction.byTask.set("a",data);setReplyDraft(message)');let release;
 ctx.apiFetch=()=>new Promise(resolve=>release=resolve);ctx.fetch=async()=>({ok:true,status:200,json:async()=>cap});
 const sending=run(ctx,'sendReply()');assert.match(outgoing.innerHTML,/local-outgoing/);assert.match(outgoing.innerHTML,/&lt;img src=x>\nMy message/);assert.doesNotMatch(outgoing.innerHTML,/<img/);assert.doesNotMatch(node.innerHTML,/local-outgoing/);
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
 const html=run(ctx,'creationForm(data.capability,interactionDraft("a"),"")');assert.match(html,/新建并发送/);assert.match(html,/&lt;project>/);assert.doesNotMatch(html,/<project>/);
 let body;ctx.apiFetch=async(u,o)=>{body=JSON.parse(o.body);return {ok:true,json:async()=>({command:{id:body.id,state:'queued'}})}};
 ctx.fetch=async()=>({ok:true,json:async()=>({...ctx.data,commands:[{id:body.id,state:'queued'}]})});
 await run(ctx,'sendCreate("v1")');assert.deepEqual(body.action,{kind:'create',revision:'v1',text:'New chat'});
 assert.equal(run(ctx,'interaction.outgoing.has("a")'),false);
});
test('policy only exposes native choices and rejects a changed revision',async()=>{
 const {ctx}=setup();ctx.data={...cap,capability:{...cap.capability,policy:{current:'on-request',revision:'v2',scope:'session-next-turn',options:[{value:'on-request',label:'Ask'},{value:'never',label:'Reject required approvals'}]}}};
 run(ctx,'interaction.byTask.set("a",data)');assert.match(run(ctx,'settingsHtml(data.capability,"")'),/后续轮次/);
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
 run(ctx,'interaction.byTask.set("a",data)');const html=run(ctx,'settingsHtml(data.capability,"")');
 assert.match(html,/同时切换沙盒与审批设置/);
 assert.match(html,/自定义组合（仅显示）/);
 assert.doesNotMatch(html,/value="custom"/);
 assert.doesNotMatch(html,/&lt;native description>/);
 assert.doesNotMatch(html,/不改变沙盒权限或审批者/);
});

test('Codex desktop draft shows the native submit step; created rollout links by thread ID',()=>{
 const {ctx}=setup();ctx.data={...cap,capability:{...cap.capability,tool:'codex',create:{cwd:'/project',revision:'v1',mode:'draft'}}};
 run(ctx,'interaction.byTask.set("a",data)');
 const html=run(ctx,'creationForm(data.capability,interactionDraft("a"),"")');
 assert.match(html,/在 Codex 打开草稿/);assert.doesNotMatch(html,/新建并发送/);
 run(ctx,`workspace.tasks=[{ui_key:'a',machine_id:'one',tool:'codex'},{ui_key:'new',machine_id:'one',tool:'codex',session_id:'rollout-file',extra:{thread_id:'native-id'}}]`);
 assert.match(run(ctx,'createdSessionHtml("a",{result:{tool:"codex",session_id:"native-id"}})'),/打开新会话/);
});

test('Antigravity displays its bound creation project and requires confirmation for a project policy',async()=>{
 const {ctx}=setup();ctx.data={...cap,capability:{...cap.capability,tool:'antigravity',create:{location:'native-project',project_id:'project-id',project_name:'Example <project>',revision:'c1'},policy:{current:'ask',scope:'project-permissions',project_name:'Example <project>',options:[{value:'ask',label:'Ask'},{value:'review',label:'Review'}],revision:'p1'}}};
 run(ctx,'interaction.byTask.set("a",data)');const html=run(ctx,'creationForm(data.capability,interactionDraft("a"),"")+settingsHtml(data.capability,"")');
 assert.match(html,/Antigravity/);assert.match(html,/Example &lt;project>/);assert.match(html,/应用到整个项目/);
 ctx.form={elements:{policy:{value:'review'},confirmProject:{checked:false}}};
 let calls=0;ctx.apiFetch=()=>{calls++};await run(ctx,'sendPolicy(form,"p1")');assert.equal(calls,0);
});

test('late failed poll cannot overwrite a newer connected response',async()=>{
 const {ctx}=setup();let fail;
 ctx.fetch=()=>new Promise((resolve,reject)=>{fail=reject});const older=run(ctx,'refreshInteraction()');
 ctx.fetch=async()=>({ok:true,json:async()=>cap});await run(ctx,'refreshInteraction()');
 fail(Error('old failure'));await older;
 assert.equal(run(ctx,'interaction.byTask.get("a").connected'),true);
});
test('reply area does not mix creation or editable policy controls',()=>{
 const {ctx}=setup();ctx.data={...cap,capability:{...cap.capability,tool:'codex',settings:{model:'Native Model',permission:'Workspace',scope:'session'},create:{cwd:'/example',revision:'c'},policy:{scope:'session-next-turn',current:'on-request',revision:'p',options:[{value:'on-request',label:'Ask'},{value:'never',label:'Reject'}]}}};
 run(ctx,'interaction.byTask.set("a",data)');const html=run(ctx,'interactionHtml("a")');
 assert.match(html,/Native Model/);assert.match(html,/Workspace/);
 assert.doesNotMatch(html,/sendCreate|sendPolicy|native-policy|native-create/);
 run(ctx,'openSessionOptions("settings")');const settings=run(ctx,'settingsHtml(data.capability,"")');
 assert.match(settings,/高级 · 审批规则/);assert.match(settings,/完整权限模式在 Codex 中选择/);
});
test('settings selection survives polls and resets confirmation on native revision changes',()=>{
 const {ctx,body,dialog}=setup();ctx.data={...cap,capability:{...cap.capability,tool:'antigravity',policy:{scope:'project-permissions',project_name:'Example',current:'a',revision:'v1',options:[{value:'a',label:'First',description:'first description'},{value:'b',label:'Second',description:'second description'}]}}};
 run(ctx,'interaction.byTask.set("a",data);openSessionOptions("settings");chooseSessionPolicy("b");sessionOptions.confirmed=true');
 assert.equal(dialog.open,true);assert.match(body.innerHTML,/value="b" checked/);
 assert.match(body.innerHTML,/second description/);assert.doesNotMatch(body.innerHTML,/first description/);
 const original=body.innerHTML;run(ctx,'renderSessionActions()');assert.equal(body.innerHTML,original);assert.equal(run(ctx,'sessionOptions.confirmed'),true);
 run(ctx,'data.capability.policy.revision="v2";renderSessionActions()');
 assert.equal(run(ctx,'sessionOptions.confirmed'),false);assert.equal(run(ctx,'sessionOptions.value'),'a');assert.match(body.innerHTML,/原应用状态已更新/);
 run(ctx,'workspace.selected="b";renderSessionActions()');assert.equal(dialog.open,false);
});
test('settings dialog disables native choices while an operation is pending',()=>{
 const {ctx,body}=setup();ctx.data={...cap,capability:{...cap.capability,policy:{scope:'session-permissions',current:'a',revision:'v',options:[{value:'a',label:'A'},{value:'b',label:'B'}]}}};
 run(ctx,'interaction.byTask.set("a",data);openSessionOptions("settings");chooseSessionPolicy("b");interaction.pending.set("a",{id:"pending"});renderSessionActions()');
 assert.match(body.innerHTML,/<fieldset disabled>/);assert.match(body.innerHTML,/id="policy-apply"[^>]+disabled/);
});
test('model metadata is escaped and never presented as a fabricated selection catalog',()=>{
 const {ctx}=setup();ctx.metadata={tool:'dsh',settings:{model:'<model>',permission:'<scope>',reasoning:'<effort>',note:'<note>',scope:'session'}};
 const html=run(ctx,'settingsHtml(metadata,"")');
 assert.match(html,/&lt;model>/);assert.match(html,/&lt;effort>/);assert.match(html,/&lt;scope>/);assert.match(html,/&lt;note>/);
 assert.doesNotMatch(html,/<model>|<scope>|<select/);
});


test('receipt and settings changes preserve the focused reply field',()=>{
 const {ctx,node,summary}=setup();ctx.data={...cap,capability:{...cap.capability,settings:{scope:'session',model:'Before'}}};
 run(ctx,'interaction.byTask.set("a",data);setReplyDraft("Draft");renderInteraction(true)');
 const original=node.innerHTML;node.contains=()=>true;
 run(ctx,'data.capability.settings.model="After";data.capability.create={revision:"new"};data.capability.policy={revision:"v2",options:[]};renderInteraction(true)');
 assert.match(summary.innerHTML,/After/);assert.equal(node.innerHTML,original);
 assert.equal(run(ctx,'interactionDraft("a").text'),'Draft');
});


test('a removed native session closes its settings and hides stale actions',()=>{
 const {ctx,dialog,actions}=setup();ctx.data=cap;
 run(ctx,'interaction.byTask.set("a",data);workspace.tasks=[{key:"a"}];openSessionOptions("settings");workspace.tasks=[];renderSessionActions()');
 assert.equal(dialog.open,false);assert.equal(actions.innerHTML,'');
});


test('settings and creation show only receipts for their own action kind',()=>{
 const {ctx}=setup();ctx.data={...cap,commands:[{id:'reply',kind:'reply',state:'delivered',created:10},{id:'policy',kind:'policy',state:'rejected',created:9},{id:'create',kind:'create',state:'queued',created:8}]};
 run(ctx,'interaction.byTask.set("a",data)');
 assert.doesNotMatch(run(ctx,'interactionFeedbackHtml("a","policy")'),/原应用已接收|等待设备接收/);
 assert.match(run(ctx,'interactionFeedbackHtml("a","policy")'),/最近权限设置.*原应用未接收/);
 assert.match(run(ctx,'interactionFeedbackHtml("a","create")'),/最近新建会话.*等待设备接收/);
 run(ctx,'interaction.pending.set("a",{id:"replying",action:{kind:"reply"}})');
 assert.doesNotMatch(run(ctx,'interactionFeedbackHtml("a","policy")'),/正在发送/);
});
test('dialog validation errors are visible inside the modal and preserve its draft',()=>{
 const {ctx,body}=setup();ctx.data={...cap,capability:{...cap.capability,tool:'codex',create:{cwd:'/example',revision:'v',mode:'draft'}}};
 run(ctx,'interaction.byTask.set("a",data);openSessionOptions("create");sendCreate("v")');
 assert.match(body.innerHTML,/class="options-error" role="alert">请输入新会话/);
 run(ctx,'interactionDraft("a").createText="Unsent draft";controlNotice("Try again","a","create")');
 assert.match(body.innerHTML,/Unsent draft/);assert.match(body.innerHTML,/Try again/);
});


test('composer offers settings from native facts and creation requires the selected capability',()=>{
 const {ctx,actions}=setup();ctx.data={...cap,capability:{...cap.capability,tool:'antigravity',settings:{model:'Example model',scope:'project',permission:'Request Review'}}};
 run(ctx,'interaction.byTask.set("a",data);renderSessionActions()');
 const html=run(ctx,'interactionHtml("a")');
 assert.match(html,/placeholder="向 Antigravity 发送系统通知…"/);
 assert.match(html,/composer-footer[\s\S]*composer-settings[\s\S]*记录模型 · Example model[\s\S]*项目权限 · Request Review[\s\S]*composer-send/);
 assert.equal(actions.innerHTML,'');
 run(ctx,'data.capability.create={revision:"native-v1"};renderSessionActions()');
 assert.match(actions.innerHTML,/新建会话/);assert.doesNotMatch(actions.innerHTML,/会话设置/);
 run(ctx,'workspace.selected="b";renderSessionActions()');assert.equal(actions.innerHTML,'');
 assert.equal(run(ctx,'settingsSummaryHtml({tool:"codex"})'),'');
 const codex=run(ctx,'settingsSummaryHtml({tool:"codex",settings:{model:"Example",permission:"工作区可写 · 联网受限 · 按需审批"}})');
 assert.match(codex,/composer-permission">工作区可写<\/span>/);assert.match(codex,/aria-description="Example · 工作区可写 · 联网受限 · 按需审批"/);
});
test('reply keyboard shortcut observes IME and the outstanding operation guard',()=>{
 const {ctx,button}=setup();let submissions=0;
 ctx.event={key:'Enter',ctrlKey:false,metaKey:false,isComposing:false,preventDefault(){},target:{form:{querySelector(){return button},requestSubmit(){submissions++}}}};
 run(ctx,'setReplyDraft("reply");composerKeydown(event)');assert.equal(submissions,0);
 ctx.event.metaKey=true;ctx.event.isComposing=true;run(ctx,'composerKeydown(event)');assert.equal(submissions,0);
 ctx.event.isComposing=false;run(ctx,'composerKeydown(event)');assert.equal(submissions,1);
 run(ctx,'interaction.pending.set("a",{id:"pending"});setReplyDraft("another");composerKeydown(event)');assert.equal(submissions,1);assert.equal(button.disabled,true);
 run(ctx,'interaction.pending.clear();setReplyDraft(" ");composerKeydown(event)');assert.equal(submissions,1);assert.equal(button.disabled,true);
});


test('standard delivered receipts show acceptance once and retain special delivery details',()=>{
 const {ctx}=setup();ctx.data={...cap,commands:[{id:'accepted',kind:'approval',state:'delivered',detail:'原应用已接收；不代表 Agent 已完成执行'}]};
 run(ctx,'interaction.byTask.set("a",data)');
 let html=run(ctx,'interactionFeedbackHtml("a")');
 assert.equal((html.match(/原应用已接收/g)||[]).length,1);assert.doesNotMatch(html,/已完成执行/);
 ctx.data.commands[0].detail='已进入 Codex 原生队列，但桌面唤醒失败；请在原应用查看，勿重复发送';
 assert.match(run(ctx,'interactionFeedbackHtml("a")'),/唤醒失败.*勿重复发送/);
 ctx.data.commands[0].state='unknown';ctx.data.commands[0].detail='设备断线，正在核实';
 assert.match(run(ctx,'interactionFeedbackHtml("a")'),/结果未知.*设备断线/);
});
test('a repeated question prompt appears once and native-only request guidance stays visible',()=>{
 const {ctx}=setup();
 ctx.data={...cap,capability:{reply:false,reason:'此请求请在 Codex 原应用处理',requests:[{kind:'question',id:'q',revision:'v1',prompt:'选择布局',questions:[{id:'one',question:'选择布局',options:['宽松','紧凑']}]}]}};
 run(ctx,'interaction.byTask.set("a",data)');const html=run(ctx,'interactionHtml("a")');
 assert.equal((html.match(/选择布局/g)||[]).length,1);assert.match(html,/<legend>选择布局<\/legend>/);assert.match(html,/请在 Codex 原应用处理/);
});
