// BirdEye visual feedback only. Execution and authority remain in the existing tool path.
(() => {
  if(globalThis.__birdeyeVisuals?.version==="0.3.1")return;
  globalThis.__birdeyeVisuals?.destroy?.();
  const host=document.createElement("div");host.id="__birdeye_agent_visuals";
  host.setAttribute("aria-hidden","true");
  host.style.cssText="position:fixed!important;inset:0!important;pointer-events:none!important;z-index:2147483647!important;contain:layout style!important;";
  const root=host.attachShadow({mode:"closed"});
  root.innerHTML=`<style>
  :host{all:initial}*{box-sizing:border-box} .hud{position:fixed;right:22px;bottom:22px;width:284px;background:rgba(18,19,23,.94);color:#f4f4f6;border:1px solid #ffffff24;border-radius:18px;font:12px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;backdrop-filter:blur(24px);padding:15px 16px 13px;transform:translateY(0);transition:opacity .3s,transform .3s,border-color .3s;overflow:hidden;pointer-events:none}
  .head{display:flex;align-items:center;gap:9px}.mark{display:grid;place-items:center;width:28px;height:28px;border-radius:9px;background:#ff57521c;border:1px solid #ff575247;color:#ff8b85}
  .brand{font-size:11px;font-weight:650;letter-spacing:1.5px}.live{margin-left:auto;display:flex;align-items:center;gap:5px;color:#aaaab2;font:9px/1 ui-monospace,monospace;letter-spacing:1px}.dot{width:5px;height:5px;background:#7e818b;border-radius:50%}
  .title{font-size:15px;font-weight:550;letter-spacing:-.25px;margin-top:13px}.meta{display:flex;justify-content:space-between;gap:12px;margin-top:5px;color:#8f919e;font-size:10px}.site{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:195px}.track{height:2px;background:#ffffff0c;margin:13px -16px -13px;overflow:hidden}.bar{height:100%;width:100%;background:#ff625a;transform:translateX(-100%);transition:transform .5s}
  .hud[data-phase="active"]{border-color:#ff71674d}.hud[data-phase="active"] .dot{background:#ff6d62;box-shadow:0 0 9px #ff574f;animation:breathe 1.5s ease-in-out infinite}.hud[data-phase="active"] .live{color:#ff9c92}.hud[data-phase="active"] .bar{animation:scan 1.9s ease-in-out infinite}
  .hud[data-phase="done"] .dot{background:#75dba6}.hud[data-phase="done"] .live{color:#92d7b1}.hud[data-phase="done"] .bar{background:#75dba6;transform:translateX(0)}
  .hud[data-phase="error"]{border-color:#ff6b6470}.hud[data-phase="error"] .dot{background:#ff685d}.hud[data-phase="idle"]{opacity:.68;transform:translateY(3px)}
  .cursor{position:fixed;left:0;top:0;width:28px;height:32px;opacity:0;will-change:transform;transition:opacity .2s;filter:drop-shadow(0 0 4px #ff5b5599) drop-shadow(0 0 13px #ff5b5555)}
  .cursor svg{width:26px;height:30px;overflow:visible}.cursor.active{opacity:1}.tag{position:absolute;top:26px;left:18px;padding:3px 6px;border:1px solid #ff7a6a55;border-radius:5px;background:#1d171bee;color:#ffc2b7;font:8px/1 ui-monospace,monospace;letter-spacing:1px;white-space:nowrap}
  .halo{position:fixed;left:0;top:0;width:58px;height:58px;border-radius:50%;background:radial-gradient(circle,#ff5e511a,transparent 70%);border:1px solid #ff756a38;opacity:0;will-change:transform;transition:opacity .2s}
  .halo.active{opacity:1}.target{position:fixed;border:1px solid #ff8a76b3;border-radius:9px;background:#ff67500a;box-shadow:0 0 0 4px #ff6c520c,0 0 22px #ff675014;opacity:0;transition:opacity .2s;pointer-events:none}.target.active{opacity:1}
  .ripple{position:fixed;left:0;top:0;width:18px;height:18px;border:2px solid #ff9983;border-radius:50%;opacity:0;pointer-events:none}
  @keyframes breathe{50%{opacity:.45}}@keyframes scan{0%{transform:translateX(-100%)}70%,100%{transform:translateX(100%)}}
  @media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}.hud[data-phase="active"] .bar{transform:translateX(0)}}
  @media(max-width:520px){.hud{right:12px;bottom:12px;width:230px;padding:12px}.title{font-size:13px}.track{margin-left:-12px;margin-right:-12px;margin-bottom:-12px}}
  </style>
  <div class="target"></div><div class="halo"></div>
  <div class="cursor"><svg viewBox="0 0 28 32" fill="none"><path d="M3 2L24 18L14.4 19.6L10.3 28L3 2Z" fill="#fff6ef" stroke="#4b2929" stroke-width="1.5" stroke-linejoin="round"/><path d="M5 6L8.8 20L11.6 15.5L18.4 15.7L5 6Z" fill="#ff6c5c"/></svg><span class="tag">AGENT</span></div>
  <div class="ripple"></div>
  <div class="hud" data-phase="idle"><div class="head"><div class="mark"><svg width="17" height="17" viewBox="0 0 20 20" fill="none"><path d="M3 10C7 3 13 3 17 10C13 17 7 17 3 10Z" stroke="currentColor" stroke-width="1.3"/><circle cx="10" cy="10" r="2.3" fill="currentColor"/></svg></div><span class="brand">BIRDEYE</span><span class="live"><i class="dot"></i><span class="state">READY</span></span></div><div class="title">Browser connected</div><div class="meta"><span class="site"></span><span class="count">00 ACTIONS</span></div><div class="track"><div class="bar"></div></div></div>`;
  (document.body||document.documentElement).append(host);
  const $=s=>root.querySelector(s),hud=$(".hud"),cursor=$(".cursor"),halo=$(".halo"),target=$(".target"),ripple=$(".ripple");
  const reduced=matchMedia("(prefers-reduced-motion: reduce)").matches;
  let x=Math.max(32,innerWidth*.55),y=Math.max(32,innerHeight*.45),phase="idle",action="",count=0,frames=0,moves=0,timer=null,animation=0,hidden=false;
  const trace=[];
  const labels={open:"Opening page",navigate:"Navigating",snapshot:"Reading page",click:"Clicking element",type:"Entering text",select:"Choosing option",press:"Pressing key",scroll:"Scrolling page",screenshot:"Capturing page",back:"Going back",forward:"Going forward",reload:"Reloading page",close:"Closing tab"};
  const record=(event,extra={})=>{trace.push({event,t:Math.round(performance.now()),...extra});if(trace.length>30)trace.shift();};
  const paint=()=>{cursor.style.transform="translate3d("+x+"px,"+y+"px,0)";halo.style.transform="translate3d("+(x-25)+"px,"+(y-25)+"px,0)";};
  paint();$(".site").textContent=location.hostname;
  function begin(a){clearTimeout(timer);phase="active";action=a;count++;hud.dataset.phase=phase;$(".state").textContent="ACTIVE";$(".title").textContent=labels[a]||"Working";$(".count").textContent=String(count).padStart(2,"0")+" ACTIONS";cursor.classList.add("active");halo.classList.add("active");record("begin",{action:a});}
  async function move(tx,ty){
    tx=Math.max(8,Math.min(innerWidth-34,tx));ty=Math.max(8,Math.min(innerHeight-42,ty));
    const sx=x,sy=y,dist=Math.hypot(tx-x,ty-y);moves++;
    if(reduced||dist<2){x=tx;y=ty;paint();record("move",{x,y,frames:0});return;}
    const duration=Math.min(650,Math.max(300,dist*.65)),start=performance.now();let n=0;
    await new Promise(resolve=>{function step(now){const p=Math.min(1,(now-start)/duration),ease=1-Math.pow(1-p,3);x=sx+(tx-sx)*ease;y=sy+(ty-sy)*ease;paint();frames++;n++;if(p<1)animation=requestAnimationFrame(step);else resolve();}animation=requestAnimationFrame(step);});
    record("move",{x:Math.round(x),y:Math.round(y),frames:n});
  }
  function box(el){const b=el.getBoundingClientRect();Object.assign(target.style,{left:(b.left-4)+"px",top:(b.top-4)+"px",width:(b.width+8)+"px",height:(b.height+8)+"px"});target.classList.add("active");return b;}
  async function focus(el,a){el.scrollIntoView({block:"center",inline:"nearest",behavior:"instant"});const b=box(el);$(".title").textContent=labels[a]||"Inspecting element";await move(b.left+b.width/2,b.top+b.height/2);record("target",{tag:el.tagName,ref:el.getAttribute("data-birdeye-ref")||null});}
  function pulse(){ripple.style.left=(x-9)+"px";ripple.style.top=(y-9)+"px";if(!reduced)ripple.animate([{transform:"scale(.6)",opacity:1},{transform:"scale(3.2)",opacity:0}],{duration:650,easing:"ease-out"});record("pulse");}
  async function probe(list){const candidates=list.filter(el=>{const b=el.getBoundingClientRect();return b.top>=0&&b.left>=0&&b.bottom<innerHeight-24&&b.right<innerWidth-24;}).slice(0,3);if(!candidates.length)await move(innerWidth*.5,Math.min(150,innerHeight*.3));for(const el of candidates){const b=box(el);await move(b.left+b.width/2,b.top+b.height/2);}record("probe",{targets:candidates.length});}
  function finish(ok){clearTimeout(timer);phase=ok?"done":"error";hud.dataset.phase=phase;$(".state").textContent=ok?"DONE":"CHECK";$(".title").textContent=ok?"Action complete":"Action needs attention";record("finish",{ok});timer=setTimeout(()=>{phase="idle";hud.dataset.phase=phase;$(".state").textContent="READY";$(".title").textContent="Browser connected";cursor.classList.remove("active");halo.classList.remove("active");target.classList.remove("active");},ok?1600:4000);}
  function hide(){hidden=!hidden;host.style.visibility=hidden?"hidden":"visible";}
  const key=e=>{if(e.altKey&&e.shiftKey&&e.code==="KeyB")hide();};addEventListener("keydown",key);
  globalThis.__birdeyeVisuals={version:"0.3.1",begin,move,focus,pulse,probe,finish,hide,inspect:()=>({version:"0.3.1",phase,action,moves,frames,x:Math.round(x),y:Math.round(y),reduced_motion:reduced,trace:[...trace]}),destroy:()=>{clearTimeout(timer);cancelAnimationFrame(animation);removeEventListener("keydown",key);host.remove();}};
})();
