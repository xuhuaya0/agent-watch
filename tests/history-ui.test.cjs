const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('fs'),vm=require('vm');
function setup(){
 const elements={'session-history':{innerHTML:'',dataset:{}},'latest-messages':{hidden:false},'session-detail':{scrollHeight:100,scrollTop:20}};
 const ctx=vm.createContext({workspace:{selected:'a',page:'sessions',tasks:[{ui_key:'a',tool:'codex',activity_at:1}]},document:{getElementById:id=>elements[id]},taskId:t=>t.ui_key,activityAt:t=>t.activity_at,esc:s=>String(s??'').replaceAll('<','&lt;').replaceAll('"','&quot;'),markdown:s=>String(s).replaceAll('<','&lt;'),setTimeout,Date,showAuthView(){}});
 vm.runInContext(fs.readFileSync('static/history.js','utf8'),ctx);return{ctx,elements,run:s=>vm.runInContext(s,ctx)};
}
test('history escapes content and never renders a reasoning role',()=>{
 const {run}=setup();const html=run(`historyHtml({messages:[{role:'user',label:'<img>',text:'<script>',id:'1',timestamp:''}],loaded:true,busy:false})`);
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
test('all three harnesses load older history through the same reader',async()=>{
 for(const tool of ['codex','antigravity','dsh']) {
  const {ctx,elements,run}=setup();run(`workspace.tasks[0].tool=${JSON.stringify(tool)}`);
  ctx.apiFetch=async()=>({ok:true,json:async()=>({ready:true,messages:[{id:'1',role:'assistant',text:'Earlier answer'}],next_cursor:'older'})});
  await run('loadHistory(true)');assert.equal(elements['latest-messages'].hidden,true);
  assert.match(elements['session-history'].innerHTML,/Earlier answer/);
  assert.match(elements['session-history'].innerHTML,/加载更早的记录/);
 }
});
