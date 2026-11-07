const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const crypto=require('node:crypto').webcrypto;

function setup(text='A reply'){
 let now=1000,submission;
 const posts=[],feedback={innerHTML:''},summary={innerHTML:''};
 const outgoing={innerHTML:'',querySelector(){return null}},button={disabled:false};
 const pane={dataset:{},innerHTML:'',querySelector(selector){return selector==='.settings-summary-slot'?summary:feedback},querySelectorAll(){return []}};
 const input={value:text,form:{querySelector(){return button},requestSubmit(){submission=run('sendReply()')}}};
 const data={connected:true,capability:{tool:'codex',reply:true,requests:[]},commands:[]};
 const context=vm.createContext({crypto,console,Date:class extends Date{static now(){return now}},
  setInterval(){return 1},clearInterval(){},workspace:{selected:'demo',page:'sessions'},
  document:{getElementById(id){return ({'native-reply':input,'composer-send':button,'session-control':pane,'chat-outgoing':outgoing})[id]||null}},
  esc:value=>String(value??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;'),
  jsArg:JSON.stringify,harnessName(){return 'Codex'},settingsSummaryHtml(){return ''},toast(){},showAuthView(){},
  apiFetch:async(url,options)=>{const request=JSON.parse(options.body);posts.push(request);data.commands=[{id:request.id,kind:'reply',state:'delivered'}];return {ok:true,json:async()=>({command:data.commands[0]})}},
  fetch:async()=>({ok:true,json:async()=>data})});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/control.js'),'utf8'),context);
 const run=source=>vm.runInContext(source,context);
 context.data=data;context.initialText=text;
 run('interaction.byTask.set("demo",data);setReplyDraft(initialText)');
 function event(overrides={}){
  const e={key:'Enter',keyCode:13,ctrlKey:false,metaKey:false,shiftKey:false,altKey:false,
   isComposing:false,repeat:false,target:input,prevented:false,preventDefault(){this.prevented=true},...overrides};
  return e;
 }
 function key(overrides={}){context.keyEvent=event(overrides);run('composerKeydown(keyEvent)');return context.keyEvent;}
 function up(){context.keyEvent=event();run('composerKeyup(keyEvent)');}
 function composition(type){context.compositionEvent={type,target:input};run('composerComposition(compositionEvent)');}
 return {context,run,data,posts,input,button,pane,outgoing,key,up,composition,advance(ms){now+=ms},done(){return submission}};
}

test('plain Enter and Ctrl/Cmd+Enter use the real reply submission and clear immediately',async()=>{
 for(const modifiers of [{},{ctrlKey:true},{metaKey:true}]){
  const s=setup('Send from keyboard');const e=s.key(modifiers);
  assert.equal(e.prevented,true);assert.equal(s.posts.length,1);
  assert.deepEqual(s.posts[0].action,{kind:'reply',text:'Send from keyboard'});
  await s.done();assert.equal(s.run('interactionDraft("demo").text'),'');assert.equal(s.input.value,'');
  assert.match(s.outgoing.innerHTML,/已送达/);
 }
});

test('Shift+Enter leaves newline handling to the textarea and preserves a multiline reply',async()=>{
 const s=setup('First line');
 assert.equal(s.key({shiftKey:true}).prevented,false);assert.equal(s.posts.length,0);
 s.input.value+='\nSecond line';s.context.edited=s.input.value;s.run('setReplyDraft(edited)');
 assert.equal(s.key({shiftKey:true,ctrlKey:true}).prevented,false);assert.equal(s.posts.length,0);
 s.key();await s.done();assert.equal(s.posts[0].action.text,'First line\nSecond line');
});

test('queued keyboard replies clear immediately and cannot double-submit before delivery',async()=>{
 const s=setup('Still waiting');let release;
 s.context.apiFetch=(url,options)=>{const request=JSON.parse(options.body);s.posts.push(request);return new Promise(resolve=>release=resolve)};
 s.key();assert.equal(s.posts.length,1);assert.match(s.outgoing.innerHTML,/发送中/);
 assert.equal(s.input.value,'');assert.equal(s.run('interactionDraft("demo").text'),'');
 assert.deepEqual(Object.keys(s.posts[0]).sort(),['action','id']);
 s.button.disabled=false;s.key();s.key({repeat:true});assert.equal(s.posts.length,1);
 const command={id:s.posts[0].id,kind:'reply',state:'queued'};s.data.commands=[command];
 release({ok:true,json:async()=>({command})});await s.done();
 assert.equal(s.run('interactionDraft("demo").text'),'');assert.match(s.outgoing.innerHTML,/等待设备接收/);
 command.state='delivered';await s.run('refreshInteraction()');assert.equal(s.input.value,'');
});

test('rejected keyboard submission keeps the draft and held Enter never retries after the button re-enables',async()=>{
 const s=setup('Keep this draft');
 s.context.apiFetch=async(url,options)=>{s.posts.push(JSON.parse(options.body));return {ok:false,json:async()=>({error:'reply_unavailable'})}};
 s.key();await s.done();assert.equal(s.posts.length,1);assert.equal(s.run('interactionDraft("demo").text'),'Keep this draft');
 assert.match(s.outgoing.innerHTML,/发送失败[\s\S]*暂时不能接收回复/);assert.equal(s.button.disabled,false);
 assert.equal(s.key({repeat:true}).prevented,true);assert.equal(s.posts.length,1);
 s.up();s.key();await s.done();assert.equal(s.posts.length,2);
});

test('blank drafts and busy native commands block Enter even with stale enabled button markup',()=>{
 for(const blank of ['','  \n\t','　']){
  const s=setup(blank);s.button.disabled=false;assert.equal(s.key().prevented,true);assert.equal(s.posts.length,0);
 }
 for(const state of ['queued','dispatched']){
  const s=setup();s.data.commands=[{id:'existing',state}];s.button.disabled=false;s.key();assert.equal(s.posts.length,0);
 }
 const s=setup();s.run('interaction.pending.set("demo",{id:"uncertain",uncertain:true})');s.key();assert.equal(s.posts.length,0);
});

test('native IME key flags and composition lifecycle leave the confirmation Enter unsent',async()=>{
 for(const flags of [{isComposing:true},{keyCode:229}]){
  const s=setup('输入法内容');assert.equal(s.key(flags).prevented,false);assert.equal(s.posts.length,0);
  assert.equal(s.key().prevented,true);assert.equal(s.posts.length,0);
  s.up();s.key();await s.done();assert.equal(s.posts.length,1);
 }
 const s=setup('组合中的内容');s.composition('compositionstart');
 assert.equal(s.key().prevented,false);assert.equal(s.posts.length,0);
 s.composition('compositionend');s.key();assert.equal(s.posts.length,0);
 s.up();s.key();await s.done();assert.equal(s.posts.length,1);
});

test('compositionend before confirming keydown is guarded, then a released intentional Enter can send',async()=>{
 const s=setup('已选中的候选词');s.composition('compositionstart');s.composition('compositionend');
 assert.equal(s.key().prevented,true);assert.equal(s.posts.length,0);
 s.key({repeat:true});assert.equal(s.posts.length,0);
 s.up();s.key();await s.done();assert.equal(s.posts.length,1);
 const mouseCommit=setup('鼠标选词');mouseCommit.composition('compositionstart');mouseCommit.composition('compositionend');
 mouseCommit.advance(201);mouseCommit.key();await mouseCommit.done();assert.equal(mouseCommit.posts.length,1);
});

test('non-Enter keys, Alt+Enter and detached inputs do not submit; markup exposes the correct help',()=>{
 const s=setup();assert.equal(s.key({key:'a',keyCode:65}).prevented,false);
 assert.equal(s.key({altKey:true}).prevented,false);assert.equal(s.posts.length,0);
 s.input.form=null;assert.doesNotThrow(()=>s.key());assert.equal(s.posts.length,0);
 const html=s.run('interactionHtml("demo")');
 assert.match(html,/aria-keyshortcuts="Enter Control\+Enter Meta\+Enter"/);
 assert.match(html,/Shift\+Enter 换行/);assert.match(html,/oncompositionstart="composerComposition\(event\)"/);
 assert.match(html,/oncompositionend="composerComposition\(event\)"/);assert.match(html,/onkeyup="composerKeyup\(event\)"/);
});


test('late receipts preserve subsequent edits, even identical or manually cleared drafts',async()=>{
 for(const state of ['delivered','waiting','rejected','expired','cancelled','unknown']){
  for(const edited of ['A reply','New draft','']){
   const s=setup();let release;
   s.context.apiFetch=(url,options)=>{s.posts.push(JSON.parse(options.body));return new Promise(resolve=>release=resolve)};
   s.key();s.context.edited=edited;s.run('setReplyDraft("temporary edit");setReplyDraft(edited)');s.input.value=edited;
   const command={id:s.posts[0].id,kind:'reply',state};s.data.commands=[command];
   release({ok:true,json:async()=>({command})});await s.done();
   assert.equal(s.input.value,edited,state);assert.equal(s.run('interactionDraft("demo").text'),edited,state);
  }
 }
});

test('failed or uncertain receipts restore an untouched draft without automatic retry',async()=>{
 for(const state of ['rejected','expired','cancelled','unknown']){
  const s=setup('Restore this reply');
  s.context.apiFetch=async(url,options)=>{const request=JSON.parse(options.body);s.posts.push(request);
   const command={id:request.id,kind:'reply',state};s.data.commands=[command];return {ok:true,json:async()=>({command})}};
  s.key();assert.equal(s.input.value,'');await s.done();
  assert.equal(s.input.value,'Restore this reply');assert.equal(s.posts.length,1);
 }
});

test('a failed request cannot overwrite the next chat composer',async()=>{
 const s=setup('First chat draft');let release;
 s.context.apiFetch=()=>new Promise(resolve=>release=resolve);
 s.key();s.run('workspace.selected="other";setReplyDraft("Other chat draft")');s.input.value='Other chat draft';
 release({ok:false,json:async()=>({error:'reply_unavailable'})});await s.done();
 assert.equal(s.input.value,'Other chat draft');assert.equal(s.run('interactionDraft("demo").text'),'First chat draft');
});
