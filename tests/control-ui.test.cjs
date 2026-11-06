const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const crypto=require('node:crypto').webcrypto;
function setup(){
 const feedback={innerHTML:''},summary={innerHTML:''},queue={innerHTML:''},outgoing={innerHTML:'',querySelector(){return null}},button={disabled:false},input={value:''},timers=new Map();let timerId=0;
 const node={dataset:{},innerHTML:'',contains(){return false},querySelector(selector){return selector==='.settings-summary-slot'?summary:selector==='.reply-queue-slot'?queue:selector==='.outgoing-message'?outgoing:selector==='.local-outgoing'?{scrollIntoView(){}}:feedback},querySelectorAll(){return [button]}};
 const optionsFeedback={innerHTML:''},body={...node,dataset:{},innerHTML:'',querySelector(selector){return selector==='.interaction-feedback'?optionsFeedback:node.querySelector(selector)}},actions={innerHTML:''},listeners=new Map();
 const dialog={open:false,modalCalls:0,menuCalls:0,style:{},classList:{toggle(){}},
  showModal(){this.open=true;this.modalCalls++},show(){this.open=true;this.menuCalls++},close(){this.open=false},
  querySelector(){return null},querySelectorAll(){return []},contains(target){return target===this||target===body}};
 const ctx=vm.createContext({console,crypto,setInterval(fn,ms){timers.set(++timerId,{fn,ms});return timerId},clearInterval(id){timers.delete(id)},document:{getElementById(id){return ({'chat-outgoing':outgoing,'composer-send':button,'native-reply':input,'session-options-body':body,'session-options':dialog,'session-actions':actions})[id]||node},querySelector(){return null},addEventListener(kind,listener){listeners.set(kind,listener)},activeElement:null},workspace:{selected:'a',page:'sessions'},esc:s=>String(s??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;'),jsArg:s=>String(JSON.stringify(s)??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;'),markdown:s=>String(s).replaceAll('<','&lt;'),toast(){},showAuthView(){}});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/control.js'),'utf8'),ctx);vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/session-options.js'),'utf8'),ctx);return {ctx,node,body,actions,dialog,feedback,optionsFeedback,summary,outgoing,button,input,timers,listeners};
}
function run(ctx,s){return vm.runInContext(s,ctx)}
function menuChoices(body){return [...body.innerHTML.matchAll(/<button\b([^>]*role="menuitemradio"[^>]*)>([\s\S]*?)<\/button>/g)].map(([,attributes,html])=>({
 value:attributes.match(/data-choice="([^"]*)"/)?.[1],checked:attributes.includes('aria-checked="true"'),disabled:/\sdisabled(?:\s|$)/.test(attributes),html
}));}
function menuChoice(body,value){const rows=menuChoices(body).filter(row=>row.value===value);assert.equal(rows.length,1,'one native choice: '+value);return rows[0];}
function deferredAction(ctx){
 const posts=[];let complete;
 ctx.apiFetch=(url,options)=>{const record=JSON.parse(options.body);posts.push(record);return new Promise(resolve=>{complete=resolve})};
 ctx.fetch=async()=>({ok:true,json:async()=>ctx.data});
 return {posts,finish(state='resolved',update=()=>{}){const command={id:posts.at(-1).id,kind:posts.at(-1).action.kind,state};update(command);ctx.data.commands=[command];complete({ok:true,json:async()=>({command})});}};
}
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
test('native history replaces the echo before its receipt or collector catches up without claiming delivery',async()=>{
 const {ctx,outgoing}=setup();ctx.histories=new Map([['a',{loaded:true,messages:[{id:'old-user',role:'user',text:'Repeat this',timestamp:'2000-01-01T00:00:00Z'}]}]]);
 ctx.data=cap;run(ctx,'interaction.byTask.set("a",data);setReplyDraft("Repeat this")');let release,command;
 ctx.apiFetch=(url,options)=>new Promise(resolve=>{command={id:JSON.parse(options.body).id,kind:'reply',state:'delivered'};release=resolve;});
 ctx.fetch=async()=>({ok:true,json:async()=>({...cap,commands:[command]})});
 const sending=run(ctx,'sendReply()');
 assert.deepEqual(Array.from(run(ctx,'interaction.outgoing.get("a").previousHistoryIds')),['old-user']);
 const created=run(ctx,'interaction.outgoing.get("a").createdAt');
 ctx.histories.get('a').messages.push({id:'new-user',role:'user',text:'Repeat this',timestamp:new Date(Math.floor(created/1000)*1000).toISOString()});
 run(ctx,'renderInteraction()');assert.equal(outgoing.innerHTML,'');
 assert.equal(run(ctx,'interaction.outgoing.get("a").state'),'sending');
 assert.equal(run(ctx,'interaction.pending.has("a")'),true);
 release({ok:true,json:async()=>({command})});await sending;
 assert.equal(outgoing.innerHTML,'');assert.equal(run(ctx,'interaction.outgoing.get("a").confirmedInHistory'),true);
 ctx.histories.get('a').messages=[];run(ctx,'renderInteraction()');assert.equal(outgoing.innerHTML,'');
});
test('history echo matching rejects old ids, older or invalid times, wrong roles and other conversations',()=>{
 const {ctx}=setup();ctx.histories=new Map([['a',{loaded:true,messages:[]}]]);
 run(ctx,'interaction.outgoing.set("a",{id:"reply",text:"Same text",createdAt:1000599,baselineAt:0,state:"delivered",previousHistoryIds:["old"]})');
 const invalid=[
  {id:'old',role:'user',text:'Same text',timestamp:new Date(1001000).toISOString()},
  {id:'new',role:'user',text:'Same text',timestamp:new Date(999000).toISOString()},
  {id:'new',role:'user',text:'Same text',timestamp:''},
  {id:'new',role:'user',text:'Same text',timestamp:'not-a-date'},
  {id:'new',role:'assistant',text:'Same text',timestamp:new Date(1001000).toISOString()},
  {id:'new',role:'user',text:'Different text',timestamp:new Date(1001000).toISOString()},
 ];
 for(const row of invalid){ctx.histories.get('a').messages=[row];assert.match(run(ctx,'outgoingHtml("a")'),/Same text/);}
 const valid={id:'fresh',role:'user',text:'Same text',timestamp:new Date(1000000).toISOString()};
 ctx.histories.set('b',{loaded:true,messages:[valid]});ctx.histories.get('a').messages=[];assert.match(run(ctx,'outgoingHtml("a")'),/Same text/);
 ctx.histories.get('a').messages=[valid];ctx.histories.get('a').loaded=false;assert.match(run(ctx,'outgoingHtml("a")'),/Same text/);
 ctx.histories.get('a').loaded=true;assert.equal(run(ctx,'outgoingHtml("a")'),'');
});
test('native history trimming the submitted trailing newline does not duplicate a delivered reply',()=>{
 const {ctx}=setup();ctx.histories=new Map([['a',{loaded:true,messages:[]}]]);
 run(ctx,'interaction.outgoing.set("a",{id:"reply",text:"  Same text\\nSecond line\\n",createdAt:1000599,state:"delivered",previousHistoryIds:["old"]})');
 const native={id:'new',role:'user',text:'Same text\nSecond line',timestamp:new Date(1001000).toISOString()};
 for(const row of [
  {...native,id:'old'},
  {...native,timestamp:new Date(999000).toISOString()},
  {...native,text:'Same text Second line'},
  {...native,text:'Same  text\nSecond line'},
 ]) {
  ctx.histories.get('a').messages=[row];assert.match(run(ctx,'outgoingHtml("a")'),/local-outgoing/);
 }
 ctx.histories.get('a').messages=[native];
 assert.equal(run(ctx,'outgoingHtml("a")'),'');
 assert.equal(run(ctx,'interaction.outgoing.get("a").confirmedInHistory'),true);
});
test('native history hides pending duplicate echoes while failed and uncertain results stay visible',()=>{
 const {ctx}=setup();ctx.histories=new Map([['a',{loaded:true,messages:[{id:'new',role:'user',text:'Visible',timestamp:'2000-01-01T00:00:00Z'}]}]]);
 for(const state of ['sending','queued','dispatched','rejected','expired','cancelled','unknown']){
  ctx.state=state;run(ctx,'interaction.outgoing.set("a",{id:"reply",text:"Visible",createdAt:1000,state,previousHistoryIds:[],confirmedInHistory:true})');
  if(['sending','queued','dispatched'].includes(state))assert.equal(run(ctx,'outgoingHtml("a")'),'');
  else assert.match(run(ctx,'outgoingHtml("a")'),/Visible/);
 }
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
 run(ctx,'interaction.byTask.set("a",data)');assert.match(run(ctx,'permissionSettingsHtml(data.capability,"")'),/审批规则.*不提供完整权限模式/);
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

test('DeepSeek presets retain native descriptions and keep a custom current value read-only',()=>{
 const {ctx}=setup();ctx.data={...cap,capability:{...cap.capability,policy:{current:'custom',revision:'preset-v1',scope:'session-permissions',options:[{value:'workspace-write',label:'Workspace',description:'<native description>'}]}}};
 run(ctx,'interaction.byTask.set("a",data)');const html=run(ctx,'permissionSettingsHtml(data.capability,"")');
 const custom=menuChoice({innerHTML:html},'custom');assert.equal(custom.checked,true);assert.equal(custom.disabled,true);
 assert.match(custom.html,/当前/);
 assert.match(html,/title="&lt;native description>"/);assert.doesNotMatch(html,/<native description>/);
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
 run(ctx,'interaction.byTask.set("a",data);openSessionOptions("permission");chooseSessionPolicy("review")');const html=run(ctx,'creationForm(data.capability,interactionDraft("a"),"")+permissionSettingsHtml(data.capability,"")');
 assert.match(html,/Antigravity/);assert.match(html,/Example &lt;project>/);assert.match(html,/项目「Example &lt;project>」的所有会话/);assert.equal(run(ctx,'sessionOptions.confirmValue'),'review');
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
 run(ctx,'openSessionOptions("permission")');const settings=run(ctx,'permissionSettingsHtml(data.capability,"")');
 assert.match(settings,/审批规则/);assert.match(settings,/当前连接不提供完整权限模式/);
});
test('settings selection survives polls and resets confirmation on native revision changes',()=>{
 const {ctx,body,dialog}=setup();ctx.data={...cap,capability:{...cap.capability,tool:'antigravity',policy:{scope:'project-permissions',project_name:'Example',current:'a',revision:'v1',options:[{value:'a',label:'First',description:'first description'},{value:'b',label:'Second',description:'second description'}]}}};
 run(ctx,'interaction.byTask.set("a",data);openSessionOptions("permission");chooseSessionPolicy("b");sessionOptions.confirmed=true');
 assert.equal(dialog.open,true);assert.equal(menuChoice(body,'b').checked,true);
 assert.match(body.innerHTML,/将项目「Example」的所有会话设为「Second」/);
 const original=body.innerHTML;run(ctx,'renderSessionActions()');assert.equal(body.innerHTML,original);assert.equal(run(ctx,'sessionOptions.confirmed'),true);
 run(ctx,'data.capability.policy.revision="v2";renderSessionActions()');
 assert.equal(run(ctx,'sessionOptions.confirmed'),false);assert.equal(run(ctx,'sessionOptions.value'),'a');assert.match(body.innerHTML,/原应用状态已更新/);
 run(ctx,'workspace.selected="b";renderSessionActions()');assert.equal(dialog.open,false);
});
test('settings dialog disables native choices while an operation is pending',()=>{
 const {ctx,body}=setup();ctx.data={...cap,capability:{...cap.capability,policy:{scope:'session-permissions',current:'a',revision:'v',options:[{value:'a',label:'A'},{value:'b',label:'B'}]}}};
 run(ctx,'interaction.byTask.set("a",data);openSessionOptions("permission");interaction.pending.set("a",{id:"pending"});renderSessionActions()');
 assert.ok(menuChoices(body).every(choice=>choice.disabled));
 let writes=0;ctx.apiFetch=async()=>{writes++};run(ctx,'chooseSessionPolicy("b")');assert.equal(writes,0);assert.equal(run(ctx,'sessionOptions.value'),'a');
});
test('a confirmed own permission change updates current selection without a stale warning',async()=>{
 const {ctx,body}=setup();ctx.data={...cap,capability:{...cap.capability,tool:'codex',policy:{scope:'session-permissions',current:'a',revision:'v1',options:[{value:'a',label:'A'},{value:'b',label:'B'}]}}};
 run(ctx,'interaction.byTask.set("a",data);openSessionOptions("permission")');const transport=deferredAction(ctx);
 const changing=run(ctx,'chooseSessionPolicy("b")');assert.equal(transport.posts.length,1);
 transport.finish('resolved',command=>{ctx.data.capability.policy={...ctx.data.capability.policy,current:'b',revision:'v2'};command.detail='原应用已确认此会话后续轮次的权限模式';});await changing;
 run(ctx,'openSessionOptions("permission")');
 assert.doesNotMatch(body.innerHTML,/原应用状态已更新/);assert.equal(menuChoice(body,'b').checked,true);
 assert.match(body.innerHTML,/原应用已确认此会话后续轮次的权限模式/);
 assert.equal(run(ctx,'interaction.pending.has("a")'),false);
});
test('clean permission snapshots update silently but an unconfirmed selection cannot mask an external value',()=>{
 const {ctx,body}=setup();ctx.data={...cap,capability:{...cap.capability,tool:'codex',policy:{scope:'session-permissions',current:'a',revision:'v1',options:[{value:'a',label:'A'},{value:'b',label:'B'},{value:'c',label:'C'}]}}};
 ctx.data.capability.policy.scope='project-permissions';ctx.data.capability.policy.project_name='Example';
 run(ctx,'interaction.byTask.set("a",data);openSessionOptions("permission");data.capability.policy.current="c";data.capability.policy.revision="v2";renderSessionActions()');
 assert.doesNotMatch(body.innerHTML,/原应用状态已更新/);
 run(ctx,'chooseSessionPolicy("b");data.capability.policy.current="a";data.capability.policy.revision="v3";renderSessionActions()');
 assert.match(body.innerHTML,/原应用状态已更新，请重新选择/);assert.equal(menuChoice(body,'a').checked,true);assert.equal(run(ctx,'sessionOptions.confirmValue'),null);
 run(ctx,'chooseSessionPolicy("c")');assert.doesNotMatch(body.innerHTML,/原应用状态已更新/);
});
test('model effort and permission menus separate read-only facts and escape native values',()=>{
 const {ctx}=setup();ctx.metadata={tool:'dsh',settings:{model:'<model>',permission:'<scope>',reasoning:'<effort>',note:'Mixed information',scope:'session'}};
 const model=run(ctx,'modelSettingsHtml(metadata,"")'),effort=run(ctx,'effortSettingsHtml(metadata,"")'),permission=run(ctx,'permissionSettingsHtml(metadata,"")');
 assert.match(model,/&lt;model>/);assert.doesNotMatch(model,/&lt;effort>/);assert.match(model,/未提供可选模型/);
 assert.match(effort,/&lt;effort>/);assert.doesNotMatch(effort,/&lt;model>|&lt;scope>/);
 assert.doesNotMatch(model,/<model>|<select|&lt;scope>|Mixed information|sendPolicy/);
 assert.match(permission,/&lt;scope>/);assert.ok(menuChoices({innerHTML:permission}).every(choice=>choice.disabled));assert.match(permission,/未提供可选权限/);
 assert.doesNotMatch(permission,/&lt;model>|&lt;effort>|Mixed information|sendModel/);
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
 run(ctx,'interaction.byTask.set("a",data);workspace.tasks=[{key:"a"}];openSessionOptions("permission");workspace.tasks=[];renderSessionActions()');
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
 assert.match(html,/composer-footer[\s\S]*openSessionOptions\('model',this\)[\s\S]*记录模型[\s\S]*Example model[\s\S]*openSessionOptions\('permission',this\)[\s\S]*项目权限[\s\S]*Request Review[\s\S]*composer-send/);
 assert.equal(actions.innerHTML,'');
 run(ctx,'data.capability.create={revision:"native-v1"};renderSessionActions()');
 assert.match(actions.innerHTML,/新建会话/);assert.doesNotMatch(actions.innerHTML,/会话设置/);
 run(ctx,'workspace.selected="b";renderSessionActions()');assert.equal(actions.innerHTML,'');
 assert.match(run(ctx,'settingsSummaryHtml({tool:"codex"})'),/模型：尚未读取[\s\S]*权限：尚未读取/);
 const codex=run(ctx,'settingsSummaryHtml({tool:"codex",settings:{model:"Example",permission:"工作区可写 · 联网受限 · 按需审批"}})');
 assert.match(codex,/composer-permission">工作区可写 · 联网受限 · 按需审批<\/span>/);
 const buttons=codex.match(/<button[\s\S]*?<\/button>/g);assert.equal(buttons.length,2);
 assert.match(buttons[0],/aria-label="模型：Example"/);assert.doesNotMatch(buttons[0],/工作区可写|权限/);
 assert.match(buttons[1],/aria-label="权限：工作区可写 · 联网受限 · 按需审批"/);assert.doesNotMatch(buttons[1],/Example|模型/);
});
test('reply keyboard shortcut observes IME and the outstanding operation guard',()=>{
 const {ctx,button}=setup();let submissions=0;
 ctx.event={key:'Enter',ctrlKey:false,metaKey:false,isComposing:false,preventDefault(){},target:{form:{querySelector(){return button},requestSubmit(){submissions++}}}};
 run(ctx,'setReplyDraft("reply");composerKeydown(event)');assert.equal(submissions,1);
 ctx.event.metaKey=true;ctx.event.isComposing=true;run(ctx,'composerKeydown(event)');assert.equal(submissions,1);
 ctx.event.isComposing=false;run(ctx,'composerKeyup(event);composerKeydown(event)');assert.equal(submissions,2);
 run(ctx,'interaction.pending.set("a",{id:"pending"});setReplyDraft("another");composerKeydown(event)');assert.equal(submissions,2);assert.equal(button.disabled,true);
 run(ctx,'interaction.pending.clear();setReplyDraft(" ");composerKeydown(event)');assert.equal(submissions,2);assert.equal(button.disabled,true);
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
test('standard model and permission confirmations appear once while nonstandard details stay visible',()=>{
 const {ctx}=setup();ctx.data={...cap,commands:[]};run(ctx,'interaction.byTask.set("a",data)');
 for(const kind of ['model','policy']){
  ctx.kind=kind;ctx.data.commands=[{id:'change',kind,state:'resolved',detail:'原应用已确认此会话后续轮次的设置'}];
  let html=run(ctx,'interactionFeedbackHtml("a",kind)');assert.equal((html.match(/原应用已确认/g)||[]).length,1);assert.match(html,/后续轮次的设置/);
  ctx.data.commands[0].detail='确认成功，但原生界面未能唤醒';
  html=run(ctx,'interactionFeedbackHtml("a",kind)');assert.match(html,/原应用已确认 · 确认成功，但原生界面未能唤醒/);
  ctx.data.commands[0].state='rejected';ctx.data.commands[0].detail='model_changed';
  assert.match(run(ctx,'interactionFeedbackHtml("a",kind)'),/原应用未接收 · 原生模型或运行状态已变化/);
 }
});
test('a repeated question prompt appears once and native-only request guidance stays visible',()=>{
 const {ctx}=setup();
 ctx.data={...cap,capability:{reply:false,reason:'此请求请在 Codex 原应用处理',requests:[{kind:'question',id:'q',revision:'v1',prompt:'选择布局',questions:[{id:'one',question:'选择布局',options:['宽松','紧凑']}]}]}};
 run(ctx,'interaction.byTask.set("a",data)');const html=run(ctx,'interactionHtml("a")');
 assert.equal((html.match(/选择布局/g)||[]).length,1);assert.match(html,/<legend>选择布局<\/legend>/);assert.match(html,/请在 Codex 原应用处理/);
});

const nativeModels={current:'model-a',effort:'low',revision:'models-v1',options:[
 {value:'model-a',label:'Native A',efforts:['low','high'],default_effort:'low'},
 {value:'model-b',label:'Native <B>',efforts:['medium','high'],default_effort:'medium'},
 {value:'model-c',label:'Native C',efforts:[]}
]};
test('opening model effort and permission menus selects native state without writing or using a modal',()=>{
 const {ctx,body,dialog}=setup();let writes=0;ctx.apiFetch=async()=>{writes++};
 ctx.data={...cap,capability:{...cap.capability,tool:'codex',model:{...structuredClone(nativeModels),current:'model-b',effort:'high'},
  policy:{scope:'session-permissions',current:'guardian-approvals',current_label:'Approve for me',revision:'p',options:[{value:'ask',label:'Ask for approval'}]}}};
 run(ctx,'interaction.byTask.set("a",data);openSessionOptions("model")');
 assert.equal(menuChoice(body,'model-b').checked,true);assert.equal(menuChoices(body).filter(o=>o.checked).length,1);
 assert.doesNotMatch(body.innerHTML,/aria-label="推理强度"|aria-label="权限"/);
 run(ctx,'openSessionOptions("effort")');assert.equal(menuChoice(body,'high').checked,true);
 assert.equal(menuChoices(body).filter(o=>o.checked).length,1);assert.doesNotMatch(body.innerHTML,/Native &lt;B>|Approve for me/);
 run(ctx,'openSessionOptions("permission")');const current=menuChoice(body,'guardian-approvals');
 assert.equal(current.checked,true);assert.equal(current.disabled,true);assert.match(current.html,/Approve for me/);
 assert.equal(menuChoices(body).filter(o=>o.checked).length,1);
 assert.equal(writes,0);assert.equal(dialog.modalCalls,0);assert.equal(dialog.menuCalls,3);
 assert.match(run(ctx,'settingsSummaryHtml(data.capability)'),/权限：Approve for me/);
});
test('model choice submits once immediately with a valid native effort and preserves permissions',async()=>{
 const {ctx,body,dialog}=setup();ctx.data={...cap,capability:{...cap.capability,tool:'codex',model:structuredClone(nativeModels),policy:{current:'ask',revision:'p',options:[]}}};
 run(ctx,'interaction.byTask.set("a",data);openSessionOptions("model")');const transport=deferredAction(ctx);
 const changing=run(ctx,'chooseSessionModel("model-b")');
 assert.deepEqual(transport.posts[0].action,{kind:'model',revision:'models-v1',model:'model-b',effort:'medium'});
 assert.ok(menuChoices(body).every(o=>o.disabled));assert.equal(menuChoice(body,'model-b').checked,true);
 await run(ctx,'chooseSessionModel("model-c")');assert.equal(transport.posts.length,1);
 transport.finish();await changing;assert.equal(dialog.open,false);assert.equal(ctx.data.capability.policy.current,'ask');
 assert.equal(run(ctx,'interaction.outgoing.has("a")'),false);
});
test('effort has an independent menu and submits only for the current native model',async()=>{
 const {ctx,body}=setup();ctx.data={...cap,capability:{...cap.capability,tool:'codex',model:structuredClone(nativeModels)}};
 run(ctx,'interaction.byTask.set("a",data);openSessionOptions("effort")');const transport=deferredAction(ctx);
 assert.deepEqual(menuChoices(body).map(o=>o.value),['low','high']);assert.equal(menuChoice(body,'low').checked,true);
 await run(ctx,'chooseModelEffort("fabricated")');assert.equal(transport.posts.length,0);
 const changing=run(ctx,'chooseModelEffort("high")');assert.deepEqual(transport.posts[0].action,{kind:'model',revision:'models-v1',model:'model-a',effort:'high'});
 transport.finish();await changing;
});
test('a model without a compatible current or default effort waits for an explicit native effort',async()=>{
 const {ctx,body,dialog}=setup();ctx.data={...cap,capability:{...cap.capability,tool:'codex',model:structuredClone(nativeModels)}};
 delete ctx.data.capability.model.options[1].default_effort;
 run(ctx,'interaction.byTask.set("a",data);openSessionOptions("model")');const transport=deferredAction(ctx);
 await run(ctx,'chooseSessionModel("model-b")');assert.equal(transport.posts.length,0);assert.equal(dialog.open,true);
 assert.equal(run(ctx,'sessionOptions.kind'),'effort');assert.equal(run(ctx,'sessionOptions.modelValue'),'model-b');
 assert.match(body.innerHTML,/请选择推理强度/);assert.equal(menuChoice(body,'medium').checked,false);
 assert.doesNotMatch(body.innerHTML,/data-choice="low"/);
 const changing=run(ctx,'chooseModelEffort("high")');assert.deepEqual(transport.posts[0].action,{kind:'model',revision:'models-v1',model:'model-b',effort:'high'});
 transport.finish();await changing;
});
test('a model without native reasoning choices sends no inherited effort',async()=>{
 const {ctx}=setup();ctx.data={...cap,capability:{...cap.capability,tool:'codex',model:structuredClone(nativeModels)}};
 run(ctx,'interaction.byTask.set("a",data);openSessionOptions("model")');const transport=deferredAction(ctx);
 const changing=run(ctx,'chooseSessionModel("model-c")');assert.deepEqual(transport.posts[0].action,{kind:'model',revision:'models-v1',model:'model-c'});
 transport.finish();await changing;
});
test('choosing native current values or an absent model never submits',async()=>{
 const {ctx,dialog}=setup();ctx.data={...cap,capability:{...cap.capability,tool:'codex',model:structuredClone(nativeModels)}};
 run(ctx,'interaction.byTask.set("a",data);openSessionOptions("model")');const transport=deferredAction(ctx);
 await run(ctx,'chooseSessionModel("absent-model")');assert.equal(dialog.open,true);
 await run(ctx,'chooseSessionModel("model-a")');assert.equal(dialog.open,false);
 run(ctx,'openSessionOptions("effort")');await run(ctx,'chooseModelEffort("low")');assert.equal(dialog.open,false);assert.equal(transport.posts.length,0);
});
test('model draft survives unrelated permission polling and matching confirmation across a capability gap',async()=>{
 const {ctx,body}=setup();ctx.data={...cap,capability:{...cap.capability,tool:'codex',model:structuredClone(nativeModels),settings:{permission:'Before'}}};
 run(ctx,'interaction.byTask.set("a",data);openSessionOptions("model")');const transport=deferredAction(ctx);
 const changing=run(ctx,'chooseSessionModel("model-b")'),before=body.innerHTML;
 run(ctx,'data.capability.settings.permission="After";renderSessionActions()');assert.equal(body.innerHTML,before);
 assert.equal(run(ctx,'sessionOptions.modelValue'),'model-b');assert.equal(run(ctx,'sessionOptions.effortValue'),'medium');
 const model=ctx.data.capability.model;delete ctx.data.capability.model;run(ctx,'renderSessionActions()');
 transport.finish('resolved',command=>{ctx.data.capability.model={...model,current:'model-b',effort:'medium',revision:'v2'};command.detail='原应用已确认此会话后续轮次的模型设置';});await changing;
 run(ctx,'openSessionOptions("model")');assert.equal(menuChoice(body,'model-b').checked,true);assert.doesNotMatch(body.innerHTML,/原应用状态已更新/);
 assert.match(body.innerHTML,/原应用已确认此会话后续轮次的模型设置/);
});
test('a stale model click is rejected and changed native effort invalidates an in-flight draft',async()=>{
 const {ctx,body}=setup();ctx.data={...cap,capability:{...cap.capability,tool:'codex',model:structuredClone(nativeModels)}};
 run(ctx,'interaction.byTask.set("a",data);openSessionOptions("model")');const transport=deferredAction(ctx);
 run(ctx,'data.capability.model.revision="v2"');await run(ctx,'chooseSessionModel("model-b")');assert.equal(transport.posts.length,0);
 assert.match(body.innerHTML,/原生模型或运行状态已变化/);
 run(ctx,'closeSessionOptions();openSessionOptions("model")');const changing=run(ctx,'chooseSessionModel("model-b")');assert.equal(transport.posts.length,1);
 run(ctx,'data.capability.model.current="model-b";data.capability.model.effort="high";data.capability.model.revision="v3";renderSessionActions()');
 assert.match(body.innerHTML,/原应用状态已更新，请重新选择/);assert.equal(run(ctx,'sessionOptions.effortValue'),'high');
 transport.finish();await changing;
});
test('model submission rejects stale revisions unsupported efforts unchanged settings and busy sessions',async()=>{
 const {ctx}=setup();ctx.data={...cap,capability:{...cap.capability,tool:'codex',model:structuredClone(nativeModels)}};ctx.form={elements:{model:{value:'model-b'},effort:{value:'medium'}}};
 run(ctx,'interaction.byTask.set("a",data)');let calls=0;ctx.apiFetch=async()=>{calls++};
 await run(ctx,'sendModel(form,"old")');
 ctx.form.elements.effort.value='low';await run(ctx,'sendModel(form,"models-v1")');
 ctx.form.elements.model.value='not-native';await run(ctx,'sendModel(form,"models-v1")');
 ctx.form.elements.model.value='model-c';await run(ctx,'sendModel(form,"models-v1")');
 ctx.form.elements.model.value='model-a';await run(ctx,'sendModel(form,"models-v1")');
 ctx.form.elements.model.value='model-b';ctx.form.elements.effort.value='medium';
 run(ctx,'data.commands=[{state:"dispatched"}]');await run(ctx,'sendModel(form,"models-v1")');
 run(ctx,'data.commands=[];data.connected=false');await run(ctx,'sendModel(form,"models-v1")');assert.equal(calls,0);
});
test('model actions send only native tokens and require effort only when the model offers it',async()=>{
 for(const [value,effort] of [['model-b','high'],['model-c',undefined]]){
  const {ctx}=setup();ctx.data={...cap,capability:{...cap.capability,tool:'codex',model:structuredClone(nativeModels)}};
  ctx.form={elements:{model:{value},...(effort?{effort:{value:effort}}:{})}};run(ctx,'interaction.byTask.set("a",data)');
  const transport=deferredAction(ctx),changing=run(ctx,'sendModel(form,"models-v1")');
  assert.deepEqual(transport.posts[0].action,{kind:'model',revision:'models-v1',model:value,...(effort?{effort}:{})});
  transport.finish();await changing;assert.equal(run(ctx,'interaction.outgoing.has("a")'),false);
 }
});
test('pending menus cannot dispatch another setting and capability loss removes writable choices',()=>{
 const {ctx,body}=setup();ctx.data={...cap,capability:{...cap.capability,tool:'codex',model:structuredClone(nativeModels)}};
 run(ctx,'interaction.byTask.set("a",data);interaction.pending.set("a",{id:"pending"});openSessionOptions("model")');
 assert.ok(menuChoices(body).every(o=>o.disabled));const transport=deferredAction(ctx);
 run(ctx,'chooseSessionModel("model-b");openSessionOptions("effort");chooseModelEffort("high")');assert.equal(transport.posts.length,0);
 assert.ok(menuChoices(body).every(o=>o.disabled));
 run(ctx,'interaction.pending.clear();delete data.capability.model;openSessionOptions("model")');assert.equal(menuChoices(body).length,0);assert.match(body.innerHTML,/未提供可选模型/);
});
test('unknown and recorded model permission facts do not fabricate writable capabilities',()=>{
 const {ctx,body}=setup();ctx.data={...cap,capability:{...cap.capability,tool:'antigravity'}};const transport=deferredAction(ctx);
 run(ctx,'interaction.byTask.set("a",data);openSessionOptions("model")');assert.equal(menuChoices(body).length,0);assert.match(body.innerHTML,/当前连接未提供切换/);
 run(ctx,'openSessionOptions("permission")');assert.equal(menuChoices(body).length,0);assert.match(body.innerHTML,/未提供可选权限/);
 run(ctx,'data.capability.settings={model:"Recorded A",permission:"Project permission",scope:"project"};openSessionOptions("model")');
 assert.match(body.innerHTML,/记录中的模型/);assert.ok(menuChoices(body).every(o=>o.disabled));assert.doesNotMatch(body.innerHTML,/Project permission/);
 run(ctx,'chooseSessionModel("Recorded A");openSessionOptions("permission");chooseSessionPolicy("Project permission")');
 assert.match(body.innerHTML,/<h2 id="session-options-title">项目权限<\/h2>/);assert.ok(menuChoices(body).every(o=>o.disabled));assert.equal(transport.posts.length,0);
});
test('missing and unoffered native effort are checked read-only rather than silently defaulted',()=>{
 for(const effort of [undefined,'native-special']){
  const {ctx,body}=setup();ctx.data={...cap,capability:{...cap.capability,tool:'codex',model:{...structuredClone(nativeModels),effort}}};
  run(ctx,'interaction.byTask.set("a",data);openSessionOptions("effort")');const current=menuChoices(body).find(o=>o.checked);
  assert.equal(current.value,effort||'');assert.equal(current.disabled,true);assert.match(current.html,effort?/native-special/:/原应用默认/);
  assert.equal(menuChoice(body,'low').checked,false);assert.equal(menuChoice(body,'high').checked,false);
 }
});
test('native values remain selected outside catalogs including catalogs with no writable choices',()=>{
 for(const options of [[],structuredClone(nativeModels.options)]){
  const {ctx,body}=setup();ctx.data={...cap,capability:{...cap.capability,tool:'codex',settings:{model:'Old turn model'},
   model:{current:'unlisted-model',current_label:'Current native model',revision:'m',options},
   policy:{scope:'session-permissions',current:'custom',current_label:'Custom (config.toml)',revision:'p',options:[]}}};
  run(ctx,'interaction.byTask.set("a",data);openSessionOptions("model")');const current=menuChoice(body,'unlisted-model');
  assert.equal(current.checked,true);assert.equal(current.disabled,true);assert.match(current.html,/Current native model/);assert.doesNotMatch(body.innerHTML,/Old turn model/);
  run(ctx,'openSessionOptions("permission")');assert.equal(menuChoice(body,'custom').checked,true);assert.equal(menuChoice(body,'custom').disabled,true);assert.match(body.innerHTML,/Custom \(config.toml\)/);
 }
});
test('native advanced permission profiles stay collapsed but the current advanced value stays visible',()=>{
 const {ctx,body}=setup();ctx.data={...cap,capability:{...cap.capability,tool:'codex',policy:{scope:'session-permissions',current:'readonly',revision:'p',options:[
  {value:'ask',label:'Ask'},{value:'readonly',label:'Read only',group:'advanced'},{value:'other',label:'Other',group:'advanced'}]}}};
 run(ctx,'interaction.byTask.set("a",data);openSessionOptions("permission")');
 const [visible,folded]=body.innerHTML.split('<details class="menu-more"');
 assert.equal(menuChoice({innerHTML:visible},'readonly').checked,true);assert.doesNotMatch(folded,/data-choice="readonly"/);
 assert.match(folded,/^ ontoggle=/);assert.match(folded,/<summary>其他原生配置<\/summary>[\s\S]*data-choice="other"/);
 run(ctx,'sessionOptions.advanced=true;renderSessionOptions(true)');assert.match(body.innerHTML,/<details class="menu-more" open/);
});
test('a session permission choice submits once without changing the model',async()=>{
 const {ctx,dialog}=setup();ctx.data={...cap,capability:{...cap.capability,tool:'codex',model:structuredClone(nativeModels),policy:{current:'ask',revision:'p',scope:'session-permissions',options:[{value:'ask',label:'Ask'},{value:'readonly',label:'Read only'}]}}};
 run(ctx,'interaction.byTask.set("a",data);openSessionOptions("permission")');const transport=deferredAction(ctx);
 const changing=run(ctx,'chooseSessionPolicy("readonly")');await run(ctx,'chooseSessionPolicy("readonly")');assert.equal(transport.posts.length,1);
 assert.deepEqual(transport.posts[0].action,{kind:'policy',revision:'p',value:'readonly'});transport.finish();await changing;
 assert.equal(dialog.open,false);assert.equal(ctx.data.capability.model.current,'model-a');
});
test('project permissions and full access wait for explicit confirmation and support cancellation',async()=>{
 for(const project of [true,false]){
  const {ctx,body}=setup();const selected=project?'review':'full-access';
  ctx.data={...cap,capability:{...cap.capability,tool:project?'antigravity':'codex',policy:{current:'ask',revision:'p',scope:project?'project-permissions':'session-permissions',project_id:'example',project_name:'Example',options:[{value:'ask',label:'Ask'},{value:selected,label:'Native target'}]}}};
  run(ctx,'interaction.byTask.set("a",data);openSessionOptions("permission")');const transport=deferredAction(ctx);ctx.selected=selected;
  await run(ctx,'chooseSessionPolicy(selected)');assert.equal(transport.posts.length,0);assert.match(body.innerHTML,/确认切换/);
  if(project)assert.match(body.innerHTML,/项目「Example」的所有会话/);
  run(ctx,'cancelPermissionChoice()');assert.equal(run(ctx,'sessionOptions.confirmValue'),null);assert.equal(menuChoice(body,'ask').checked,true);assert.equal(transport.posts.length,0);
  run(ctx,'chooseSessionPolicy(selected)');const changing=run(ctx,'confirmPermissionChoice()');assert.equal(transport.posts.length,1);
  assert.deepEqual(transport.posts[0].action,{kind:'policy',revision:'p',value:selected,...(project?{project_id:'example'}:{})});transport.finish();await changing;
 }
});
test('model failures keep the selection and show an in-menu error without chat activity',async()=>{
 const {ctx,body,dialog}=setup();ctx.data={...cap,capability:{...cap.capability,tool:'codex',model:structuredClone(nativeModels)}};
 run(ctx,'interaction.byTask.set("a",data);openSessionOptions("model")');let posts=0;
 ctx.apiFetch=async()=>{posts++;return {ok:false,json:async()=>({error:'model_changed'})}};ctx.fetch=async()=>({ok:true,json:async()=>ctx.data});
 await run(ctx,'chooseSessionModel("model-b")');assert.equal(posts,1);assert.equal(dialog.open,true);assert.equal(menuChoice(body,'model-b').checked,true);
 assert.match(body.innerHTML,/class="options-error" role="alert">原生模型或运行状态已变化/);
 assert.equal(run(ctx,'interaction.outgoing.has("a")'),false);assert.equal(run(ctx,'interactionFeedbackHtml("a")'),'');
 run(ctx,'openSessionOptions("permission")');assert.doesNotMatch(body.innerHTML,/原生模型或运行状态已变化/);
});
test('model receipts stay in both model and effort menus but never permission or chat activity',()=>{
 const {ctx,body}=setup();ctx.data={...cap,capability:{...cap.capability,tool:'codex',model:structuredClone(nativeModels)},commands:[{id:'model-change',kind:'model',state:'resolved',detail:'已更新后续轮次模型'}]};
 run(ctx,'interaction.byTask.set("a",data);openSessionOptions("model");controlNotice("Native model unavailable","a","model")');
 assert.match(body.innerHTML,/Native model unavailable/);assert.match(body.innerHTML,/最近模型设置.*已更新后续轮次模型/);
 run(ctx,'openSessionOptions("effort")');assert.match(body.innerHTML,/最近模型设置.*已更新后续轮次模型/);
 assert.equal(run(ctx,'interactionFeedbackHtml("a")'),'');assert.equal(run(ctx,'interactionFeedbackHtml("a","policy")'),'');
 run(ctx,'openSessionOptions("permission")');assert.doesNotMatch(body.innerHTML,/Native model unavailable|最近模型设置/);
});
test('menus dismiss on outside click or Escape return focus and leave creation modal',()=>{
 const {ctx,dialog,listeners}=setup();ctx.data={...cap,capability:{...cap.capability,tool:'codex',model:structuredClone(nativeModels),create:{revision:'c',cwd:'/example'}}};
 let focused=0;ctx.anchor={focus(){focused++}};run(ctx,'interaction.byTask.set("a",data);openSessionOptions("model",anchor)');
 listeners.get('pointerdown')({target:dialog});assert.equal(dialog.open,true);
 listeners.get('pointerdown')({target:{closest(){return {}}}});assert.equal(dialog.open,true);
 listeners.get('pointerdown')({target:{closest(){return null}}});assert.equal(dialog.open,false);assert.equal(focused,1);
 run(ctx,'openSessionOptions("effort",anchor)');let prevented=0;ctx.keyEvent={key:'Escape',preventDefault(){prevented++}};run(ctx,'sessionMenuKeydown(keyEvent)');
 assert.equal(dialog.open,false);assert.equal(focused,2);assert.equal(prevented,1);
 run(ctx,'openSessionOptions("create")');assert.equal(dialog.modalCalls,1);listeners.get('pointerdown')({target:{}});assert.equal(dialog.open,true);
});
test('menu arrow navigation follows visible enabled choices and Tab closes without trapping',()=>{
 const {ctx,dialog}=setup();ctx.data={...cap,capability:{...cap.capability,tool:'codex',model:structuredClone(nativeModels)}};
 run(ctx,'interaction.byTask.set("a",data);openSessionOptions("model")');
 const make=options=>({getClientRects(){return [{}]},focus(){ctx.document.activeElement=this},...options});
 const first=make(),second=make(),hidden=make({getClientRects(){return []}});dialog.querySelectorAll=()=>[first,second,hidden];
 let prevented=0;ctx.keyEvent={key:'Home',preventDefault(){prevented++}};run(ctx,'sessionMenuKeydown(keyEvent)');assert.equal(ctx.document.activeElement,first);
 ctx.keyEvent.key='ArrowUp';run(ctx,'sessionMenuKeydown(keyEvent)');assert.equal(ctx.document.activeElement,second);
 ctx.keyEvent.key='ArrowDown';run(ctx,'sessionMenuKeydown(keyEvent)');assert.equal(ctx.document.activeElement,first);
 ctx.keyEvent.key='End';run(ctx,'sessionMenuKeydown(keyEvent)');assert.equal(ctx.document.activeElement,second);
 ctx.keyEvent.key='Tab';run(ctx,'sessionMenuKeydown(keyEvent)');assert.equal(dialog.open,false);assert.equal(prevented,4);
});
test('menu arrows ignore advanced choices inside closed details even when layout rectangles survive',()=>{
 const {ctx,dialog}=setup();dialog.open=true;
 const first={parentElement:dialog,getClientRects(){return [{}]},focus(){ctx.document.activeElement=this}};
 const details={tagName:'DETAILS',open:false,parentElement:dialog};
 const advanced={parentElement:details,getClientRects(){return [{}]},focus(){ctx.document.activeElement=this}};
 dialog.querySelectorAll=()=>[first,advanced];let prevented=0;ctx.keyEvent={key:'End',preventDefault(){prevented++}};
 run(ctx,'sessionMenuKeydown(keyEvent)');assert.equal(ctx.document.activeElement,first);
 details.open=true;run(ctx,'sessionMenuKeydown(keyEvent)');assert.equal(ctx.document.activeElement,advanced);assert.equal(prevented,2);
});
test('opening a menu focuses its current choice and an old close event cannot clear a new menu',()=>{
 const {ctx,dialog}=setup();ctx.data={...cap,capability:{...cap.capability,tool:'codex',model:structuredClone(nativeModels)}};
 const current={focus(){ctx.document.activeElement=this}},first={focus(){ctx.document.activeElement=this}};
 dialog.querySelector=selector=>selector.includes('[aria-checked="true"]')?current:first;
 run(ctx,'interaction.byTask.set("a",data);openSessionOptions("model")');assert.equal(ctx.document.activeElement,current);
 run(ctx,'openSessionOptions("effort");sessionOptionsClosed()');assert.equal(dialog.open,true);assert.equal(run(ctx,'sessionOptions.kind'),'effort');
 run(ctx,'closeSessionOptions()');assert.equal(dialog.open,false);assert.equal(run(ctx,'sessionOptions.id'),null);
});
test('all native permission presets are listed even when one is unavailable to switch to',()=>{
 const {ctx,body}=setup();ctx.data={...cap,capability:{...cap.capability,tool:'codex',policy:{scope:'session-permissions',current:'on-request',current_option:'guardian-approvals',revision:'p',
  options:[{value:'full-access',label:'Full access'},{value:'on-request',label:'Ask for approval'}],
  unavailable_options:[{value:'guardian-approvals',label:'Approve for me',reason:'Native policy prevents selection'}]}}};
 run(ctx,'interaction.byTask.set("a",data);openSessionOptions("permission")');const transport=deferredAction(ctx);
 assert.deepEqual(menuChoices(body).map(o=>o.value),['on-request','guardian-approvals','full-access']);
 assert.equal(menuChoice(body,'guardian-approvals').checked,true);assert.equal(menuChoice(body,'guardian-approvals').disabled,true);
 assert.match(body.innerHTML,/Native policy prevents selection/);run(ctx,'chooseSessionPolicy("guardian-approvals")');assert.equal(transport.posts.length,0);
});
test('a queued settings receipt keeps the menu open until matching native success',async()=>{
 for(const kind of ['model','permission']){
  const {ctx,dialog,body,optionsFeedback}=setup();ctx.data={...cap,capability:{...cap.capability,tool:'codex',model:structuredClone(nativeModels),policy:{scope:'session-permissions',current:'ask',revision:'p',options:[{value:'ask',label:'Ask'},{value:'readonly',label:'Read only'}]}}};
  ctx.kind=kind;run(ctx,'interaction.byTask.set("a",data);openSessionOptions(kind)');const transport=deferredAction(ctx);
  const changing=run(ctx,kind==='model'?'chooseSessionModel("model-b")':'chooseSessionPolicy("readonly")');transport.finish('queued');await changing;
  assert.equal(dialog.open,true);assert.ok(menuChoices(body).every(o=>o.disabled));assert.match(optionsFeedback.innerHTML,/等待设备接收/);
  ctx.data.commands=[{id:'unrelated',kind:kind==='model'?'model':'policy',state:'resolved'},...ctx.data.commands];run(ctx,'renderSessionActions()');assert.equal(dialog.open,true);
  ctx.data.commands[1].state='dispatched';await run(ctx,'refreshInteraction()');assert.equal(dialog.open,true);
  ctx.data.commands[1].state='resolved';await run(ctx,'refreshInteraction()');assert.equal(dialog.open,false);assert.equal(transport.posts.length,1);
 }
});
test('a later native rejection remains in the originating menu without automatic retry',async()=>{
 const {ctx,dialog,body}=setup();ctx.data={...cap,capability:{...cap.capability,tool:'codex',model:structuredClone(nativeModels)}};
 run(ctx,'interaction.byTask.set("a",data);openSessionOptions("model")');const transport=deferredAction(ctx);
 const changing=run(ctx,'chooseSessionModel("model-b")');transport.finish('queued');await changing;
 ctx.data.commands[0]={...ctx.data.commands[0],state:'rejected',detail:'model_changed'};await run(ctx,'refreshInteraction()');
 assert.equal(dialog.open,true);assert.equal(menuChoice(body,'model-b').checked,true);assert.match(body.innerHTML,/原应用未接收.*原生模型或运行状态已变化/);
 assert.equal(run(ctx,'interaction.pending.has("a")'),false);await run(ctx,'refreshInteraction()');assert.equal(transport.posts.length,1);
 assert.equal(run(ctx,'interactionFeedbackHtml("a")'),'');
});
test('DeepSeek session and default model changes require confirmation for models and effort',async()=>{
 for(const kind of ['model','effort']){
  const {ctx,body,dialog}=setup();ctx.data={...cap,capability:{...cap.capability,tool:'dsh',model:{...structuredClone(nativeModels),scope:'session-and-default'}}};
  ctx.kind=kind;run(ctx,'interaction.byTask.set("a",data);openSessionOptions(kind)');const transport=deferredAction(ctx);
  const choice=kind==='model'?'chooseSessionModel("model-b")':'chooseModelEffort("high")';await run(ctx,choice);
  assert.equal(transport.posts.length,0);assert.equal(dialog.open,true);assert.equal(run(ctx,'sessionOptions.modelConfirm'),true);
  assert.match(body.innerHTML,/同时更新此会话和后续默认模型/);
  run(ctx,'cancelModelChoice()');assert.equal(run(ctx,'sessionOptions.modelConfirm'),false);assert.equal(run(ctx,'sessionOptions.modelValue'),'model-a');assert.equal(run(ctx,'sessionOptions.effortValue'),'low');assert.equal(transport.posts.length,0);
  await run(ctx,choice);const changing=run(ctx,'confirmModelChoice()');assert.equal(transport.posts.length,1);
  assert.deepEqual(transport.posts[0].action,{kind:'model',revision:'models-v1',model:kind==='model'?'model-b':'model-a',effort:kind==='model'?'medium':'high'});
  transport.finish();await changing;assert.equal(dialog.open,false);
 }
});
test('a revised DeepSeek catalog clears the pending default-model confirmation',async()=>{
 const {ctx,body}=setup();ctx.data={...cap,capability:{...cap.capability,tool:'dsh',model:{...structuredClone(nativeModels),scope:'session-and-default'}}};
 run(ctx,'interaction.byTask.set("a",data);openSessionOptions("model")');const transport=deferredAction(ctx);
 await run(ctx,'chooseSessionModel("model-b")');run(ctx,'data.capability.model.revision="new-revision";renderSessionActions()');
 assert.equal(run(ctx,'sessionOptions.modelConfirm'),false);assert.equal(run(ctx,'sessionOptions.modelConfirmed'),false);assert.equal(menuChoice(body,'model-a').checked,true);
 await run(ctx,'confirmModelChoice()');assert.equal(transport.posts.length,0);assert.match(body.innerHTML,/原应用状态已更新/);
});
test('provider and effort labels are readable while submitted tokens remain native',async()=>{
 const {ctx,body}=setup();ctx.data={...cap,capability:{...cap.capability,tool:'dsh',model:structuredClone(nativeModels)}};
 ctx.data.capability.model.options[0].provider_label='Provider <one>';
 ctx.data.capability.model.options[0].effort_labels={low:'Low',high:'Extra <High>'};
 run(ctx,'interaction.byTask.set("a",data);openSessionOptions("model")');assert.match(menuChoice(body,'model-a').html,/Native A · Provider &lt;one>/);assert.doesNotMatch(body.innerHTML,/<one>/);
 run(ctx,'openSessionOptions("effort")');assert.match(menuChoice(body,'high').html,/Extra &lt;High>/);assert.doesNotMatch(body.innerHTML,/<High>/);
 assert.match(run(ctx,'settingsSummaryHtml(data.capability)'),/推理强度：Low/);
 const transport=deferredAction(ctx),changing=run(ctx,'chooseModelEffort("high")');assert.equal(transport.posts[0].action.effort,'high');transport.finish();await changing;
});
test('selecting a normal permission after full-access confirmation removes the obsolete confirmation',async()=>{
 const {ctx,body}=setup();ctx.data={...cap,capability:{...cap.capability,tool:'codex',policy:{current:'ask',revision:'p',scope:'session-permissions',options:[{value:'ask',label:'Ask'},{value:'full-access',label:'Full access'},{value:'readonly',label:'Read only'}]}}};
 run(ctx,'interaction.byTask.set("a",data);openSessionOptions("permission")');const transport=deferredAction(ctx);
 run(ctx,'chooseSessionPolicy("full-access")');assert.match(body.innerHTML,/menu-confirm/);assert.equal(transport.posts.length,0);
 const changing=run(ctx,'chooseSessionPolicy("readonly")');assert.equal(transport.posts.length,1);assert.equal(transport.posts[0].action.value,'readonly');
 assert.equal(run(ctx,'sessionOptions.confirmValue'),null);assert.doesNotMatch(body.innerHTML,/menu-confirm/);transport.finish();await changing;
});
test('cancel cannot reset a model or permission choice after its native action has been submitted',async()=>{
 for(const kind of ['model','permission']){
  const {ctx,body}=setup();ctx.data={...cap,capability:{...cap.capability,tool:'dsh',model:{...structuredClone(nativeModels),scope:'session-and-default'},policy:{current:'ask',revision:'p',scope:'project-permissions',project_id:'example',project_name:'Example',options:[{value:'ask',label:'Ask'},{value:'review',label:'Review'}]}}};
  ctx.kind=kind;run(ctx,'interaction.byTask.set("a",data);openSessionOptions(kind)');const transport=deferredAction(ctx);
  await run(ctx,kind==='model'?'chooseSessionModel("model-b")':'chooseSessionPolicy("review")');
  const changing=run(ctx,kind==='model'?'confirmModelChoice()':'confirmPermissionChoice()');assert.equal(transport.posts.length,1);
  run(ctx,kind==='model'?'cancelModelChoice()':'cancelPermissionChoice()');
  assert.equal(run(ctx,kind==='model'?'sessionOptions.modelValue':'sessionOptions.value'),kind==='model'?'model-b':'review');
  const confirmation=body.innerHTML.match(/<div class="menu-confirm">([\s\S]*?)<\/div><\/div>/)?.[1];
  if(confirmation)assert.ok([...confirmation.matchAll(/<button([^>]*)>/g)].every(([,attrs])=>/\sdisabled(?:\s|$)/.test(attrs)));
  transport.finish();await changing;
 }
});
