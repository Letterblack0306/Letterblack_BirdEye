"use strict";
const $ = id => document.getElementById(id);
const icons = {
  overview:'<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  workspaces:'<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v10H3z"/><path d="M3 10h18"/>',
  tools:'<path d="m12 2 2.2 4.2 4.8.8-3.5 3.5.8 4.8L12 13l-4.3 2.3.8-4.8L5 7l4.8-.8z" transform="translate(0 2)"/><path d="M7 21h10"/>',
  skills:'<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v17H7a3 3 0 0 0-3 1z"/><path d="M4 5v14a3 3 0 0 1 3-3h13"/><path d="M9 8h7M9 11h5"/>',
  activity:'<path d="M3 12h4l3-7 4 14 3-7h4"/><circle cx="12" cy="12" r="10" stroke-opacity=".18"/>',
  database:'<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14c0 4 18 4 18 0V5M3 12c0 4 18 4 18 0"/>',
  connections:'<circle cx="5" cy="12" r="2"/><circle cx="19" cy="5" r="2"/><circle cx="19" cy="19" r="2"/><path d="m7 11 10-5M7 13l10 5"/>',
  settings:'<path d="M3 6h18M3 12h18M3 18h18"/><circle cx="8" cy="6" r="2" fill="#152029"/><circle cx="16" cy="12" r="2" fill="#152029"/><circle cx="11" cy="18" r="2" fill="#152029"/>',
  refresh:'<path d="M20 5v5h-5M4 19v-5h5"/><path d="M5.5 9A7 7 0 0 1 18.7 6.4L20 10M4 14l1.3 3.6A7 7 0 0 0 18.5 15"/>',
  menu:'<path d="M4 7h16M4 12h16M4 17h16"/>',
  search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m15.5 15.5 5 5"/>',
  "arrow-left":'<path d="M20 12H4m7-7-7 7 7 7"/>',
  folder:'<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v10H3z"/>',
  file:'<path d="M6 2h8l4 4v16H6zM14 2v5h4"/>',
  arrow:'<path d="M7 17 17 7M9 7h8v8"/>',
  close:'<path d="M5 5 19 19M19 5 5 19"/>',
  check:'<path d="m4 12 5 5L20 6"/>',
  info:'<circle cx="12" cy="12" r="10"/><path d="M12 11v5M12 7.5v.5"/>',
  shield:'<path d="M12 2 4 5v6c0 6 4 9 8 11 4-2 8-5 8-11V5z"/><path d="m9 12 2 2 4-4"/>',
  minus:'<path d="M5 12h14"/>',
  git:'<circle cx="7" cy="6" r="2"/><circle cx="17" cy="9" r="2"/><circle cx="7" cy="18" r="2"/><path d="M7 8v8M9 6h3a5 5 0 0 1 5 5"/>'
};
function icon(name){
  const wrapper=document.createElement('span');wrapper.className='inline-icon';
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
  svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('fill','none');
  svg.setAttribute('stroke','currentColor');svg.setAttribute('stroke-linecap','round');
  svg.setAttribute('stroke-linejoin','round');svg.innerHTML=icons[name]||icons.info;
  wrapper.append(svg);return wrapper;
}
function el(tag,cls,txt){
  const x=document.createElement(tag);
  if(cls)x.className=cls;
  if(txt!==undefined && txt!==null)x.textContent=String(txt);
  return x;
}
function append(host,...kids){for(const k of kids){if(k)host.append(k)}return host}
function mono(txt){return el('span','mono',txt)}
function pill(value,type='neutral'){return el('span','pill '+type,String(value||'UNVERIFIED'))}
function panel(name,subtitle,meta){
  const outer=el('section','surface fade-in'),head=el('header','surface-head'),box=el('div');
  append(box,el('h2','',name),el('p','',subtitle||''));
  append(head,box,meta?el('span','head-meta',meta):null);
  const body=el('div','surface-body');outer.append(head,body);return {outer,body,head};
}
function row(name,detail,badgeText,badgeType='neutral',glyph='folder'){
  const r=el('div','info-row'),left=el('div','info-row-main'),i=el('div','row-icon');
  i.append(icon(glyph));const b=el('div');append(b,el('div','row-name',name),el('div','row-detail',detail));
  append(left,i,b);append(r,left,badgeText?pill(badgeText,badgeType):null);return r;
}
function empty(target,title,description){
  const d=el('div','empty');append(d,el('strong','',title),el('p','',description));target.replaceChildren(d);
}
function fmt(n){return Number(n||0).toLocaleString('en-US')}
function date(s){if(!s)return 'Time unavailable';const v=new Date(s);return isNaN(v.getTime())?String(s):v.toLocaleString()}
function short(s,len=94){s=String(s||'');return s.length>len?s.slice(0,len-1)+'…':s}
function bytes(n){if(n==null)return 'Not measured';return Number(n)>=1073741824?(n/1073741824).toFixed(2)+' GB':Number(n)>=1048576?(n/1048576).toFixed(1)+' MB':Number(n)>=1024?(n/1024).toFixed(1)+' KB':n+' B'}
let toolCategorySelection='All';
let model=null,view='overview',workspaceSelection='__birdeye__',receiptSelection=null,loadCounter=0,toastTimer=0;
const pages={
  overview:['Overview','SYSTEM OVERVIEW','Operational intelligence from the canonical local gateway.'],
  workspaces:['Workspace Intelligence','SOURCE & CONTEXT','Understand each workspace on its own terms, from live files and revision evidence.'],
  tools:['MCP Tool Registry','DISCOVER CAPABILITIES','Real registered tools, inputs and execution surfaces.'],
  skills:['Skills & Knowledge','KNOWLEDGE LAYER','Find relevant instruction packages. Discovery does not activate skills.'],
  planning:['Task Planning','PLAN & VERIFY','Prepare workspace-specific tasks and verification for your agent.'],
  git:['Git History','VISUAL COMMIT GRAPH','Trace local branches, merges, commits and changed files without leaving BirdEye.'],
  execution:['Execution Ledger','EVIDENCE & RECEIPTS','Machine-qualified command history with source receipts and runtime events.'],
  data:['Memory & Data','DATA LINEAGE','Know which source owns each record and where query projections live.'],
  connections:['Connections','RUNTIME SURFACES','Live endpoints, local application ownership and connector boundaries.'],
  settings:['Configuration','WORKSPACE SETTINGS','Manage indexed roots without overriding workspace-owned execution policies.']
};
let content=$('content');
function error(message){
  const box=$(panelOpen?'panel-error':'error');box.hidden=!message;box.textContent=message||'';
}
function showToast(message){
  const t=$('toast');t.textContent=message;t.hidden=false;
  clearTimeout(toastTimer);toastTimer=setTimeout(()=>{t.hidden=true},4200);
}
async function get(url,opt){
  const r=await fetch(url,Object.assign({cache:'no-store',credentials:'same-origin'},opt||{}));
  let data;
  try{data=await r.json()}catch(e){throw new Error('Invalid gateway response (HTTP '+r.status+')')}
  if(!r.ok||data.ok===false)throw new Error(data.error||data.message||'HTTP '+r.status);
  return data;
}
function loadText(target){target.replaceChildren(append(el('div','loading-state'),el('span','loading-ring'),el('span','','Loading live evidence...')))}
function addStat(host,name,value,detail,accent=false){
  const target={'HTTP MCP tools':'tools','Indexed skills':'skills',
    'Configured roots':'workspaces','EYES generation lag':'data'}[name];
  const s=el(target?'button':'div','stat'+(target?' clickable-stat':''));
  append(s,el('div','label',name),el('div','value'+(accent?' accent':' muted-value'),value),
    el('div','detail',detail));
  if(target){
    s.type='button';s.dataset.panelView=target;
    s.title='Open '+pages[target][0]+' in floating panel';
    s.onclick=()=>selectView(target,s);
  }
  host.append(s);
}
function note(text,warn=false){
  const box=el('div','notice'+(warn?' warning':''));append(box,icon(warn?'info':'shield'),el('div','',text));return box;
}
let panelOpen=false,closingPanel=false,panelOrigin=null,lastPanelFocus=null,panelAnimation=null;
function anchorFor(viewId, explicit){
  if(explicit && explicit.isConnected)return explicit;
  return document.querySelector('#content [data-panel-view="'+viewId+'"]') ||
    document.querySelector('#content .network-core') || document.querySelector('#content .session-bar');
}
function cardGeometry(panel,origin){
  const rect=panel.getBoundingClientRect();
  const card=origin && origin.isConnected?origin.getBoundingClientRect():
    {left:window.innerWidth/2-70,top:window.innerHeight/2-45,width:140,height:90};
  return {
    x:(card.left+card.width/2)-(rect.left+rect.width/2),
    y:(card.top+card.height/2)-(rect.top+rect.height/2),
    sx:Math.max(.12,Math.min(.96,card.width/rect.width)),
    sy:Math.max(.08,Math.min(.9,card.height/rect.height))
  };
}
function allowsMotion(){
  return !document.documentElement.classList.contains('reduce-motion') &&
    !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}
function movePanel(toCard){
  const panel=$('detail-panel');
  if(panelAnimation){panelAnimation.cancel();panelAnimation=null}
  if(!allowsMotion() || typeof panel.animate!=='function')return Promise.resolve();
  const g=cardGeometry(panel,panelOrigin);
  const atCard={transform:'translate('+g.x+'px,'+g.y+'px) scale('+g.sx+','+g.sy+')',opacity:.3};
  const open={transform:'translate(0px,0px) scale(1,1)',opacity:1};
  panelAnimation=panel.animate(toCard?[open,atCard]:[atCard,open],{
    duration:toCard?340:440,easing:toCard?'cubic-bezier(.55,.05,.45,.96)':'cubic-bezier(.19,1,.28,1)',
    fill:'both'
  });
  return panelAnimation.finished.catch(()=>{}).finally(()=>{panelAnimation=null});
}
function panelCopy(next){
  $('crumb').textContent=pages[next][0];
  $('panel-title').textContent=pages[next][0];
  $('panel-eyebrow').textContent=pages[next][1];
  $('panel-subtitle').textContent=pages[next][2];
}
function openFloatingPanel(next,origin){
  if(!pages[next] || next==='overview')return;
  if(!model){showToast('Connect to BirdEye before opening a capability');return}
  const fresh=!panelOpen;
  if(closingPanel){closingPanel=false}
  if(fresh){
    panelOrigin=anchorFor(next,origin);
    lastPanelFocus=document.activeElement;
    $('detail-layer').hidden=false;
    document.body.classList.add('panel-open');
    document.getElementById('main').inert=true;
    document.querySelector('.topbar').inert=true;
  }else if(origin && origin.isConnected){
    panelOrigin=origin;
  }
  panelOpen=true;view=next;content=$('panel-content');
  panelCopy(next);error('');render();
  $('panel-scroll').scrollTop=0;
  $('detail-layer').classList.add('visible');
  $('detail-panel').focus({preventScroll:true});
  if(fresh)requestAnimationFrame(()=>{movePanel(false)});
  if(location.hash!=='#'+next)history.replaceState(null,'','#'+next);
}
function closeFloatingPanel(){
  if(!panelOpen || closingPanel)return;
  closingPanel=true;panelOpen=false;
  loadCounter++; // Invalidate pending API loads targeting the closing surface.
  $('detail-layer').classList.remove('visible');
  const toFocus=panelOrigin;
  const finish=()=>{
    if(!closingPanel)return;
    $('detail-layer').hidden=true;
    $('panel-content').replaceChildren();
    document.body.classList.remove('panel-open');
    document.getElementById('main').inert=false;
    document.querySelector('.topbar').inert=false;
    view='overview';content=$('content');
    $('crumb').textContent='Overview';
    closingPanel=false;panelOrigin=null;
    render();
    const focus=(toFocus&&toFocus.isConnected?toFocus:lastPanelFocus);
    if(focus&&focus.isConnected&&typeof focus.focus==='function')focus.focus({preventScroll:true});
    if(location.hash!=='#overview')history.replaceState(null,'','#overview');
  };
  movePanel(true).then(finish);
}
function selectView(next,origin){
  if(!pages[next])return;
  if(next==='overview'){if(panelOpen)closeFloatingPanel();else{
    view='overview';content=$('content');render();
    if(location.hash!=='#overview')history.replaceState(null,'','#overview');
  }return}
  openFloatingPanel(next,origin);
}
function installFloatingPanel(){
  $('panel-close').onclick=closeFloatingPanel;
  $('panel-minimize').onclick=closeFloatingPanel;
  $('panel-scrim').onclick=closeFloatingPanel;
  document.addEventListener('keydown',event=>{
    if(!panelOpen)return;
    if(event.key==='Escape'){
      if(document.querySelector('.command-dialog[open]'))return;
      event.preventDefault();closeFloatingPanel();return;
    }
    if(event.key!=='Tab')return;
    const focusables=[...$('detail-panel').querySelectorAll(
      'a[href],button:not([disabled]),input:not([disabled]),textarea:not([disabled]),select:not([disabled]),[tabindex]:not([tabindex="-1"])'
    )].filter(x=>x.getClientRects().length>0);
    if(!focusables.length){event.preventDefault();$('detail-panel').focus();return}
    const first=focusables[0],last=focusables[focusables.length-1];
    if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus()}
    else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus()}
  });
}

function refreshButtonState(busy){
  const b=$('refresh');b.disabled=busy;
  b.setAttribute('aria-busy',String(busy));
}
async function refresh(){
  refreshButtonState(true);error('');
  try{
    model=await get('/control/api/snapshot');
    document.documentElement.classList.remove('stale-data');
    $('connection-state').classList.remove('offline');$('connection-state').classList.add('online');
    $('connection-state').replaceChildren(el('span','dot'),document.createTextNode(' Online'));
    $('checked-time').textContent='CHECKED '+new Date(model.observed_at).toLocaleTimeString();
    $('side-host').textContent=model.host||'Local machine';
    $('footer-host').textContent=(model.host||'BirdEye')+' / '+model.tools.length+' tools';
    const deepLink=location.hash.slice(1);
    if(!panelOpen && pages[deepLink] && deepLink!=='overview'){
      view='overview';content=$('content');render();
      openFloatingPanel(deepLink);
    }else render();
  }catch(e){
    $('connection-state').classList.remove('online');$('connection-state').classList.add('offline');
    $('connection-state').replaceChildren(el('span','dot'),document.createTextNode(' Unverified'));
    document.documentElement.classList.add('stale-data');
    if(model)$('checked-time').textContent='STALE · last observation '+date(model.observed_at);
    error(e.message);if(!model)empty(content,'Gateway unavailable','The existing service must respond before its state can be displayed.');
  }finally{refreshButtonState(false)}
}
function render(){
  loadCounter++;
  content.replaceChildren();if(!model){empty(content,'Gateway not connected','Refresh to load current state from BirdEye.');return}
  if(view==='overview')overview();
  else if(view==='workspaces')workspaces();
  else if(view==='tools')toolsView();
  else if(view==='skills')skillsView();
  else if(view==='planning')planningView();
  else if(view==='git')gitView();
  else if(view==='execution')executionView();
  else if(view==='data')dataView();
  else if(view==='connections')connectionsView();
  else if(view==='settings')settingsView();
}
function allRoots(){return model.roots.roots||[]}
function domains(){return ((model.eyes||{}).eyes||{}).domains||{}}
function diagnostic(target,{reason,action,evidence,recovered=false}={}){
  if(!reason&&!action&&!evidence)return;
  const wrap=el('div','diagnostic-detail'+(recovered?' recovered':''));
  if(reason)wrap.append(el('p','diagnostic-reason',(recovered?'Previous issue: ':'Reason: ')+reason));
  if(action)wrap.append(el('p','diagnostic-action','Next action: '+action));
  if(evidence)wrap.append(el('small','diagnostic-evidence','Evidence: '+evidence));
  target.append(wrap);
}
function serviceStatus(box,name,probe){
  if(probe.ok)return;
  diagnostic(box,{
    reason:probe.reason||'Local health endpoint did not respond',
    action:probe.next_action||'Inspect the existing process, service and logs',
    evidence:name+' / local endpoint observation'
  });
}

function capabilityNetwork(){
  const areas=[
    {id:'workspaces',name:'Workspaces',desc:fmt(allRoots().length)+' source roots',glyph:'workspaces'},
    {id:'tools',name:'MCP Tools',desc:fmt(model.tools.length)+' live capabilities',glyph:'tools'},
    {id:'skills',name:'Skills & Knowledge',desc:fmt(model.skills.files_tracked||0)+' indexed',glyph:'skills'},
    {id:'planning',name:'Task Planning',desc:'Agent-owned workflow',glyph:'activity'},
    {id:'git',name:'Git History',desc:'Branches & commits',glyph:'git'},
    {id:'execution',name:'Executions',desc:'Receipts & evidence',glyph:'activity'},
    {id:'data',name:'Memory & Data',desc:'Storage provenance',glyph:'database'},
    {id:'connections',name:'Connections',desc:'Local runtime targets',glyph:'connections'},
    {id:'settings',name:'Configuration',desc:'Workspace settings',glyph:'settings'}
  ];
  const section=el('section','capability-network fade-in');
  // Ambient dot-grid interaction is bounded to the overview and stops on pointer exit.
  // CSS supplies the static treatment for touch devices and reduced-motion users.
  if(window.matchMedia('(hover: hover) and (pointer: fine) and (prefers-reduced-motion: no-preference)').matches){
    let frame=0;
    section.addEventListener('pointermove',event=>{
      if(frame)return;
      const x=event.clientX,y=event.clientY;
      frame=requestAnimationFrame(()=>{
        frame=0;
        const r=section.getBoundingClientRect();
        section.style.setProperty('--motion-x',((x-r.left)/r.width*100).toFixed(2)+'%');
        section.style.setProperty('--motion-y',((y-r.top)/r.height*100).toFixed(2)+'%');
        section.style.setProperty('--motion-strength','1');
      });
    },{passive:true});
    section.addEventListener('pointerleave',()=>{
      if(frame){cancelAnimationFrame(frame);frame=0}
      section.style.setProperty('--motion-strength','0');
    });
  }

  section.setAttribute('aria-label','BirdEye operational areas');
  section.append(el('div','network-caption','CHOOSE A CARD · OPEN FLOATING WORKSPACE'));
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
  svg.setAttribute('viewBox','0 0 1000 540');svg.setAttribute('preserveAspectRatio','none');
  svg.setAttribute('aria-hidden','true');svg.classList.add('network-lines');
  const firstSide=Math.ceil(areas.length/2);
  for(const [i,area] of areas.entries()){
    const left=i<firstSide,j=left?i:i-firstSide;
    const count=left?firstSide:areas.length-firstSide;
    const x=left?16:84,y=13+(j*(74/Math.max(1,count-1)));
    const path=document.createElementNS('http://www.w3.org/2000/svg','path');
    path.setAttribute('d','M500 270 C'+(left?310:690)+' 270 '+(left?340:660)+' '+(y*5.4)+' '+(x*10)+' '+(y*5.4));
    svg.append(path);
    const button=el('button','network-node capability-card');
    button.type='button';button.style.left=x+'%';button.style.top=y+'%';
    button.dataset.panelView=area.id;
    button.title='Open '+area.name+' in a floating panel';
    const info=el('span','card-labels');
    append(info,el('strong','',area.name),el('small','',area.desc));
    append(button,icon(area.glyph),info,el('i','network-port'));
    button.onclick=()=>selectView(area.id,button);
    section.append(button);
  }
  section.prepend(svg);
  const core=el('button','network-core');
  core.type='button';core.dataset.panelView='workspaces';
  core.title='Inspect the canonical BirdEye gateway';
  core.onclick=()=>{workspaceSelection='__birdeye__';selectView('workspaces',core)};
  append(core,icon('connections'),el('strong','','BIRDEYE'),el('small','',model.host),
    el('span','network-observed','OBSERVED '+new Date(model.observed_at).toLocaleTimeString()));
  section.append(core);
  return section;
}

function overview(){

  const hero=el('section','session-bar fade-in'),identity=el('div','session-identity'),glyph=el('div','brandmark');glyph.append(icon('connections'));
  const copy=el('div');append(copy,el('h2','',model.host||'Local gateway'),el('p','','Local source · observed '+date(model.observed_at)));
  append(identity,glyph,copy);
  const controls=el('div','session-controls'),target=el('select','field');target.setAttribute('aria-label','Active workspace');
  for(const item of [{id:'__birdeye__'}].concat(allRoots())){const option=el('option','',item.id==='__birdeye__'?'BirdEye gateway':item.id);option.value=item.id;target.append(option)}
  target.value=workspaceSelection;target.onchange=()=>{workspaceSelection=target.value};
  const inspect=el('button','primary-button','Inspect workspace');inspect.type='button';inspect.onclick=()=>{workspaceSelection=target.value;selectView('workspaces')};
  append(controls,fieldLabel('Workspace',target),inspect);append(hero,identity,controls);content.append(hero);
  content.append(capabilityNetwork());
  const stats=el('div','stats-grid'),lag=Math.max(0,...Object.values(domains()).map(x=>Number(x.lag||0)));
  addStat(stats,'HTTP MCP tools',fmt(model.tools.length),'LIVE REGISTRY',true);
  addStat(stats,'Indexed skills',fmt((model.skills||{}).files_tracked||0),'SHARED SKILLS');
  addStat(stats,'Configured roots',fmt(allRoots().length),'SOURCE REGISTRY');
  addStat(stats,'EYES generation lag',fmt(lag),Object.keys(domains()).length+' DATA DOMAINS',lag===0);
  content.append(stats);
  const cols=el('div','columns'),roots=panel('Workspace sources','Registered roots and current index observations','SOURCE: EYES');
  for(const item of allRoots().slice(0,8)){
    const completed=item.last_completed_at?' · Indexed '+date(item.last_completed_at):' · No completed index';
    const line=row(item.id,fmt(item.indexed_files)+' indexed · '+short(item.path,68)+completed,
      item.status||'UNVERIFIED',item.status==='CURRENT'?'good':'warn','folder');
    const previous=item.last_failure,active=item.status==='FAILED';
    const problem=active || !['CURRENT','EXCLUDED'].includes(item.status);
    if(problem || item.recovered_after_failure){
      const cause=active?(item.last_error||previous?.error||'No recorded index error'):
        item.status==='UNAVAILABLE'?'Source directory does not exist or is inaccessible':
        item.status==='PARTIAL'?item.unresolved_files?
          fmt(item.unresolved_files)+' unreadable or size-limited files':
          'Index run incomplete: '+String(item.last_run_status||'no completed run'):
        item.status==='INDEXING'?'An index operation started but is not yet complete':
        previous?.error||'No recorded reason';
      const msg=problem?'Index issue: '+cause:'Recovered from previous issue: '+cause;
      const info=line.querySelector('.row-detail');
      if(info)info.after(el('div','root-failure-reason',msg));
      const inspect=el('button','root-inspect-link','Inspect details');
      inspect.type='button';inspect.onclick=()=>{workspaceSelection=item.id;selectView('workspaces')};
      const main=line.querySelector('.info-row-main');if(main)main.append(inspect);
    }
    roots.body.append(line);
  }
  const health=panel('System integrity','Canonical generations and query projection state','LIVE');
  for(const [name,entry] of Object.entries(domains())){
    const healthy=entry.lag===0&&!entry.reason;
    const line=row(name+' index','Generation '+fmt(entry.applied_generation)+' / '+fmt(entry.canonical_generation),
      healthy?'IN SYNC':entry.reason?'ISSUE':'LAG '+entry.lag,healthy?'good':'warn','database');
    if(!healthy)diagnostic(line,{reason:entry.reason||('Projection is behind by '+fmt(entry.lag)+' generations'),
      action:entry.journal_replayable?'Check the projection owner and replay/rebuild through the existing projector':'Inspect projection source and owner before recovery',
      evidence:entry.projection_owner||'EYES projection status'});
    health.body.append(line);
  }
  const line=el('p','source-foot','Memory remains historical evidence. Runtime and source observations take precedence.');
  health.body.append(line);append(cols,roots.outer,health.outer);content.append(cols);
  const activity=panel('Recent local executions','Latest persisted execution receipts in the canonical BirdEye state','EXECUTION EVIDENCE');
  content.append(activity.outer);loadText(activity.body);
  const token=loadCounter;
  get('/control/api/history?root=__birdeye__').then(data=>{
    if(token!==loadCounter)return;
    activity.body.replaceChildren();
    if(!data.records.length){empty(activity.body,'No recent matching receipts','This view checks the recent receipt window only.');return}
    for(const item of data.records.slice(0,5)){
      const btn=el('button','receipt-item');btn.type='button';
      const left=el('div');append(left,el('div','name',short((item.argv||[]).join(' '),105)),el('div','date',date(item.completed_at)+' · '+item.execution_id));
      append(btn,left,pill(item.overall_evidence||'OBSERVED',item.exit_code===0?'good':'warn'));
      btn.addEventListener('click',()=>{receiptSelection=item.execution_id;selectView('execution')});
      activity.body.append(btn);
    }
  }).catch(e=>{if(token===loadCounter)empty(activity.body,'Activity unavailable',e.message)});
}
function workspaces(){
  const roots=[{id:'__birdeye__',path:'Canonical BirdEye gateway',root_class:'workspace',indexed_files:0}].concat(allRoots());
  if(!roots.find(x=>x.id===workspaceSelection))workspaceSelection='__birdeye__';
  const split=el('div','split'),nav=panel('Workspace registry','Select a live target to inspect','REGISTERED ROOTS'),detail=panel('Workspace Intelligence','Live structure, revision evidence and the selected workspace’s identity','CURRENT TARGET');
  const menu=el('div','workspace-menu');
  for(const item of roots){
    const b=el('button','workspace-select'+(item.id===workspaceSelection?' selected':''));
    b.type='button';b.setAttribute('aria-pressed',String(item.id===workspaceSelection));
    const badge=el('div');append(badge,el('span','root-title',item.id==='__birdeye__'?'BirdEye Gateway':item.id),el('span','root-detail',item.root_class||'workspace'));
    append(b,icon('folder'),badge,el('span','count',item.id==='__birdeye__'?'LOCAL':fmt(item.indexed_files||0)));
    b.onclick=()=>{workspaceSelection=item.id;workspaces();};
    menu.append(b);
  }
  nav.body.replaceChildren(menu);
  append(split,nav.outer,detail.outer);content.replaceChildren(split);loadText(detail.body);
  const selected=workspaceSelection,token=++loadCounter;
  get('/control/api/workspace?root='+encodeURIComponent(selected)).then(data=>{
    if(token!==loadCounter || workspaceSelection!==selected)return;
    detail.body.replaceChildren();
    const box=el('div','workspace-detail');
    append(box,el('h3','',selected==='__birdeye__'?'BirdEye Gateway':selected),el('p','section-subtitle',data.path));
    const metrics=el('div','detail-grid');
    for(const [n,v] of [['Host',model.host],['Git branch',data.git.branch||'Not a Git repository'],['Revision',data.git.head_sha?short(data.git.head_sha,15):'Not available'],['Working tree',data.git.is_repository?(data.git.dirty?'Modified':'Clean'):'Not a Git repository']]){
      const t=el('div','detail-tile');append(t,el('small','',n),el('strong','',v));metrics.append(t);
    }
    box.append(metrics);
    const rootHealth=allRoots().find(x=>x.id===selected);
    if(rootHealth){
      const health=el('section','detail-group index-root-health');
      append(health,el('h4','','Index health'));
      health.append(row('Current status',rootHealth.last_completed_at?'Last completed: '+date(rootHealth.last_completed_at):'No completed index run',rootHealth.status,rootHealth.status==='CURRENT'?'good':'warn','database'));
      if(rootHealth.last_error)health.append(row('Failure reason',rootHealth.last_error,'ACTIVE ERROR','warn','info'));
      if(rootHealth.last_failure){
        const entry=rootHealth.last_failure;
        health.append(row(rootHealth.recovered_after_failure?'Previous failure · recovered':'Last failure',
          String(entry.error||'No recorded reason')+' · '+date(entry.completed_at),
          rootHealth.recovered_after_failure?'RECOVERED':'HISTORY',
          rootHealth.recovered_after_failure?'good':'warn','activity'));
      }
      if(rootHealth.unresolved_files)health.append(el('p','helper-text',
        'Unresolved files: '+fmt(rootHealth.unresolved_files)+' · '+(rootHealth.next_action||'Inspect index receipts')));
      box.append(health);
    }
    const sourceExtra=el('details','workspace-source-details');
    sourceExtra.append(el('summary','','Source files, instructions & detected signals'));
    const signals=el('div','detail-group');append(signals,el('h4','','Detected workspace signals'));
    const tags=el('div','file-tags');
    for(const s of data.signals)tags.append(el('span','file-tag',s));
    if(!data.signals.length)tags.append(el('span','helper-text','No selected manifest signals detected at root level.'));
    append(signals,tags);sourceExtra.append(signals);
    const instructions=el('div','detail-group');append(instructions,el('h4','','Applicable instruction files present'));
    const is=el('div','file-tags');
    for(const p of data.instructions)is.append(el('span','file-tag',p));
    if(!data.instructions.length)is.append(el('span','helper-text','No root-level instruction file found. This is not a global policy denial.'));
    append(instructions,is);sourceExtra.append(instructions);
    const entries=el('div','detail-group');append(entries,el('h4','','Root entries · observed'));
    const et=el('div','file-tags');
    for(const p of data.entries)et.append(el('span','file-tag',(p.directory?'▣ ':'· ')+p.name));
    append(entries,et);sourceExtra.append(entries);
    sourceExtra.append(el('p','source-foot',data.evidence_source+' · '+date(data.observed_at)));
    detail.body.append(box);
    const visual=el('section','workspace-git-embedded');
    visual.setAttribute('aria-label','Visual Git status, history and open work for '+selected);
    detail.body.append(visual,sourceExtra);
    gitView({host:visual,root:selected,embedded:true});
  }).catch(e=>{if(token===loadCounter)empty(detail.body,'Workspace unavailable',e.message)});
}
function toolsView(){
  const p=panel('Registered MCP capabilities','Tools are discovered from the installed HTTP gateway, not a static list.',fmt(model.tools.length)+' TOOLS');
  p.body.append(note('Tool registration confirms the gateway definition only. Successful execution, target connectivity and permitted effects require independent receipts.'));
  const bar=el('div','surface-body toolbar'),search=el('div','search-bar');
  const input=el('input','field');input.type='search';input.placeholder='Search name, category, or capability…';input.setAttribute('aria-label','Filter tools');
  append(search,icon('search'),input);
  const categories=['All'].concat([...new Set(model.tools.map(x=>x.category))]);
  const categoriesBox=el('div','segment'),list=el('div','tool-list'),counter=el('div','tiny');
  let active=categories.includes(toolCategorySelection)?toolCategorySelection:'All';
  function update(){
    categoriesBox.replaceChildren();
    for(const cat of categories){
      const b=el('button',cat===active?'active':'',cat);b.type='button';
      b.setAttribute('aria-pressed',String(cat===active));b.onclick=()=>{active=cat;update()};
      categoriesBox.append(b);
    }
    list.replaceChildren();
    const query=input.value.trim().toLowerCase();
    const filtered=model.tools.filter(x=>(active==='All'||x.category===active) &&
      (x.name+' '+x.description+' '+x.category).toLowerCase().includes(query));
    counter.textContent=fmt(filtered.length)+' of '+fmt(model.tools.length)+' registered';
    for(const t of filtered){
      const card=el('details','tool-card'),s=el('summary'),left=el('div');
      append(left,el('div','tool-title',t.name),el('div','tool-desc',t.description));
      append(s,icon('tools'),left);card.append(s);
      const body=el('div','tool-detail');
      append(body,el('div','muted-sm','Category · '+t.category),el('div','muted-sm','Inputs / required fields'));
      for(const key of t.inputs||[]){const code=el('span','code-token',key+(t.required.includes(key)?' *':''));body.append(code)}
      if(!(t.inputs||[]).length)body.append(el('span','helper-text','No named parameters'));
      if(t.schema){const details=el('details','schema-detail'),summary=el('summary','','Input schema');details.append(summary,el('pre','raw-json',JSON.stringify(t.schema,null,2)));body.append(details)}
      card.append(body);list.append(card);
    }
    if(!filtered.length)empty(list,'No matching tools','Clear the filter to inspect all registered capabilities.');
  }
  input.addEventListener('input',update);
  const toolsOuter=append(p.outer,bar);
  append(bar,search,counter);const filters=el('div','tabs-small');filters.append(categoriesBox);
  p.outer.append(filters,list);p.body.remove();content.append(toolsOuter);update();
}
function skillsView(){
  const p=panel('Skills discovery','Search indexes available skill packages. Search results are not loaded instructions.','CURRENT SKILLS INDEX');
  const indexRoot=allRoots().find(x=>x.id==='skills');
  if(indexRoot && indexRoot.status!=='CURRENT'){
    diagnostic(p.body,{reason:indexRoot.last_error||('Skills indexing status: '+indexRoot.status),
      action:indexRoot.next_action||'Inspect indexed skills root and its latest trace receipt',
      evidence:indexRoot.last_completed_at||'No completed run'});
  }
  const intro=el('div','two-metric');
  const x=el('div');append(x,el('strong','',fmt(model.skills.files_tracked||0)),el('small','','TRACKED FILES'));
  append(intro,x);append(p.body,intro,el('div','line'),note('The reasoning agent selects relevant instructions. Full skill content should be resolved and SHA-pinned for a task before use.'));
  const form=el('form','toolbar');form.setAttribute('aria-label','Search skills');
  const bar=el('div','search-bar'),input=el('input','field');input.placeholder='Search skill name or expertise…';input.type='search';input.required=true;input.maxLength=160;
  append(bar,icon('search'),input);const button=el('button','primary-button','Search skills');button.type='submit';append(form,bar,button);p.body.append(form);
  const results=el('div');p.body.append(results);content.append(p.outer);
  form.addEventListener('submit',async e=>{
    e.preventDefault();if(!input.value.trim())return;button.disabled=true;loadText(results);error('');
    try{
      const data=await get('/control/api/skills?q='+encodeURIComponent(input.value.trim()));
      results.replaceChildren();
      const header=el('div','section-overline',fmt(data.results?.length||0)+' DISCOVERY MATCHES');
      results.append(header);
      for(const item of data.results||[]){
        results.append(row(item.path,'SHA-256 '+short(item.sha256,22),'DISCOVERY','neutral','file'));
      }
      if(!data.results?.length)empty(results,'No matching skills','Try another term or verify that the skills index contains the requested domain.');
    }catch(err){empty(results,'Discovery failed',err.message)}finally{button.disabled=false}
  });
}
function selectForRoots(withAll){
  const select=el('select','field');select.setAttribute('aria-label','Select workspace receipts');
  const list=withAll?[{id:'__all__',name:'All recent receipts'}]:[];
  list.push({id:'__birdeye__',name:'BirdEye Gateway'});
  for(const r of allRoots())list.push({id:r.id,name:r.id});
  for(const item of list){const option=el('option','',item.name);option.value=item.id;select.append(option)}
  return select;
}
function executionView(){
  const p=panel('Evidence ledger','Inspect execution receipts, observed changes, exact target and structured events.','PERSISTED RECEIPTS');
  const toolbar=el('div','surface-body toolbar'),select=selectForRoots(true),btn=el('button','secondary-button','Load receipts');
  select.value='__birdeye__';append(toolbar,select,btn);p.outer.append(toolbar);
  const split=el('div','split'),list=panel('Recent commands','Up to 25 receipts from the most recent scan','RECEIPT IDS'),detail=panel('Evidence inspector','Select a receipt to inspect its actual recorded effect.','SOURCE VERIFIED');
  split.append(list.outer,detail.outer);content.append(p.outer,split);
  function inspect(id){
    receiptSelection=id;loadText(detail.body);const token=++loadCounter;
    get('/control/api/receipt?id='+encodeURIComponent(id)).then(data=>{
      if(token!==loadCounter)return;
      detail.body.replaceChildren();
      const r=data.receipt;
      const wrap=el('div','receipt-detail');
      append(wrap,el('h3','',id),el('p','source-foot',r.workspace+' · '+date(r.completed_at)));
      const stats=el('div','detail-grid');
      for(const [name,value] of [['Exit code',r.exit_code===null?'Timeout':r.exit_code],['Evidence',r.overall_evidence||'UNVERIFIED'],['Changed workspace',String(!!r.workspace_changed_by_command)],['Elapsed',r.elapsed_seconds+' s']]){
        const tile=el('div','detail-tile');append(tile,el('small','',name),el('strong','',value));stats.append(tile);
      }
      wrap.append(stats);
      if(r.exit_code!==0 || r.overall_evidence==='FAILED'){
        diagnostic(wrap,{reason:r.exit_code===null?'Execution timeout':r.exit_code!==0?'Nonzero exit status '+r.exit_code:'Execution evidence did not pass',
          action:'Review events and hashes below, then retry only if the original operation remains authorized',
          evidence:'Persisted receipt '+id});
      }
      const cmd=el('div','detail-group');append(cmd,el('h4','','Executed argv'),el('pre','code-block',(r.argv||[]).join(' ')));wrap.append(cmd);
      const paths=el('div','detail-group');append(paths,el('h4','','Evidence hashes'),
        el('p','tiny','RECEIPT: '+(r.receipt_sha256||'UNAVAILABLE')),
        el('p','tiny','EXECUTION: '+(r.execution_evidence_sha256||'UNAVAILABLE')));
      wrap.append(paths);
      const timeline=el('div','detail-group');append(timeline,el('h4','','Recorded event timeline'));
      const items=el('div','timeline');
      for(const event of data.events||[]){
        const e=el('div','timeline-event');append(e,el('strong','',event.event||'event'),el('small','',date(event.timestamp)));
        items.append(e);
      }
      append(timeline,items);wrap.append(timeline);
      wrap.append(el('p','source-foot','Persisted source: '+data.source));detail.body.append(wrap);
    }).catch(e=>{if(token===loadCounter)empty(detail.body,'Receipt unavailable',e.message)});
  }
  async function load(){
    loadText(list.body);loadText(detail.body);btn.disabled=true;error('');
    const token=++loadCounter;
    try{
      const data=await get('/control/api/history?root='+encodeURIComponent(select.value));
      if(token!==loadCounter)return;list.body.replaceChildren();
      if(!data.records.length){empty(list.body,'No matching receipts in scan','The latest '+fmt(data.scanned)+' execution directories were checked. Try All recent receipts.');empty(detail.body,'Select an observed receipt','No receipt selected.');return}
      for(const r of data.records){
        const b=el('button','receipt-item');b.type='button';
        const left=el('div');append(left,el('div','name',short((r.argv||[]).join(' '),72)),
          el('div','date',short(r.execution_id,41)+' · '+date(r.completed_at)));
        const right=el('div','status-side');append(right,pill(r.exit_code===0?'EXIT 0':'EXIT '+r.exit_code,r.exit_code===0?'good':'bad'),
          el('span','tiny',r.overall_evidence||'UNVERIFIED'));
        append(b,left,right);
        if(r.exit_code!==0 || r.overall_evidence==='FAILED'){
          diagnostic(b,{reason:r.exit_code===null?'Command timed out':r.exit_code!==0?'Command exited with code '+r.exit_code:'Recorded execution evidence failed',
            action:'Select this receipt and inspect its event timeline and evidence hashes',
            evidence:r.execution_id});
        }
        b.onclick=()=>inspect(r.execution_id);list.body.append(b);
      }
      list.body.append(el('p','source-foot','Recent scan: '+fmt(data.scanned)+' receipt directories. Source is persisted state, not cached UI.'));
      const first=data.records.find(x=>x.execution_id===receiptSelection)||data.records[0];
      if(first)inspect(first.execution_id);
    }catch(e){if(token===loadCounter)empty(list.body,'History unavailable',e.message)}
    finally{btn.disabled=false}
  }
  btn.onclick=load;select.onchange=load;load();
}
function dataView(){
   registryManager("sources");
  const p=panel('Storage ownership','Canonical records, compatibility stores and disposable indexes','LIVE FILESYSTEM');
  content.append(note('Historical memory and derived indexes provide context, not current workspace authority.'));
  content.append(p.outer);loadText(p.body);
  const token=loadCounter;
  get('/control/api/storage').then(data=>{
    if(token!==loadCounter)return;p.body.replaceChildren();
    for(const entry of data.stores){
      const extra=entry.role+' · '+entry.path;
      const result=entry.exists?(entry.bytes===null?'DIRECTORY':bytes(entry.bytes)):'MISSING';
      const line=row(entry.name,extra,result,entry.exists?'good':'warn','database');
      if(!entry.exists)diagnostic(line,{reason:'Configured storage location is not present on this host',
        action:entry.role.toLowerCase().includes('projection')?'Confirm its canonical source and rebuild through the existing projector':'Check the configured path and its owning component; do not create a replacement store',
        evidence:entry.path});
      p.body.append(line);
    }
    const sources=panel('Memory source mappings','Historical records discovered from the current configured memory root',fmt(data.sources.length)+' SOURCES');
    for(const s of data.sources){
      const line=row(s.id,s.source_class+' · '+s.path,s.exists?'FOUND':'MISSING',s.exists?'neutral':'warn','file');
      if(!s.exists)diagnostic(line,{reason:'Configured source path is inaccessible or missing',action:'Verify this source still exists and is accessible to the BirdEye service identity',evidence:s.path});
      sources.body.append(line);
    }
    sources.body.append(el('p','source-foot','Observed '+date(data.observed_at)+' · Source paths remain authoritative on their owning machine.'));content.append(sources.outer);
  }).catch(e=>{if(token===loadCounter)empty(p.body,'Storage inspection unavailable',e.message)});
}
function connectionsView(){
   registryManager("connections");
  const grid=el('div','service-grid');
  const connections=[
    {name:'BirdEye MCP',target:'127.0.0.1:8766/mcp',probe:{ok:true},
     detail:'Canonical Streamable HTTP · '+model.tools.length+' registered tools'},
    {name:'BrowserAgent Local',target:'127.0.0.1:7726',probe:model.browser,
     detail:'Existing Chrome extension relay · CDP '+(model.browser.cdp?'enabled':'disabled')},
    {name:'Managed FreeLLMAPI',target:'127.0.0.1:8767',probe:model.llm,
     detail:'Shared local model routing process'},
  ];
  for(const entry of connections){
    const box=el('div','service-card');const ok=entry.probe.ok;
    append(box,el('h3','',entry.name),el('p','',entry.target),
      pill(ok?'RESPONDING':'UNVERIFIED',ok?'good':'warn'),el('p','',entry.detail));
    serviceStatus(box,entry.name,entry.probe);
    if(entry.name==='BrowserAgent Local'){
      const attached=entry.probe.client_connected===true;
      box.append(el('p','source-foot',attached?'Chrome extension connected · '+(entry.probe.client_age_seconds||0)+'s since last check-in':'Chrome extension connection not verified'));
      if(entry.probe.ok&&!attached)diagnostic(box,{
        reason:entry.probe.client_reason||'No active browser client check-in was reported',
        action:entry.probe.client_next_action||'Check the installed extension polling the existing relay',
        evidence:'Local relay /health browser_client'
      });
    }
    grid.append(box);
  }

  for(const a of model.apps||[]){
    const box=el('div','service-card');append(box,el('h3','',a.name),el('p','',a.path),pill(a.running?'RUNNING':'STOPPED',a.running?'good':'neutral'),el('p','','Existing managed application; launcher remains authoritative.'));
    if(!a.running)diagnostic(box,{reason:'Managed application process is not currently running',action:'Launch the application through its existing app launcher if required',evidence:'Live managed application registry'});
    grid.append(box);
  }
  content.append(grid);
  const p=panel('Capability exposure','Server registration does not imply every client exposes all tools.','OBSERVED');
  append(p.body,row('HTTP MCP tool definitions',fmt(model.tools.length)+' registered in the installed gateway','LOCAL','good','tools'),
    row('ChatGPT connector surface','Discover per-client; may be smaller than local HTTP registry','CLIENT SPECIFIC','neutral','connections'),
    row('Workspace execution','Direct argv command execution; target workspace owns its applicable constraints','AVAILABLE','good','activity'));
  content.append(p.outer);
  content.append(note('Fleet and Oracle machine state is shown only when independently discovered and verified. Local gateway health alone is not remote availability.'));
}

function settingsView(){
  registryManager("roots");
  const p=panel('Workspace configuration','Edit actual configured roots on '+model.host+'. Saved changes require the existing service to reload.','config.json');
  content.append(p.outer);loadText(p.body);const token=loadCounter;
  get('/control/api/config').then(data=>{
    if(token!==loadCounter)return;
    p.body.replaceChildren();
    let revision=data.revision;
    for(const item of data.roots){
      const card=el('details','config-card'),head=el('summary');
      append(head,icon('folder'),el('strong','',item.id),el('span','config-path',item.path),pill(item.enabled?'Enabled':'Disabled'));
      card.append(head);
      const form=el('form','config-form');
      const path=el('input','field');path.value=item.path;path.required=true;path.name='path';
      const excluded=el('textarea','field');excluded.value=(item.exclusions||[]).join('\n');excluded.rows=3;excluded.name='exclusions';
      append(form,fieldLabel('Workspace directory',path),fieldLabel('Excluded names or patterns · one per line',excluded));
      const checks=el('div','config-checks'),inputs={};
      for(const [key,label] of [['enabled','Participate in discovery'],['index','Index files'],['git','Inspect Git state']]){
        const check=el('input');check.type='checkbox';check.checked=!!item[key];check.name=key;inputs[key]=check;
        const lab=el('label','check-label');append(lab,check,document.createTextNode(label));checks.append(lab);
      }
      const actions=el('div','form-actions'),save=el('button','primary-button','Save changes'),reset=el('button','secondary-button','Reset');
      save.type='submit';reset.type='reset';const status=el('span','helper-text');status.setAttribute('role','status');
      append(actions,save,reset,status);append(form,checks,actions);
      form.onreset=e=>{e.preventDefault();path.value=item.path;excluded.value=(item.exclusions||[]).join('\n');for(const k of Object.keys(inputs))inputs[k].checked=!!item[k];status.textContent='Changes reset';};
      form.onsubmit=async e=>{
        e.preventDefault();save.disabled=true;status.textContent='Saving…';
        const patch={path:path.value.trim(),exclusions:excluded.value.split('\n').map(x=>x.trim()).filter(Boolean)};
        for(const k of Object.keys(inputs))patch[k]=inputs[k].checked;
        try{
          const result=await get('/control/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:item.id,patch,revision})});
          revision=result.revision;Object.assign(item,result.root);head.lastElementChild.replaceWith(pill(item.enabled?'Enabled':'Disabled'));
          status.textContent=result.changed?'Saved and read back · reload service to apply':'No changes';
          if(result.changed && !result.audit_saved)status.textContent+=' · audit unavailable';
          showToast(status.textContent);
        }catch(e){status.textContent=e.message;error(e.message)}finally{save.disabled=false}
      };
      card.append(form);p.body.append(card);
    }
    p.body.append(el('p','source-foot','Observed '+date(data.observed_at)+' · '+data.host+' · source config.json. Runtime and saved settings can differ until service reload.'));
  }).catch(e=>{if(token===loadCounter)empty(p.body,'Configuration unavailable',e.message)});
}

function registryManager(mode){
  const descriptions={
    roots:['Add workspace or indexed directory','Register an existing folder on the BirdEye host. This updates the authoritative config.json; indexing may require a service restart.'],
    sources:['Add memory, file or data source','Register an existing file or folder under a memory, knowledge or data root. The path is on the BirdEye host; registering it does not import or copy file contents.'],
    connections:['Manage SSH machine connections','Register an existing SSH connection to a remote machine. Private keys are referenced by path, never uploaded or displayed.']
  };
  const [heading,description]=descriptions[mode],p=panel(heading,description,'CONFIGURATION / LIVE');
  content.append(p.outer);
  const body=p.body;
  loadText(body);
  const token=loadCounter;
  get('/control/api/registry').then(state=>{
    if(token!==loadCounter)return;
    body.replaceChildren();
    const form=el('form','config-form registry-form');
    const help=el('p','helper-text','Paths must exist on the BirdEye host (except a remote SSH workspace path). Changes are saved, audited, and take effect after a gateway restart.');
    form.append(help);
    const fields={};
    function input(name,label,placeholder,value=''){
      const control=el('input','field');control.type='text';control.name=name;control.placeholder=placeholder||'';control.value=value;control.required=true;
      fields[name]=control;form.append(fieldLabel(label,control));return control;
    }
    input('id',mode==='connections'?'Connection ID':mode==='sources'?'Source ID':'Root ID','e.g. project-research');
    if(mode==='roots'){
      input('path','Directory path on this PC','D:\\Projects\\NewWorkspace');
      const select=el('select','field');
      for(const category of ['workspace','knowledge','memory','data']){
        const option=el('option','',category);option.value=category;select.append(option);
      }
      fields.class=select;form.append(fieldLabel('Type of indexed root',select));
    }else if(mode==='sources'){
      const select=el('select','field');
      const eligible=state.roots.filter(r=>['memory','knowledge','data'].includes(r.class));
      for(const root of eligible){const option=el('option','',root.id+' / '+root.class);option.value=root.id;select.append(option);}
      fields.root_id=select;form.append(fieldLabel('Owning registry root',select));
      input('path','File or directory path on this PC','C:\\Data\\research.pdf');
    }else{
      input('host','Host/IP address','192.168.1.240');
      input('expected_hostname','Verified machine hostname','LETTERBLACK');
      input('user','SSH user','Letterblack');
      input('port','SSH port','22','22');
      input('identity_file','Private-key file path (never its contents)','C:\\Users\\...\\.ssh\\id_ed25519');
      input('workspace','Workspace path on remote PC','C:\\BirdEye');
    }
    const rowActions=el('div','form-actions');
    const add=el('button','primary-button','Register '+(mode==='roots'?'root':mode==='sources'?'source':'connection'));
    add.type='submit';const status=el('span','helper-text');status.setAttribute('role','status');
    append(rowActions,add,status);form.append(rowActions);body.append(form);
    if(mode==='sources'){
      const uploadForm=el('form','config-form registry-upload');
      const file=el('input','field');file.type='file';file.required=true;
      const uploadButton=el('button','primary-button','Upload file to BirdEye');uploadButton.type='submit';
      const uploadStatus=el('span','helper-text');uploadStatus.setAttribute('role','status');
      const actions=el('div','form-actions');append(actions,uploadButton,uploadStatus);
      append(uploadForm,fieldLabel('Choose a file from this computer (maximum 20 MB)',file),actions);
      uploadForm.onsubmit=async event=>{
        event.preventDefault();
        if(!file.files.length)return;
        const chosen=file.files[0];uploadButton.disabled=true;uploadStatus.textContent='Uploading…';
        try{
          const result=await get('/control/api/upload?name='+encodeURIComponent(chosen.name),{
            method:'POST',headers:{'Content-Type':'application/octet-stream','X-Config-Revision':state.revision},
            body:await chosen.arrayBuffer()
          });
          state.revision=result.revision;
          uploadStatus.textContent='Saved '+result.file.name+' ('+bytes(result.file.size)+'). Restart BirdEye for indexing.';
          showToast('File uploaded and verified: '+result.file.name);
          file.value='';
          // The managed indexed user-data root is registered by the server on first upload.
          const updated=await get('/control/api/registry');
          state.roots=updated.roots;state.revision=updated.revision;
          entries();
        }catch(e){uploadStatus.textContent=e.message;error(e.message)}
        finally{uploadButton.disabled=false;}
      };
      body.append(uploadForm);
    }
    const registered=panel('Registered '+(mode==='roots'?'roots':mode==='sources'?'sources':'SSH connections'),
      'Remove a registry entry without deleting files or changing credentials','LIVE CONFIG');
    body.append(registered.outer);
    function entries(){
      registered.body.replaceChildren();
      const list=mode==='roots'?state.roots.map(r=>({id:r.id,path:r.class,action:'remove_root'})):
        mode==='sources'?state.roots.flatMap(r=>(r.sources||[]).map(x=>({id:x.id,path:r.id+' / '+x.path,root_id:r.id,action:'remove_source'}))):
        state.connections.map(x=>({id:x.id,path:x.user+'@'+x.host+':'+x.port+' / '+x.workspace,action:'remove_connection'}));
      if(!list.length){registered.body.append(el('p','helper-text','No entries registered.'));return;}
      for(const item of list){
        const holder=el('div','registry-entry');
        const info=el('div','registry-info');
        append(info,el('strong','',item.id),el('small','',short(item.path,104)));
        const remove=el('button','secondary-button','Remove');remove.type='button';
        remove.setAttribute('aria-label','Remove '+item.id);
        remove.onclick=async()=>{
          if(!confirm('Remove '+item.id+' from BirdEye configuration? Files and SSH keys will not be deleted.'))return;
          remove.disabled=true;
          try{
            const payload={action:item.action,id:item.id,revision:state.revision};
            if(item.root_id)payload.root_id=item.root_id;
            const result=await registryWrite(payload);
            state.revision=result.revision;
            if(mode==='roots')state.roots=state.roots.filter(x=>x.id!==item.id);
            else if(mode==='sources'){const root=state.roots.find(x=>x.id===item.root_id);if(root)root.sources=(root.sources||[]).filter(x=>x.id!==item.id);}
            else state.connections=state.connections.filter(x=>x.id!==item.id);
            entries();showToast('Removed '+item.id+' from registry; restart required');
          }catch(e){error(e.message);remove.disabled=false;}
        };
        append(holder,info,remove);registered.body.append(holder);
      }
    }
    entries();
    form.onsubmit=async event=>{
      event.preventDefault();add.disabled=true;status.textContent='Saving…';
      const payload={action:mode==='roots'?'add_root':mode==='sources'?'add_source':'add_connection',revision:state.revision};
      for(const [key,control] of Object.entries(fields))payload[key]=control.value.trim();
      if(mode==='connections')payload.port=Number(fields.port.value);
      try{
        const result=await registryWrite(payload);state.revision=result.revision;
        if(mode==='roots')state.roots.push({id:payload.id,class:payload.class,sources:[]});
        else if(mode==='sources'){const root=state.roots.find(r=>r.id===payload.root_id);if(root)root.sources.push({id:payload.id,path:payload.path});}
        else state.connections.push({id:payload.id,user:payload.user,host:payload.host,port:payload.port,workspace:payload.workspace});
        status.textContent='Saved and verified. Restart BirdEye to apply.';
        form.reset();entries();showToast('Registered '+payload.id+'; restart required');
      }catch(e){status.textContent=e.message;error(e.message)}
      finally{add.disabled=false;}
    };
  }).catch(e=>{if(token===loadCounter)empty(body,'Registry unavailable',e.message)});
}
async function registryWrite(payload){
  return get('/control/api/registry',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
}

function fieldLabel(text,input){
  const lab=el('label','form-field');append(lab,el('span','field-label',text),input);return lab;
}



// Render one actual Git worktree status surface in both Git History and Workspace Intelligence.
function gitWorktreeSurface(target,data){
  target.replaceChildren();
  if(!data.is_repository){
    target.append(note('Git status is not available: '+(data.reason||'No readable local repository.'),true));
    return;
  }
  const w=data.worktree||{};
  const header=el('div','git-branch-banner');
  const title=el('div','git-branch-identity');
  const glyph=el('div','row-icon');glyph.append(icon('git'));
  const description=el('div');
  append(description,el('strong','',data.branch||'Local branch'),
    el('small','',short(data.head||'',16)+' · local Git · '+date(data.observed_at)));
  append(title,glyph,description);
  const state=pill(!w.available?'UNKNOWN':w.dirty?'UNCOMMITTED CHANGES':'CLEAN',!w.available?'warn':w.dirty?'warn':'good');
  append(header,title,state);target.append(header);
  if(!w.available){
    target.append(el('p','helper-text',w.reason||'Unable to read working tree'));
    return;
  }
  const stats=el('div','git-worktree-stats');
  for(const [label,num] of [
    ['Staged',w.staged],['Unstaged',w.unstaged],
    ['Untracked',w.untracked],['Conflicts',w.conflicted]
  ]){
    const cell=el('div','git-worktree-counter');
    append(cell,el('strong','',fmt(num)),el('span','',label));stats.append(cell);
  }
  const ahead=el('div','git-worktree-counter');
  append(ahead,el('strong','',w.ahead==null?'—':'↑'+fmt(w.ahead)),
    el('span','','Ahead'+(w.upstream?' · '+short(w.upstream,32):'')));
  const behind=el('div','git-worktree-counter');
  append(behind,el('strong','',w.behind==null?'—':'↓'+fmt(w.behind)),el('span','','Behind'));
  stats.append(ahead,behind);target.append(stats);
  if(w.ahead==null||w.behind==null)target.append(el('p','source-foot','Upstream not configured or comparison unavailable. An unknown count is not zero.'));
  if(w.files?.length){
    const list=el('details','git-pending-files');
    const summary=el('summary','',fmt(w.files.length)+(w.file_list_limited?'+':'')+' working-tree entries · view paths');
    list.append(summary);
    for(const entry of w.files){
      const line=el('div','git-pending-row');
      append(line,el('span','code-token',entry.status),el('span','',entry.path));list.append(line);
    }
    if(w.file_list_limited)list.append(el('p','helper-text','File list capped for display; counts use full Git status output.'));
    target.append(list);
  }
}

// Open GitHub issues/PRs are fetched from the workspace's origin remote. Never infer
// "none open" from an unavailable GitHub response.
function gitRemoteWorkSurface(host,data,embedded){
  host.replaceChildren();
  const title=el('div','git-remote-caption'),left=el('div');
  append(left,el('strong','',data.repository||'Repository not connected'),
    el('small','','GitHub origin · '+date(data.observed_at)));
  title.append(left);
  if(data.repository_url){
    const link=el('a','git-remote-link','View on GitHub ↗');link.href=data.repository_url;
    link.target='_blank';link.rel='noopener noreferrer';title.append(link);
  }
  host.append(title);
  const grid=el('div','git-remote-grid');
  for(const section of [
    {key:'issues',status:'issues_status',limited:'issues_limited',name:'Open issues',empty:'No open issues reported by GitHub.',icon:'activity'},
    {key:'pull_requests',status:'pull_requests_status',limited:'pull_requests_limited',name:'Open pull requests',empty:'No open pull requests reported by GitHub.',icon:'git'}
  ]){
    const box=el('section','git-remote-card'),head=el('div','git-remote-card-head'),dataList=data[section.key]||[];
    const live=data[section.status]==='live';
    const label=el('div','git-remote-title');label.append(icon(section.icon),el('strong','',section.name));
    append(head,label,pill(live?fmt(dataList.length)+(data[section.limited]?'+':'')+' OPEN':'UNAVAILABLE',
      live?'neutral':'warn'));box.append(head);
    if(!live){
      box.append(el('p','git-remote-empty',data.reason||'GitHub data could not be verified. This does not mean zero open items.'));
    }else if(dataList.length===0){
      box.append(el('p','git-remote-empty',section.empty));
    }else{
      const list=el('div','git-remote-list');
      const items=embedded?dataList.slice(0,4):dataList.slice(0,14);
      for(const item of items){
        const link=el('a','git-remote-item');link.href=item.url;
        link.rel='noopener noreferrer';link.target='_blank';
        const prefix=el('span','git-remote-number','#'+item.number);
        const body=el('span','git-remote-item-body');
        append(body,el('strong','',item.title));
        const line=item.draft?'DRAFT · '+short(item.head||'',40):item.head?short(item.head,40)+' → '+short(item.base,40):'Updated '+date(item.updated_at);
        append(body,el('small','',line));
        if(item.labels?.length){
          const tags=el('span','git-remote-labels');
          for(const value of item.labels.slice(0,3))tags.append(el('span','git-ref',value));
          body.append(tags);
        }
        append(link,prefix,body,icon('arrow'));list.append(link);
      }
      box.append(list);
      if(items.length<dataList.length){
        box.append(el('p','source-foot','Showing '+items.length+' of '+dataList.length+' items. Use GitHub to view the complete list.'));
      }
      if(data[section.limited])box.append(el('p','source-foot','At least 100 open results; the source response is bounded to the first page.'));
    }
    grid.append(box);
  }
  host.append(grid);
  host.append(el('p','source-foot','Remote information is separate from local Git commits and worktree state.'));
}

function gitView(options={}){
  const host=options.host||content,embedded=!!options.embedded;
  const heading=panel(embedded?'Workspace Git status & history':'Git History',
    'Local changes, branches and real commit-parent edges. Select a commit to inspect its files.','SOURCE: LOCAL GIT');
  const toolbar=el('div','toolbar'),target=selectForRoots(false),reload=el('button','secondary-button','Load history');
  target.setAttribute('aria-label','Git workspace');
  target.value=options.root||workspaceSelection||'__birdeye__';
  if(!target.value)target.value='__birdeye__';
  const searchWrap=el('div','search-bar'),search=el('input','field');
  search.type='search';search.placeholder='Highlight commits, authors, or refs…';
  search.setAttribute('aria-label','Search Git history');
  append(searchWrap,icon('search'),search);
  if(!embedded)toolbar.append(target);
  append(toolbar,searchWrap,reload);
  heading.body.append(toolbar);
  const worktree=el('div','git-worktree-surface');heading.body.append(worktree);
  host.append(heading.outer);
  const layout=el('div','git-layout'+(embedded?' git-layout-embedded':'')),timeline=panel('Commit graph','Parent lines and local branch annotations','LOCAL REFS'),
    inspector=panel('Commit inspector','Select any graph node or commit to inspect its contents','READ ONLY');
  layout.append(timeline.outer,inspector.outer);
  const remote=panel('GitHub issues & pull requests',
    'Current open work from this workspace’s configured GitHub origin.','SOURCE: REMOTE');
  const remoteBody=el('div','git-remote-surface');remote.body.append(remoteBody);
  if(embedded)host.append(layout,remote.outer); // Workspace: inspect local graph before remote work.
  else host.append(remote.outer,layout); // Full history: show issues and PRs before the long graph.
  let selectedSha='',shownRows=[],initialToken=loadCounter,query='';
  const palette=['#9de6c2','#a9b8f3','#f1d094','#d3a4e2','#8bd0e6','#edaa9c','#b8cb96','#d6bab0'];
  function graphLayout(commits){
    const active=[];
    const out=[];
    for(const commit of commits){
      let lane=active.indexOf(commit.sha);
      if(lane<0){lane=active.indexOf(null);if(lane<0)lane=active.length;active[lane]=commit.sha}
      active[lane]=commit.parents[0]||null;
      for(const parent of commit.parents.slice(1)){
        if(active.includes(parent))continue;
        let i=active.indexOf(null);if(i<0)i=active.length;active[i]=parent;
      }
      while(active.length&&active[active.length-1]===null)active.pop();
      out.push(Object.assign({},commit,{lane}));
    }
    return out;
  }
  function updateHighlight(){
    query=search.value.trim().toLowerCase();let matches=0;
    for(const row of timeline.body.querySelectorAll('.git-commit-row')){
      const hit=!query || (row.dataset.search||'').includes(query);
      row.classList.toggle('git-dim',!hit);if(hit)matches++;
    }
    const caption=timeline.head.querySelector('.head-meta');
    if(caption)caption.textContent=query?matches+' MATCHES · TOPOLOGY KEPT':'LOCAL REFS';
  }
  async function inspect(row,sourceRoot){
    selectedSha=row.sha;
    timeline.body.querySelectorAll('.git-commit-row').forEach(x=>x.classList.toggle('selected',x.dataset.sha===selectedSha));
    const token=loadCounter;
    loadText(inspector.body);
    try{
      const result=await get('/control/api/git/commit?root='+encodeURIComponent(sourceRoot)+'&commit='+encodeURIComponent(row.sha));
      if(token!==loadCounter||selectedSha!==row.sha||target.value!==sourceRoot)return;
      inspector.body.replaceChildren();
      const box=el('div','git-inspector-body');
      append(box,el('div','section-overline','SELECTED COMMIT'),
        el('h3','git-commit-title',row.subject),pill(row.short,'neutral'));
            // Use explicit real Git metadata; the graph only visualizes received parent relations.
      const facts=el('div','git-fact-list');
      for(const [label,value] of [['Commit',row.sha],['Branch markers',row.refs.length?row.refs.join(', '):'No ref at this commit'],['Parents',row.parents.length?row.parents.map(s=>s.slice(0,10)).join(', '):'Root commit']]){
        const d=el('div','git-fact');append(d,el('span','',label),el('code','',value));facts.append(d);
      }
      box.append(facts);
      const files=el('div','git-file-group'),title=el('h4','',result.files.length+' changed files in displayed diff');
      files.append(title);
      for(const item of result.files){
        const r=el('div','git-file-row'),name=el('span','git-file-name',item.path),count=el('span','git-file-count');
        count.append(el('span','plus',item.added===null?'binary':'+'+fmt(item.added)),
                     el('span','minus',item.deleted===null?'binary':'−'+fmt(item.deleted)));
        append(r,name,count);files.append(r);
      }
      if(!result.files.length)files.append(el('p','helper-text','No per-file numstat was returned. Merge commits may have a combined diff.'));
      if(result.truncated)files.append(el('p','helper-text','File list truncated for bounded read-only inspection.'));
      box.append(files);
      box.append(el('p','source-foot','Read-only source: '+result.source+' · observed '+date(result.observed_at)));
      inspector.body.append(box);
    }catch(e){if(token===loadCounter)empty(inspector.body,'Commit details unavailable',e.message)}
  }
  function draw(data,sourceRoot){
    timeline.body.replaceChildren();
    const commits=embedded?(data.commits||[]).slice(0,10):(data.commits||[]);
    shownRows=graphLayout(commits);
    gitWorktreeSurface(worktree,data);
    const head=el('div','git-summary'),label=el('div');
    append(label,el('strong','',data.branch||'Local history'),el('small','',data.repo_root||data.path||sourceRoot));
    append(head,label,pill((data.commits||[]).length+' COMMITS','neutral'));
    timeline.body.append(head);
    if(embedded && (data.commits||[]).length>10){
      const viewAll=el('button','git-show-full','Explore full Git history →');
      viewAll.type='button';viewAll.onclick=()=>selectView('git');
      timeline.body.append(viewAll);
    }
    if(!data.is_repository || !shownRows.length){
      empty(timeline.body,'No Git commits available',data.reason||'This workspace has no readable local commits.');
      empty(inspector.body,'No commit selected','Select a Git workspace with readable local history.');
      return;
    }
    const rowHeight=78,rail=Math.max(92,Math.min(215,(Math.max(...shownRows.map(x=>x.lane))+1)*20+38));
    const wrapper=el('div','git-graph-wrapper');
    wrapper.style.setProperty('--git-rail',rail+'px');
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
    const height=shownRows.length*rowHeight,xmlns='http://www.w3.org/2000/svg';
    svg.setAttribute('viewBox','0 0 '+rail+' '+height);svg.setAttribute('aria-hidden','true');
    svg.setAttribute('width',String(rail));svg.setAttribute('height',String(height));svg.classList.add('git-graph-svg');
    const index=new Map(shownRows.map((c,i)=>[c.sha,i]));
    const xFor=c=>25+Math.min(c.lane,8)*20;
    for(const [i,c] of shownRows.entries()){
      for(const parent of c.parents){
        const pi=index.get(parent);if(pi==null||pi<=i)continue;
        const target=shownRows[pi],x1=xFor(c),x2=xFor(target),y1=(i+.5)*rowHeight,y2=(pi+.5)*rowHeight;
        const segment=document.createElementNS(xmlns,'path');
        const mid=y1+Math.min((y2-y1)/2,rowHeight*.62);
        segment.setAttribute('d','M'+x1+' '+y1+' C'+x1+' '+mid+' '+x2+' '+(y2-(mid-y1))+' '+x2+' '+y2);
        segment.setAttribute('stroke',palette[c.lane%palette.length]);
        segment.setAttribute('stroke-width',parent===c.parents[0]?'2.5':'1.7');
        segment.setAttribute('opacity',parent===c.parents[0]?'.86':'.61');
        segment.setAttribute('fill','none');svg.append(segment);
      }
    }
    for(const [i,c] of shownRows.entries()){
      const circle=document.createElementNS(xmlns,'circle');
      circle.setAttribute('cx',String(xFor(c)));circle.setAttribute('cy',String((i+.5)*rowHeight));
      circle.setAttribute('r',c.is_head?'7':'5');
      circle.setAttribute('fill',c.is_head?'#dcefe5':'#172127');
      circle.setAttribute('stroke',palette[c.lane%palette.length]);
      circle.setAttribute('stroke-width',c.is_head?'3':'2');
      svg.append(circle);
    }
    wrapper.append(svg);
    for(const c of shownRows){
      const entry=el('button','git-commit-row'),copy=el('span','git-commit-copy');
      entry.type='button';entry.dataset.sha=c.sha;
      entry.dataset.search=(c.sha+' '+c.subject+' '+c.author+' '+c.refs.join(' ')).toLowerCase();
      append(copy,el('strong','git-subject',c.subject));
      const refs=el('span','git-row-refs');
      for(const ref of c.refs.slice(0,3))refs.append(el('span','git-ref',ref));
      if(c.is_head)refs.append(el('span','git-head','HEAD'));
      if(refs.childNodes.length)copy.append(refs);
      append(copy,el('small','git-authored',c.short+' · '+c.author+' · '+date(c.date)));
      entry.append(copy);entry.onclick=()=>inspect(c,sourceRoot);
      wrapper.append(entry);
    }
    timeline.body.append(wrapper);
    timeline.body.append(el('p','source-foot','Visualized from Git parent SHA references on '+data.repo_root+'. No checkout, fetch or write. Maximum '+data.limit+' entries.'));
    updateHighlight();
    const selected=shownRows.find(x=>x.sha===selectedSha)||shownRows.find(x=>x.is_head)||shownRows[0];
    inspect(selected,sourceRoot);
  }
  async function load(){
    const root=target.value;
    workspaceSelection=root;
    const token=++loadCounter;
    reload.disabled=true;loadText(timeline.body);loadText(inspector.body);loadText(remoteBody);
    try{
      const data=await get('/control/api/git?root='+encodeURIComponent(root));
      if(token!==loadCounter||target.value!==root)return;
      draw(data,root);
      try{
        const github=await get('/control/api/github?root='+encodeURIComponent(root));
        if(token===loadCounter && target.value===root)gitRemoteWorkSurface(remoteBody,github,embedded);
      }catch(err){if(token===loadCounter)empty(remoteBody,'GitHub unavailable','Remote issue and PR counts are unverified: '+err.message)}
    }catch(e){if(token===loadCounter){empty(timeline.body,'Git history unavailable',e.message);empty(inspector.body,'No selection','Select another local Git workspace.');empty(remoteBody,'GitHub not checked','No Git history data was retrieved.')}}
    finally{reload.disabled=false}
  }
  target.onchange=load;reload.onclick=load;search.oninput=updateHighlight;
  load();
}

function planningView(){
  const p=panel('Prepare a task','Choose a workspace and tools, then describe the outcome and how to verify it.','LOCAL DRAFT');
  const key='birdeye-plan:'+model.host;let draft={objective:'',workspace:workspaceSelection,steps:[],verification:'',effects:''};
  try{const saved=JSON.parse(localStorage.getItem(key)||'null');if(saved&&typeof saved==='object')Object.assign(draft,saved)}catch{}
  const form=el('form','plan-form'),objective=el('textarea','field');objective.rows=2;objective.required=true;objective.value=draft.objective;objective.placeholder='What should your agent accomplish?';
  const target=el('select','field');for(const root of [{id:'__birdeye__'}].concat(allRoots())){const o=el('option','',root.id==='__birdeye__'?'BirdEye gateway':root.id);o.value=root.id;target.append(o)}target.value=draft.workspace;
  if(!target.value)target.value='__birdeye__';
  const steps=el('div','plan-steps'),add=el('button','secondary-button','Add step');add.type='button';
  function step(initial={}){
    const line=el('div','plan-step'),tool=el('select','field'),why=el('input','field'),remove=el('button','secondary-button','Remove');
    tool.setAttribute('aria-label','Tool for step');why.setAttribute('aria-label','Step purpose');why.placeholder='Purpose and expected effect';why.value=initial.purpose||'';
    const prompt=el('option','','Choose a discovered tool');prompt.value='';tool.append(prompt);
    for(const t of model.tools){const o=el('option','',t.name);o.value=t.name;tool.append(o)}tool.value=initial.tool||'';
    remove.type='button';remove.onclick=()=>line.remove();line.append(tool,why,remove);steps.append(line);
  }
  for(const s of draft.steps)step(s);if(!draft.steps.length)step();add.onclick=()=>step();
  const effects=el('textarea','field');effects.rows=2;effects.value=draft.effects;
  const verification=el('textarea','field');verification.rows=2;verification.value=draft.verification;
  const actions=el('div','form-actions'),save=el('button','primary-button','Save draft'),copy=el('button','secondary-button','Copy plan'),status=el('span','helper-text');save.type='submit';copy.type='button';status.setAttribute('role','status');
  function collect(){return {host:model.host,workspace:target.value,objective:objective.value.trim(),steps:[...steps.children].map(line=>({tool:line.querySelector('select').value,purpose:line.querySelector('input').value.trim()})),effects:effects.value.trim(),verification:verification.value.trim(),saved_at:new Date().toISOString(),state:'draft'}}
  form.onsubmit=e=>{e.preventDefault();try{localStorage.setItem(key,JSON.stringify(collect()));status.textContent='Saved in this browser · no execution submitted';showToast('Task draft saved')}catch{status.textContent='Browser storage unavailable; copy the plan to keep it'}};
  copy.onclick=async()=>{try{await navigator.clipboard.writeText(JSON.stringify(collect(),null,2));showToast('Plan copied')}catch{status.textContent='Clipboard unavailable. Save the draft in this browser.'}};
  append(actions,save,copy,status);append(form,fieldLabel('Objective',objective),fieldLabel('Target workspace',target),el('h3','','Planned steps'),steps,add,fieldLabel('Side effects and recovery',effects),fieldLabel('Verification and required evidence',verification),actions);p.body.append(form);content.append(p.outer);
}

function updateFreshness(){
  if(!model || !model.observed_at)return;
  const age=Math.max(0,Date.now()-new Date(model.observed_at).getTime());
  if(age>90000){
    document.documentElement.classList.add('stale-data');
    $('connection-state').classList.remove('online');
    $('connection-state').replaceChildren(el('span','dot'),document.createTextNode(' Cached'));
    $('checked-time').textContent='LAST OBSERVED '+date(model.observed_at);
  }
}
setInterval(updateFreshness,15000);

function installAppControls(){
  const launch=el('button','secondary-button command-launch','Jump to…');launch.type='button';launch.title='Jump to a view (Ctrl+K)';launch.onclick=()=>command.showModal();
  document.querySelector('.topbar-left').append(launch);
  const command=el('dialog','command-dialog');command.setAttribute('aria-label','Jump to a view');
  const input=el('input','field');input.type='search';input.placeholder='Search views or workspaces';input.setAttribute('aria-label','Search views or workspaces');
  const results=el('div','command-results'),close=el('button','secondary-button','Close');close.type='button';close.onclick=()=>command.close();
  const draw=()=>{
    results.replaceChildren();
    const targets=Object.entries(pages).map(([id,p])=>({name:p[0],keywords:id,run:()=>selectView(id)}));
    if(model)for(const root of allRoots())targets.push({name:'Workspace · '+root.id,run:()=>{workspaceSelection=root.id;selectView('workspaces')}});
    const found=targets.filter(x=>(x.name+' '+(x.keywords||'')).toLowerCase().includes(input.value.toLowerCase()));
    for(const target of found){const b=el('button','command-result',target.name);b.type='button';b.onclick=()=>{target.run();command.close()};results.append(b)}
    if(!found.length)empty(results,'No matching destination','Try a workspace or view name.');
  };
  input.oninput=draw;command.addEventListener('close',()=>{input.value='';launch.focus()});
  append(command,input,results,close);document.body.append(command);draw();
  launch.onclick=()=>{draw();command.showModal();input.focus()};
  document.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();draw();if(!command.open)command.showModal();input.focus()}});
  window.addEventListener('hashchange',()=>{const next=location.hash.slice(1);if(pages[next]&&next!==view)selectView(next)});
}
document.querySelectorAll('[data-icon]').forEach(x=>x.append(icon(x.dataset.icon)));
installFloatingPanel();
$('refresh').onclick=refresh;
const requested=location.hash.replace('#','');if(pages[requested])selectView(requested);else selectView('overview');
refresh();
