const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'../static/index.html'),'utf8');
const inline=html.match(/<script>([\s\S]*?)<\/script>/)[1].replace(/checkAuth\(\);\s*$/,'');
const app=fs.readFileSync(path.join(__dirname,'../static/workspace.js'),'utf8');
function context(){
 const nodes={};
 const node=()=>({listeners:{},addEventListener(type,fn){this.listeners[type]=fn},value:'',checked:false,hidden:false,style:{},dataset:{},scrollTop:0,innerHTML:'',textContent:'',classList:{add(){},remove(){},toggle(){}},querySelectorAll(){return []},querySelector(){return null},contains(){return false},setAttribute(){},focus(){},scrollIntoView(){}});
 const document={activeElement:null,addEventListener(){},getElementById(id){return nodes[id] ||=node()},querySelectorAll(){return []},querySelector(){return null}};
 const ctx=vm.createContext({document,setInterval(){},clearInterval(){},setTimeout(){},clearTimeout(){},console,URL,location:{origin:'http://example.test'},window:{},navigator:{}});
 vm.runInContext(app,ctx);vm.runInContext(inline,ctx);return {ctx,nodes};
}
const task=(id,more={})=>({key:id,ui_key:id,title:'Task '+id,state:'done',tool:'codex',tool_short:'CX',machine:'Mac',machine_id:'mac-id',activity_at:10,extra:{},ui:{},...more});
function seed(ctx,tasks){ctx.seed=tasks;vm.runInContext('workspace.tasks=seed',ctx);}
const run=(ctx,s)=>vm.runInContext(s,ctx);

test('Pinned remains stable while Recent is newest first',()=>{
 const {ctx}=context();seed(ctx,[task('old',{activity_at:1}),task('p2',{activity_at:90,extra:{native_pin:{known:true,pinned:true,order:2}}}),task('new',{activity_at:80}),task('p1',{extra:{native_pin:{known:true,pinned:true,order:1}}})]);
 assert.equal(run(ctx,'splitList(visibleTasks()).pinned.map(t=>t.key).join(",")'),'p1,p2');
 assert.equal(run(ctx,'splitList(visibleTasks()).recent.map(t=>t.key).join(",")'),'new,old');
});
test('active and newly completed tasks precede native pins without duplication, then return after 2h',()=>{
 const {ctx,nodes}=context();run(ctx,'Date.now=()=>1800000000000');
 const pin={native_pin:{known:true,pinned:true}};
 seed(ctx,[task('pin',{extra:pin}),task('run',{state:'running',activity_at:1799999900,extra:pin}),task('done',{activity_at:1799999990,extra:pin}),task('wait',{state:'waiting_input'}),task('old')]);
 run(ctx,'renderList()');const markup=nodes['session-items'].innerHTML;
 for(const [a,b] of [['wait','run'],['run','done'],['done','pin'],['pin','old']])assert.ok(markup.indexOf(`data-session="${a}"`)<markup.indexOf(`data-session="${b}"`));
 assert.equal((markup.match(/data-session=/g)||[]).length,5);
 run(ctx,'workspace.tasks.find(t=>t.key==="run").state="done"');
 assert.equal(run(ctx,'splitList(visibleTasks()).done.length'),2);
 run(ctx,'Date.now=()=>1800007200000');
 assert.equal(run(ctx,'splitList(visibleTasks()).done.length'),0);
 assert.equal(run(ctx,'splitList(visibleTasks()).pinned.length'),3);
});
test('search/device/agent filters combine and obsolete archive flags cannot hide tasks',()=>{
 const {ctx}=context();seed(ctx,[task('a',{title:'Camera'}),task('b',{title:'Camera',machine_id:'other'}),task('c',{title:'Camera',ui:{archived:true}})]);
 run(ctx,'workspace.query="camera";workspace.device="id:mac-id";workspace.tool="codex"');
 assert.equal(run(ctx,'visibleTasks().map(t=>t.key).join(",")'),'a,c');
});
test('same project names on different devices or paths stay separate',()=>{
 const {ctx}=context();seed(ctx,[task('a',{project:'app',extra:{cwd:'/one/app'}}),task('b',{project:'app',extra:{cwd:'/two/app'}}),task('c',{project:'app',machine_id:'other',extra:{cwd:'/one/app'}})]);
 assert.equal(run(ctx,'projectGroups().length'),3);ctx.path='C:\\Work\\App\\';assert.equal(run(ctx,'normalizedPath(path)'),'c:/work/app');
});
test('list pane is shown only for conversations',()=>{
 const {ctx,nodes}=context();run(ctx,'navigate("projects")');assert.equal(nodes['sessions-view'].hidden,true);assert.equal(nodes['projects-view'].hidden,false);
 run(ctx,'navigate("devices")');assert.equal(nodes['projects-view'].hidden,true);assert.equal(nodes['devices-view'].hidden,false);
 run(ctx,'navigate("settings")');assert.equal(nodes['settings-view'].hidden,false);
});
test('row shows escaped saved title and host rather than latest reply',()=>{
 const {ctx}=context();ctx.item=task('a',{title:'Real name <script>',machine:'PC <one>',prompt:'Yes'});
 const row=run(ctx,'rowHtml(item)');assert.match(row,/Real name &lt;script&gt;/);assert.match(row,/PC &lt;one&gt;/);assert.doesNotMatch(row,/>Yes</);
});
test('2h and 12h filters stay exclusive',()=>{
 const {ctx}=context();run(ctx,'Date.now=()=>1800000000000');
 for(const [age,group] of [[7199,'done'],[7200,'recent'],[43199,'recent'],[43200,'other']]){
  ctx.item={state:'done',activity_at:1800000000-age,acked:true};assert.equal(run(ctx,'taskGroup(item)'),group);
 }
});
test('markdown and nested subagents escape raw HTML',()=>{
 const {ctx}=context();ctx.text='# Result\n\n**Bold**\n\n```html\n<img src=x onerror=alert(1)>\n```\n<script>alert(1)</script>';
 const result=run(ctx,'markdown(text)');assert.match(result,/<h2>Result<\/h2>/);assert.match(result,/<strong>Bold<\/strong>/);assert.match(result,/&lt;img/);assert.doesNotMatch(result,/<img|<script/);
 ctx.subs=[{id:'x',label:'Review <script>',state:'done',subagents:[{id:'n',label:'Tests',state:'running'}]}];
 const nested=run(ctx,'renderSubagents(subs,"main")');assert.equal((nested.match(/<details/g)||[]).length,2);assert.doesNotMatch(nested,/<details[^>]*\sopen/);assert.match(nested,/Review &lt;script&gt;/);
});
test('Markdown HTTPS links and raster images render safely with balanced destinations and no referrer',()=>{
 const {ctx}=context();ctx.text='[Code **submitted**](https://example.test/commit/a?x=1&y=2)\n\n![Preview](https://example.test/images/a(1).png "Title")';
 const result=run(ctx,'markdown(text)');
 assert.match(result,/<a href="https:\/\/example.test\/commit\/a\?x=1&amp;y=2" target="_blank" rel="noopener noreferrer">Code <strong>submitted<\/strong><\/a>/);
 assert.match(result,/<img[^>]+src="https:\/\/example.test\/images\/a\(1\).png"[^>]+alt="Preview"[^>]+loading="lazy"[^>]+referrerpolicy="no-referrer"/);
 assert.doesNotMatch(result,/!\[Preview\]|\[Code/);
});
test('ordinary HTTP panel links are navigable without automatically requesting their destination',()=>{
 const {ctx}=context();ctx.text='[Center panel](http://192.0.2.10:8090/)';
 const result=run(ctx,'markdown(text)');
 assert.match(result,/<a href="http:\/\/192.0.2.10:8090\/" target="_blank" rel="noopener noreferrer">Center panel<\/a>/);
 assert.doesNotMatch(result,/<img|<iframe|<script|src=/);
});
test('Markdown cannot turn script, data, file, HTTP, network-path or credential URLs into active resources',()=>{
 const {ctx}=context();
 for(const target of ['javascript:alert(1)','data:text/html;base64,PHNjcmlwdD4=','file:///example/image.png','http://example.test/image.png','//example.test/image.png','https://user:password@example.test/image.png','https://example.test/\\evil.png','https://example.test/image.svg']){
  ctx.target=target;const image=run(ctx,'markdown("![Preview]("+target+")")');
  assert.doesNotMatch(image,/<img|href=/);
 }
 ctx.text='<img src=x onerror=alert(1)>\n\n[<script>](https://example.test/"onmouseover="x)';
 const result=run(ctx,'markdown(text)');assert.doesNotMatch(result,/<script|<img| onmouseover=/);assert.match(result,/&lt;img/);
});
test('code stays literal while local file links become non-download chips',()=>{
 const {ctx}=context();ctx.text='`[Example](https://example.test)`\n\n```text\n![Preview](/example/image.png)\n```\n\n[Report](/example/report.txt)';
 const result=run(ctx,'markdown(text)');assert.doesNotMatch(result,/href=|src=|data-attachment-id/);
 assert.match(result,/<code>\[Example\]/);assert.match(result,/原设备文件/);assert.match(result,/Report/);
});
test('local image loading requires a matching session attachment descriptor and never puts its path in a URL',()=>{
 const {ctx}=context();ctx.text='![Native preview](</example/image (1).png>)';
 assert.match(run(ctx,'markdown(text)'),/本地图片 · 需原设备提供/);
 ctx.media={sessionId:'device/codex/example',attachments:[{id:'A'.repeat(32),source:'/example/image (1).png',name:'image.png',mime:'image/png',width:640,height:480}]};
 const result=run(ctx,'markdown(text,media)');assert.match(result,/data-attachment-id="A{32}"/);assert.match(result,/aspect-ratio:640\/480/);
 assert.doesNotMatch(result,/(?:src|href)="\/example|file:/);
 ctx.media.attachments[0].source='/example/other.png';assert.doesNotMatch(run(ctx,'markdown(text,media)'),/data-attachment-id/);
 ctx.media.attachments=[{source:'/example/image (1).png',name:'image.png',error:'attachment_unavailable'}];
 assert.match(run(ctx,'markdown(text,media)'),/原设备图片暂不可用/);
});
test('local image bytes use the authenticated session route and failures leave a retryable message',async()=>{
 const {ctx}=context();let requested='';
 ctx.URL=class extends URL {static createObjectURL(){return 'blob:synthetic-image'}static revokeObjectURL(){}};
 const button={disabled:false,hidden:false},status={textContent:''},link={hidden:true};
 const image={set src(value){this.loaded=value;this.onload()}};
 ctx.mediaNode={isConnected:true,dataset:{attachmentSession:'device/codex/example',attachmentId:'A'.repeat(32)},querySelector(selector){return {button,'.attachment-status':status,a:link,img:image}[selector]}};
 run(ctx,'workspace.selected="device/codex/example"');
 ctx.fetch=async url=>{requested=url;return {ok:true,headers:{get:()=> 'image/png'},blob:async()=>({size:100})}};
 await run(ctx,'fetchMessageAttachment(mediaNode)');
 assert.equal(requested,'/api/attachments/tasks/device%2Fcodex%2Fexample?id='+'A'.repeat(32));
 assert.equal(link.href,'blob:synthetic-image');assert.equal(link.hidden,false);assert.equal(button.hidden,true);assert.equal(status.textContent,'');
 ctx.mediaNode.dataset.attachmentId='B'.repeat(32);ctx.fetch=async()=>({ok:false,status:401});button.hidden=false;
 await run(ctx,'fetchMessageAttachment(mediaNode)');
 assert.equal(ctx.mediaNode.dataset.attachmentState,'error');assert.equal(button.disabled,false);assert.equal(status.textContent,'请重新登录');
});
test('stale detail response cannot replace a newly selected session',async()=>{
 const {ctx}=context();seed(ctx,[task('a'),task('b')]);let resolve;ctx.fetch=()=>new Promise(r=>resolve=r);
 run(ctx,'workspace.selected="a"');const pending=run(ctx,'refreshDetail()');run(ctx,'workspace.selected="b"');
 resolve({status:200,ok:true,json:async()=>({task:task('a')})});await pending;assert.equal(run(ctx,'workspace.detail'),null);
});
test('unchanged polling preserves detail DOM and scroll',()=>{
 const {ctx,nodes}=context();seed(ctx,[task('a')]);run(ctx,'workspace.selected="a";renderDetail()');
 nodes['session-detail'].innerHTML='READING';nodes['session-detail'].scrollTop=345;
 run(ctx,'renderDetail()');assert.equal(nodes['session-detail'].innerHTML,'READING');assert.equal(nodes['session-detail'].scrollTop,345);
});
test('activity changes update the header without unmounting long history or the focused reply',()=>{
 const {ctx,nodes}=context();seed(ctx,[task('a')]);run(ctx,'workspace.selected="a";renderDetail()');
 const pane=nodes['session-detail'];
 const reply={value:'Unsent',selectionStart:3};ctx.document.activeElement=reply;
 pane.scrollTop=2479;
 // A real browser clamps the offset when the long contents are removed.
 Object.defineProperty(pane,'innerHTML',{set(){pane.scrollTop=0;ctx.document.activeElement=null;}});
 run(ctx,'workspace.tasks[0].title="Updated title";workspace.detailSignature="next";renderDetail()');
 assert.match(nodes['detail-title'].textContent,/Updated title/);
 assert.equal(pane.scrollTop,2479);assert.equal(ctx.document.activeElement,reply);
});
test('switching sessions restores each reader offset and ignores a hidden reader',()=>{
 const {ctx,nodes}=context();seed(ctx,[task('a'),task('b')]);
 ctx.refreshDetail=()=>{};
 run(ctx,'selectSession("a")');const pane=nodes['session-detail'];pane.clientHeight=600;pane.scrollTop=1300;
 run(ctx,'selectSession("b")');assert.equal(pane.scrollTop,0);
 pane.scrollTop=225;run(ctx,'selectSession("a")');assert.equal(pane.scrollTop,1300);
 run(ctx,'showList()');pane.clientHeight=0;pane.scrollTop=0;
 run(ctx,'selectSession("b")');assert.equal(pane.scrollTop,225);
 assert.equal(run(ctx,'workspace.scroll.get("a")'),1300);
});
test('a returning reader waits for full detail unless they move during the request',()=>{
 for(const userMoved of [false,true]) {
  const {ctx,nodes}=context();seed(ctx,[task('a',{prompt:'Short summary'})]);
  let offset=0,maxOffset=80;
  const pane=nodes['session-detail'];
  Object.defineProperty(pane,'scrollTop',{get:()=>offset,set:value=>{offset=Math.min(maxOffset,value)}});
  run(ctx,'workspace.scroll.set("a",1400);workspace.selected="a";renderDetail()');
  assert.equal(offset,80);
  if(userMoved){pane.listeners.wheel();pane.scrollTop=35;}
  maxOffset=2100;
  run(ctx,'workspace.detail={extra:{latest_turn:{assistant:"Full reply"}}};workspace.detailSignature="loaded";renderDetail()');
  assert.equal(offset,userMoved?35:1400);
 }
});
test('first open settles at the latest captured reply once and respects reading started before the detail arrived',()=>{
 for(const moved of [false,true]){
  const {ctx,nodes}=context();
  seed(ctx,[task('a',{prompt:'Short summary'})]);
  ctx.historyState=()=>({loaded:true,messages:[],error:''});
  const pane=nodes['session-detail'];pane.scrollHeight=100;
  run(ctx,'workspace.selected="a";renderDetail()');
  if(moved){pane.listeners.wheel();pane.scrollTop=28;}
  pane.scrollHeight=3200;
  run(ctx,'workspace.detail={extra:{latest_turn:{assistant:"Captured reply"}}};workspace.detailSignature="loaded";renderDetail()');
  assert.equal(pane.scrollTop,moved?28:3200);
  pane.scrollTop=370;pane.scrollHeight=3500;
  run(ctx,'workspace.detailSignature="new-activity";renderDetail()');
  assert.equal(pane.scrollTop,370);
 }
});
test('captured conversation distinguishes initial loading, active work and an empty completed session',()=>{
 const {ctx}=context();ctx.noMessages=task('a');
 assert.match(run(ctx,'capturedChatHtml(noMessages,false)'),/正在读取/);
 assert.match(run(ctx,'capturedChatHtml(noMessages,true)'),/暂无消息/);
 ctx.noMessages.state='running';
 assert.match(run(ctx,'capturedChatHtml(noMessages,true)'),/正在处理/);
 assert.doesNotMatch(run(ctx,'capturedChatHtml(noMessages,true)'),/暂无消息/);
});
test('full detail and older collector summaries both render honestly',()=>{
 const {ctx,nodes}=context();seed(ctx,[task('a',{prompt:'Summary prompt',detail:'Short result'})]);
 ctx.full=task('a',{extra:{latest_turn:{user:'Full question',assistant:'Full result',truncated:false}}});
 run(ctx,'workspace.selected="a";workspace.detail=full;workspace.detailSignature="v1";renderDetail()');
 assert.match(nodes['latest-messages'].innerHTML,/Full question/);assert.match(nodes['latest-messages'].innerHTML,/Full result/);
 run(ctx,'workspace.detail=null;workspace.detailSignature="";renderDetail()');assert.match(nodes['latest-messages'].innerHTML,/最新结果摘要/);assert.match(nodes['latest-messages'].innerHTML,/Short result/);
});
test('settings keep outbound and inbound tokens separate',async()=>{
 const {ctx}=context();run(ctx,'load=async()=>{};settingsView.loaded=true');ctx.document.getElementById('s-hub-token').value='destination';ctx.document.getElementById('s-token').value='receiver';let body;
 ctx.fetch=async(url,opts)=>{body=JSON.parse(opts.body);return {status:200,ok:true}};
 await run(ctx,'saveSettings()');assert.equal(body.hub_token,'destination');assert.equal(Object.hasOwn(body,'ingest_token'),false);
});
test('read does not clear action-required status',()=>{
 const {ctx}=context();ctx.item=task('a',{state:'waiting_approval',ui:{read_at:20}});
 assert.equal(run(ctx,'isUnread(item)'),false);assert.equal(run(ctx,'taskGroup(item)'),'attention');
});

test('native archived tasks are hidden while obsolete panel archive flags stay ignored',()=>{
 const {ctx}=context();seed(ctx,[task('a',{ui:{archived:true,pinned:true}}),task('b',{extra:{native_archived:true}})]);
 assert.equal(run(ctx,'visibleTasks().length'),1);assert.equal(run(ctx,'splitList(visibleTasks()).pinned.length'),0);
});
test('single-line row distinguishes devices from Harness without a second metadata row',()=>{
 const {ctx}=context();ctx.item=task('a');seed(ctx,[ctx.item]);
 const row=run(ctx,'rowHtml(item)');assert.match(row,/machine-badge/);assert.match(row,/harness-badge/);assert.match(row,/Harness：Codex/);assert.doesNotMatch(row,/row-meta|row-host|row-project/);
});
test('native pin changes appear on refresh and no independent pin control exists',()=>{
 const {ctx,nodes}=context();seed(ctx,[task('a',{extra:{native_pin:{known:true,pinned:true}}})]);
 assert.equal(run(ctx,'splitList(visibleTasks()).pinned.length'),1);
 run(ctx,'workspace.tasks[0].extra.native_pin.pinned=false;workspace.selected="a";renderDetail()');
 assert.equal(run(ctx,'splitList(visibleTasks()).pinned.length'),0);
 assert.doesNotMatch(nodes['detail-facts'].innerHTML,/setPreference\('pinned'/);
});

test('archive controls are absent while acknowledgement remains available',()=>{
 const {ctx,nodes}=context();seed(ctx,[task('a',{ui:{archived:true}})]);
 run(ctx,'workspace.selected="a";renderDetail()');
 assert.doesNotMatch(html,/filter-archive|icon-archive/);
 assert.doesNotMatch(nodes['detail-extra-actions'].innerHTML,/归档会话|恢复会话|setPreference/);
 assert.match(nodes['detail-extra-actions'].innerHTML,/知道了/);
});
test('native system notifications are separate from the original user message',()=>{
 const {ctx,nodes}=context();seed(ctx,[task('a')]);
 ctx.full=task('a',{extra:{latest_turn:{user:'Original question',notification:'External <script> message',assistant:'New answer'}}});
 run(ctx,'workspace.selected="a";workspace.detail=full;workspace.detailSignature="new";renderDetail()');
 const text=nodes['latest-messages'].innerHTML;
 assert.match(text,/Original question/);assert.match(text,/原生系统通知/);assert.match(text,/External &lt;script&gt; message/);assert.match(text,/New answer/);
 assert.doesNotMatch(text,/<script>/);
});


test('initial auth network failure is unknown and offers a retry without assuming login or setup',async()=>{
 const {ctx,nodes}=context();ctx.fetch=async()=>{throw Error('offline')};
 await run(ctx,'checkAuth()');
 assert.equal(nodes['auth-fields'].hidden,true);assert.equal(nodes['auth-retry'].hidden,false);assert.equal(nodes['auth-retry'].disabled,false);
 assert.match(nodes['auth-err'].textContent,/尚未确认登录状态/);assert.equal(run(ctx,'NEEDS_SETUP'),false);
 ctx.load=async()=>{};ctx.fetch=async()=>({ok:true,json:async()=>({authenticated:true,csrf_token:'test-csrf'})});
 await run(ctx,'checkAuth()');assert.equal(nodes['dash-main'].style.display,'flex');assert.equal(run(ctx,'CSRF_TOKEN'),'test-csrf');
});
test('late auth failure cannot hide a newer successful login state',async()=>{
 const {ctx,nodes}=context();let reject;ctx.fetch=()=>new Promise((resolve,fail)=>reject=fail);
 const older=run(ctx,'checkAuth()');ctx.load=async()=>{};ctx.fetch=async()=>({ok:true,json:async()=>({authenticated:true})});
 await run(ctx,'checkAuth()');reject(Error('late'));await older;assert.equal(nodes['dash-main'].style.display,'flex');
});
test('login prevents duplicate submissions and retains input after network failure',async()=>{
 const {ctx,nodes}=context();run(ctx,'showAuthView(false)');ctx.document.getElementById('auth-pw').value='synthetic password';let reject,calls=0;
 ctx.fetch=()=>{calls++;return new Promise((resolve,fail)=>reject=fail)};
 const pending=run(ctx,'submitAuth({preventDefault(){}})');await run(ctx,'submitAuth({preventDefault(){}})');
 assert.equal(calls,1);assert.equal(nodes['auth-btn'].disabled,true);
 reject(Error('network'));await pending;assert.equal(nodes['auth-btn'].disabled,false);assert.equal(nodes['auth-pw'].value,'synthetic password');assert.match(nodes['auth-err'].textContent,/未收到登录回执/);
});
test('late settings load does not steal navigation or overwrite a newer edited form',async()=>{
 const {ctx,nodes}=context();let resolve;ctx.loadControlDevices=()=>{};ctx.fetch=()=>new Promise(r=>resolve=r);
 const older=run(ctx,'openSettings()');assert.equal(run(ctx,'workspace.page'),'settings');assert.equal(nodes['settings-fields'].disabled,true);
 run(ctx,'navigate("projects")');resolve({ok:true,json:async()=>({machine:'Late server'})});await older;
 assert.equal(run(ctx,'workspace.page'),'projects');assert.equal(nodes['s-machine'],undefined);
 let first;ctx.fetch=()=>new Promise(r=>first=r);const stale=run(ctx,'openSettings()');
 run(ctx,'navigate("devices")');ctx.fetch=async()=>({ok:true,json:async()=>({machine:'Current server'})});await run(ctx,'openSettings()');
 nodes['s-machine'].value='New edit';run(ctx,'settingsInputChanged({target:{id:"s-machine"}})');
 first({ok:true,json:async()=>({machine:'Stale server'})});await stale;
 assert.equal(nodes['s-machine'].value,'New edit');assert.match(nodes['settings-target'].textContent,/Current server/);
 let calls=0;ctx.fetch=()=>{calls++;throw Error('should not reload')};await run(ctx,'openSettings()');
 run(ctx,'navigate("projects")');await run(ctx,'openSettings()');assert.equal(calls,0);assert.equal(nodes['s-machine'].value,'New edit');
});
test('settings read failure offers retry and prevents saving incomplete default fields',async()=>{
 const {ctx,nodes}=context();ctx.fetch=async()=>{throw Error('offline')};await run(ctx,'openSettings()');
 assert.match(nodes['settings-status'].textContent,/读取失败/);assert.equal(nodes['settings-retry'].hidden,false);assert.equal(nodes['settings-save'].disabled,true);
 ctx.loadControlDevices=()=>{};ctx.fetch=async()=>({ok:true,json:async()=>({machine:'Service device'})});await run(ctx,'openSettings(true)');
 assert.equal(nodes['settings-fields'].disabled,false);assert.equal(nodes['settings-save'].disabled,false);assert.match(nodes['settings-target'].textContent,/Service device/);
});
test('settings save clears old success, blocks duplicate requests and keeps edits on failure',async()=>{
 const {ctx,nodes}=context();run(ctx,'settingsView.loaded=true');ctx.document.getElementById('settings-status').textContent='已保存';ctx.document.getElementById('s-machine').value='Draft';let reject,calls=0;
 ctx.fetch=()=>{calls++;return new Promise((resolve,fail)=>reject=fail)};
 const pending=run(ctx,'saveSettings()');assert.match(nodes['settings-status'].textContent,/正在保存/);assert.equal(nodes['settings-save'].disabled,true);
 await run(ctx,'saveSettings()');assert.equal(calls,1);reject(Error('network'));await pending;
 assert.match(nodes['settings-status'].textContent,/未收到保存回执/);assert.equal(nodes['s-machine'].value,'Draft');assert.equal(nodes['settings-save'].disabled,false);
});
test('successful settings save does not mark a later edit as saved',async()=>{
 const {ctx,nodes}=context();run(ctx,'settingsView.loaded=true');ctx.document.getElementById('s-machine').value='Submitted';let resolve,body;
 ctx.fetch=(url,opts)=>{body=JSON.parse(opts.body);return new Promise(r=>resolve=r)};ctx.load=async()=>{};
 const pending=run(ctx,'saveSettings()');nodes['s-machine'].value='Newer draft';run(ctx,'settingsInputChanged({target:{id:"s-machine"}})');
 resolve({ok:true,status:200});await pending;assert.equal(body.machine,'Submitted');assert.equal(nodes['s-machine'].value,'Newer draft');assert.equal(run(ctx,'settingsView.dirty'),true);assert.match(nodes['settings-status'].textContent,/未保存的新修改/);
});


test('overview polls retain focus and scroll when data is unchanged or updated',()=>{
 const {ctx}=context();let replacements=0,markup='old';
 const active={dataset:{focusKey:'device:a'}},restored={dataset:{focusKey:'device:a'},focus(){ctx.document.activeElement=restored}};
 const pane={scrollTop:123,contains(el){return el===active},querySelectorAll(){return [restored]},get innerHTML(){return markup},set innerHTML(value){markup=value;replacements++;ctx.document.activeElement=null}};
 ctx.pane=pane;ctx.document.activeElement=active;
 run(ctx,'replaceOverview(pane,"old")');assert.equal(replacements,0);assert.equal(ctx.document.activeElement,active);
 run(ctx,'replaceOverview(pane,"new activity")');assert.equal(replacements,1);assert.equal(ctx.document.activeElement,restored);assert.equal(pane.scrollTop,123);
});
test('empty collection is distinct from an empty filter result and offers recovery',()=>{
 const {ctx,nodes}=context();run(ctx,'renderList()');assert.match(nodes['session-items'].innerHTML,/尚未发现会话/);assert.doesNotMatch(nodes['session-items'].innerHTML,/筛选条件/);
 seed(ctx,[task('a')]);run(ctx,'workspace.query="no-match";renderList()');assert.match(nodes['session-items'].innerHTML,/没有符合筛选条件/);assert.match(nodes['session-items'].innerHTML,/清除筛选/);
});


test('captured process is folded for all harnesses without hiding final replies',()=>{
 const {ctx}=context();
 for(const tool of ['codex','antigravity','dsh']){
  ctx.item=task('a',{tool,extra:{latest_turn:{user:'Question',assistant:'Native progress',assistant_phase:'commentary'}}});
  const progress=run(ctx,'capturedChatHtml(item,true)');
  assert.match(progress,/<details class="history-tools" data-captured-process>/);
  assert.doesNotMatch(progress,/<details[^>]*\sopen/);
  assert.match(progress,/Native progress/);
  ctx.item.extra.latest_turn.assistant_phase='final';
  const final=run(ctx,'capturedChatHtml(item,true)');
  assert.doesNotMatch(final,/<details/);assert.match(final,/Native progress/);
 }
});
test('captured process expansion survives updates only within the same native turn',()=>{
 const {ctx,nodes}=context();
 run(ctx,"document.getElementById('latest-messages')");
 const region=nodes['latest-messages'],process={open:false};region.querySelector=()=>process;
 ctx.item=task('a',{turn_started_at:10,extra:{latest_turn:{user:'Question',assistant:'Progress',assistant_phase:'commentary'}}});
 run(ctx,'updateCapturedChat(item,true)');assert.equal(process.open,false);
 process.open=true;ctx.item.extra.latest_turn.assistant='New progress';
 run(ctx,'updateCapturedChat(item,true)');assert.equal(process.open,true);
 ctx.item.turn_started_at=11;
 run(ctx,'updateCapturedChat(item,true)');assert.equal(process.open,false);
});
