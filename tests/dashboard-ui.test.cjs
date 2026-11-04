const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'../static/index.html'),'utf8');
const inline=html.match(/<script>([\s\S]*?)<\/script>/)[1].replace(/checkAuth\(\);\s*$/,'');
const app=fs.readFileSync(path.join(__dirname,'../static/workspace.js'),'utf8');
function context(){
 const nodes={};
 const node=()=>({value:'',checked:false,hidden:false,style:{},dataset:{},scrollTop:0,innerHTML:'',textContent:'',classList:{add(){},remove(){},toggle(){}},querySelectorAll(){return []},querySelector(){return null},contains(){return false},setAttribute(){},focus(){},scrollIntoView(){}});
 const document={activeElement:null,getElementById(id){return nodes[id] ||=node()},querySelectorAll(){return []},querySelector(){return null}};
 const ctx=vm.createContext({document,setInterval(){},clearInterval(){},setTimeout(){},clearTimeout(){},console,location:{origin:'http://example.test'},window:{},navigator:{}});
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
test('full detail and older collector summaries both render honestly',()=>{
 const {ctx,nodes}=context();seed(ctx,[task('a',{prompt:'Summary prompt',detail:'Short result'})]);
 ctx.full=task('a',{extra:{latest_turn:{user:'Full question',assistant:'Full result',truncated:false}}});
 run(ctx,'workspace.selected="a";workspace.detail=full;workspace.detailSignature="v1";renderDetail()');
 assert.match(nodes['session-detail'].innerHTML,/Full question/);assert.match(nodes['session-detail'].innerHTML,/Full result/);
 run(ctx,'workspace.detail=null;workspace.detailSignature="";renderDetail()');assert.match(nodes['session-detail'].innerHTML,/最新结果摘要/);assert.match(nodes['session-detail'].innerHTML,/Short result/);
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
 assert.doesNotMatch(nodes['session-detail'].innerHTML,/setPreference\('pinned'/);
});

test('archive controls are absent while acknowledgement remains available',()=>{
 const {ctx,nodes}=context();seed(ctx,[task('a',{ui:{archived:true}})]);
 run(ctx,'workspace.selected="a";renderDetail()');
 assert.doesNotMatch(html,/filter-archive|icon-archive/);
 assert.doesNotMatch(nodes['session-detail'].innerHTML,/归档会话|恢复会话|setPreference/);
 assert.match(nodes['session-detail'].innerHTML,/知道了/);
});
test('native system notifications are separate from the original user message',()=>{
 const {ctx,nodes}=context();seed(ctx,[task('a')]);
 ctx.full=task('a',{extra:{latest_turn:{user:'Original question',notification:'External <script> message',assistant:'New answer'}}});
 run(ctx,'workspace.selected="a";workspace.detail=full;workspace.detailSignature="new";renderDetail()');
 const text=nodes['session-detail'].innerHTML;
 assert.match(text,/Original question/);assert.match(text,/最近外部消息 · 原生系统通知/);assert.match(text,/External &lt;script&gt; message/);assert.match(text,/New answer/);
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
