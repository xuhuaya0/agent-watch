const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('fs'),vm=require('vm');
class HistoryElement {
 constructor(tag='div') {this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.html='';this.open=false;}
 get innerHTML(){return this.html+this.children.map(node=>node.innerHTML).join('');}
 set innerHTML(value){this.replaceChildren();this.html=value;}
 setAttribute(name,value){this[name]=value;}
 querySelector(selector){return this.children.find(element=>'#'+element.id===selector)||null;}
 get firstElementChild(){return this.children[0]||null;}
 get lastElementChild(){return this.children.at(-1)||null;}
 get nextElementSibling(){return this.parentElement?.children[this.parentElement.children.indexOf(this)+1]||null;}
 get tools(){return this.children.flatMap(node=>node.tagName==='DETAILS'?[node]:node.tools);}
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
 const ctx=vm.createContext({workspace:{selected:'a',page:'sessions',tasks:[{ui_key:'a',tool:'codex',activity_at:1}]},document:{getElementById:id=>elements[id],createElement:tag=>new HistoryElement(tag)},taskId:t=>t.ui_key,activityAt:t=>t.activity_at,esc:s=>String(s??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;'),markdown:s=>String(s).replaceAll('<','&lt;'),icon:()=>'<svg aria-hidden="true"></svg>',setTimeout,Date,showAuthView(){}});
 vm.runInContext(fs.readFileSync('static/history.js','utf8'),ctx);return{ctx,elements,run:s=>vm.runInContext(s,ctx)};
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
  ctx.apiFetch=async()=>({ok:true,json:async()=>({ready:true,messages:[{id:'1',role:'assistant',text:'Earlier answer'}],next_cursor:'older'})});
  await run('loadHistory(true)');assert.equal(elements['latest-messages'].hidden,true);
  assert.match(elements['session-history'].innerHTML,/Earlier answer/);
  assert.match(elements['session-history'].innerHTML,/加载更早的记录/);
 }
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
 ctx.opened=elements['session-history'].tools[0];ctx.opened.open=true;run('rememberHistoryTool(opened)');
 run(`workspace.selected='b';historyState('b').messages=[{id:'same',role:'tool',text:'B'}];renderHistory()`);
 assert.equal(elements['session-history'].tools[0].open,false);
 run(`workspace.selected='a';renderHistory()`);assert.equal(elements['session-history'].tools[0].open,true);
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
 ctx.apiFetch=async()=>({ok:true,json:async()=>({ready:true,messages:[{id:'latest',role:'assistant',text:'latest'}],next_cursor:'new'})});
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
