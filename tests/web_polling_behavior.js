const vm = require('node:vm');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const script = fs.readFileSync('src/automation_web.py','utf8').split('SCRIPT = """')[1].split('</script>')[0].replace(/^<script[^>]*>/,'');
(async function () {
  const timers = new Map(), events = {}, requests = [];
  let serial = 0, parsed = 0, replacements = 0;
  const connection = {textContent:''};
  const child = {outerHTML:'same',getAttribute(){return 'run';},contains(){return false;},replaceWith(){replacements++;}};
  const root = {children:[child],querySelector(){return child;}};
  const panel = {firstElementChild:root,querySelectorAll(){return [];},querySelector(){return null;},addEventListener(){},getAttribute(){return '1';}};
  const incoming = {children:[child],getAttribute(){return '1';}};
  const document = {hidden:false,getElementById(id){return id === 'panel' ? panel : connection;},addEventListener(name, fn){events[name]=fn;},createElement(){parsed++;return {content:{firstElementChild:incoming}};}};
  const context = {document,location:{hash:'#run'},window:{addEventListener(name,fn){events[name]=fn;},matchMedia(){return {};},scrollTo(){}},sessionStorage:{getItem(){return null;},removeItem(){}},performance:{now(){return 0;}},requestAnimationFrame(){},setTimeout(fn,ms){timers.set(++serial,{fn,ms});return serial;},clearTimeout(id){timers.delete(id);},fetch(url){return new Promise((resolve,reject)=>requests.push({url,resolve,reject}));},AbortController,encodeURIComponent,console};
  const flush = async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
  const run = ()=>{const [id,entry]=[...timers].sort((a,b)=>a[1].ms-b[1].ms)[0];timers.delete(id);entry.fn();};
  vm.runInNewContext(script.replace('})();', 'window.testUpdate = updateSection; })();'), context);
  run();
  assert.equal(requests.length,1);
  assert.match(requests[0].url,/tab=run/);
  assert.equal(timers.size,1,'only request timeout while request pending');
  requests[0].reject(new Error('offline'));
  await flush();
  assert.match(connection.textContent,/Disconnected/);
  document.hidden=true;
  events.visibilitychange();
  assert.equal(timers.size,0,'hidden page must not poll');
  document.hidden=false;events.visibilitychange();run();
  context.location.hash='#plan';events.hashchange();
  requests[1].resolve({ok:true,text:async()=>'<stale>'});await flush();
  assert.equal(parsed,0,'discard response from earlier tab');
  run();assert.match(requests[2].url,/tab=plan/);
  child.getAttribute=()=> 'plan';
  requests[2].resolve({ok:true,text:async()=>'<fresh>'});await flush();
  assert.equal(parsed,1);
  assert.equal(replacements,0,'unchanged section keeps its DOM');
  assert.equal(connection.textContent,'Connected');
  incoming.children=[{...child,outerHTML:'changed'}];run();
  requests[3].resolve({ok:true,text:async()=>'<changed>'});await flush();
  assert.equal(replacements,1,'changed section is updated');
  // Cache entries below a replaced container cannot describe the new subtree.
  function leaf(value) {
    return {outerHTML:value,getAttribute(){return '';},replaceWith(fresh){this.outerHTML=fresh.outerHTML;}};
  }
  function group(parts) {
    return {children:parts,get outerHTML(){return 'group:' + this.children.map(p=>p.outerHTML).join(',');},
      getAttribute(name){return name==='class'?'boxes':'';},replaceWith(fresh){this.children=fresh.children;}};
  }
  const tree=group([leaf('A')]);
  context.window.testUpdate(tree,group([leaf('B')]),'tree');
  context.window.testUpdate(tree,group([leaf('C'),leaf('D')]),'tree');
  context.window.testUpdate(tree,group([leaf('B'),leaf('D')]),'tree');
  assert.equal(tree.children[0].outerHTML,'B','replacement invalidates descendant markup cache');
  timers.clear();requests.length=0;
  let loaded=false, removed=false, scrollY=null;
  const detail={open:false,getAttribute(){return 'phase';}};
  context.location.hash='#plan';
  context.sessionStorage={getItem(){return JSON.stringify({y:321,open:{phase:true}});},removeItem(){removed=true;}};
  context.window.scrollTo=(x,y)=>{scrollY=y;};
  panel.querySelectorAll=(selector)=>selector==='details[data-key]' && loaded?[detail]:[];
  document.createElement=()=>{loaded=true;return {content:{firstElementChild:incoming}};};
  vm.runInNewContext(script,context);
  assert.equal(removed,false,'saved state retained until lazy tab arrives');
  run();requests[0].reject(new Error('offline'));await flush();
  assert.equal(removed,false,'failed polling retains saved state');
  run();requests[1].resolve({ok:true,text:async()=>'<plan>'});await flush();
  assert.equal(detail.open,true,'lazy plan restores saved toggle');
  assert.equal(scrollY,321,'lazy plan restores saved scroll');
  assert.equal(removed,true,'successful restoration consumes saved state');
  console.log('single-flight, hidden, failure, stale-response, unchanged/changed DOM behavior passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
