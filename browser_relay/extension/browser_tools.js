// General browser operations owned by the existing BirdEye extension.
(() => {
  let polling=false;
  const done=new Map();
  const post=async(path,body)=>{const r=await fetch("http://127.0.0.1:7726"+path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});const d=await r.json();if(!r.ok)throw Error(d.message||d.error);return d;};
  const hello=()=>post("/browser/hello",{version:chrome.runtime.getManifest().version,extension_id:chrome.runtime.id});
  const waitTab=async id=>{
    const end=Date.now()+15000;
    while(Date.now()<end){const t=await chrome.tabs.get(id);if(t.status==="complete")return t;await new Promise(r=>setTimeout(r,100));}
    throw Error("BROWSER_LOAD_TIMEOUT");
  };
  async function visual(id,phase,p={}){
    await chrome.scripting.executeScript({target:{tabId:Number(id),frameIds:[Number(p.frame_id||0)]},files:["agent_visuals.js"]});
    const r=await chrome.scripting.executeScript({target:{tabId:Number(id),frameIds:[Number(p.frame_id||0)]},func:(phase,p)=>{
      const ui=globalThis.__birdeyeVisuals;
      if(phase==="begin")ui.begin(p.action);
      if(phase==="finish"){if(!ui.inspect().action)ui.begin(p.action);ui.finish(p.ok!==false);}
      return ui.inspect();
    },args:[phase,p]});
    return r[0]?.result;
  }
  async function execute(p){
    let id=p.tab_id,result,visualState=null;
    try{
      if(id&&p.action!=="tabs"){
        const t=await chrome.tabs.get(Number(id));
        if(t.url!==p.expected_url)throw Error("BROWSER_IDENTITY_CHANGED");
        if(/^https?:/.test(t.url)&&(t.active||p.active===true||p.action!=="snapshot")){
          if(p.active===true&&p.action!=="screenshot")await chrome.tabs.update(Number(id),{active:true});
          try{visualState=await visual(id,"begin",p);}catch(e){console.warn("BirdEye visuals",e.message);}
        }
      }
      result=await executeOperation(p);
      if(p.action==="open"&&result.ok){id=result.tab_id;try{visualState=await visual(id,"begin",p);}catch(e){console.warn("BirdEye visuals",e.message);}}
      if(id&&visualState&&p.action!=="close"&&p.action!=="tabs"){try{visualState=await visual(id,"finish",{...p,ok:result.ok});}catch(e){console.warn("BirdEye visuals",e.message);}}
      return {...result,...(visualState?{agent_visual:visualState}:{})};
    }catch(e){
      if(id&&visualState){try{await visual(id,"finish",{...p,ok:false});}catch(_e){}}
      throw e;
    }
  }
  async function executeOperation(p) {
    const a=p.action;
    if(a==="tabs")return {ok:true,tabs:(await chrome.tabs.query({})).map(t=>({tab_id:String(t.id),window_id:t.windowId,url:t.url,title:t.title,active:t.active,status:t.status}))};
    if(a==="open"){const t=await chrome.tabs.create({url:p.url,active:p.active===true});const after=await waitTab(t.id);return {ok:true,tab_id:String(t.id),url:after.url,title:after.title};}
    const id=Number(p.tab_id);const t=await chrome.tabs.get(id);
    if(t.url!==p.expected_url)throw Error("BROWSER_IDENTITY_CHANGED");
    if(!/^https?:/.test(t.url||""))throw Error("BROWSER_RESTRICTED_PAGE");
    if(a==="close"){await chrome.tabs.remove(id);return {ok:true,closed:true,tab_id:String(id)};}
    if(["navigate","back","forward","reload"].includes(a)){
      if(a==="navigate")await chrome.tabs.update(id,{url:p.url});
      if(a==="back"||a==="forward"){
        await chrome.scripting.executeScript({target:{tabId:id},func:delta=>{setTimeout(()=>history.go(delta),0);return {scheduled:true};},args:[a==="back"?-1:1]});
        const deadline=Date.now()+3000;
        while(Date.now()<deadline){const now=await chrome.tabs.get(id);if(now.url!==t.url||now.status==="loading")break;await new Promise(r=>setTimeout(r,50));}
      }
      if(a==="reload")await chrome.tabs.reload(id);
      await new Promise(r=>setTimeout(r,150));
      const after=await waitTab(id);return {ok:true,tab_id:String(id),url:after.url,title:after.title,loaded:after.status==="complete"};
    }
    if(a==="screenshot"){
      const previous=(await chrome.tabs.query({windowId:t.windowId,active:true}))[0];
      try{
      await chrome.tabs.update(id,{active:true});
      await new Promise(r=>setTimeout(r,100));
      const active=(await chrome.tabs.query({windowId:t.windowId,active:true}))[0];
      if(active?.id!==id||active.url!==p.expected_url)throw Error("BROWSER_CAPTURE_TARGET_CHANGED");
      // captureVisibleTab requires a renderable foreground browser window.
      // A background/minimized window can fail with "image readback failed".
      const captureWindow=await chrome.windows.get(t.windowId);
      if(captureWindow.state==="minimized")await chrome.windows.update(t.windowId,{state:"normal"});
      if(!captureWindow.focused)await chrome.windows.update(t.windowId,{focused:true});
      await new Promise(r=>setTimeout(r,350));
      let data;
      for(let attempt=0;attempt<2;attempt++){
        try{data=await chrome.tabs.captureVisibleTab(t.windowId,{format:"png"});break;}
        catch(e){
          if(attempt||!/image readback failed/i.test(String(e.message||e)))throw e;
          await new Promise(r=>setTimeout(r,500));
        }
      }
      if(!data?.startsWith("data:image/png;base64,"))throw Error("BROWSER_CAPTURE_INVALID_PNG");
      const after=await chrome.tabs.get(id);
      if(after.url!==p.expected_url)throw Error("BROWSER_IDENTITY_CHANGED");
      return {ok:true,tab_id:String(id),url:after.url,image_base64:data.split(",")[1],mime_type:"image/png"};
      }finally{
        // Restore only our own temporary switch; never override a user's later tab choice.
        const now=(await chrome.tabs.query({windowId:t.windowId,active:true}))[0];
        if(previous&&previous.id!==id&&now?.id===id){
          try{await chrome.tabs.get(previous.id);await chrome.tabs.update(previous.id,{active:true});}catch(_e){}
        }
      }
    }
    const allowed=await chrome.permissions.contains({origins:[new URL(t.url).origin+"/*"]});
    if(!allowed)throw Error("BROWSER_SITE_ACCESS_REQUIRED");
    let results;try{results=await chrome.scripting.executeScript({
      target:{tabId:id,frameIds:[Number(p.frame_id||0)]},
      func:async p=>{
        try{
        if(location.href!==p.expected_url && !p.frame_id)throw Error("BROWSER_IDENTITY_CHANGED");
        const sleep=ms=>new Promise(r=>setTimeout(r,ms));
        const ui=(document.visibilityState==="visible"||p.active===true)?globalThis.__birdeyeVisuals:null;
        function elements(){
          const all=[];
          function visit(root){for(const el of root.querySelectorAll("*")){if(el.id==="__birdeye_agent_visuals")continue;if(el.matches('a,button,input,textarea,select,[role="button"],[contenteditable="true"]')&&el.getClientRects().length)all.push(el);if(el.shadowRoot)visit(el.shadowRoot);}}
          visit(document);return all.slice(0,250);
        }
        function snapshot(){
          const epoch=Date.now().toString(36);
          const refs=globalThis.__birdeyeElementRefs=new Map();
          const es=elements().map((el,i)=>{const ref=epoch+"-"+i;refs.set(ref,el);return {ref,tag:el.tagName.toLowerCase(),name:String(el.getAttribute("aria-label")||el.getAttribute("placeholder")||el.innerText||el.name||el.id||"").slice(0,160),type:el.type||"",value:el.type==="password"?"[redacted]":String(el.value||"").slice(0,300),href:el.href||null,disabled:!!el.disabled};});
          return {url:location.href,title:document.title,history_length:history.length,text:(document.body?.innerText||"").slice(0,20000),elements:es,frames:Array.from(document.querySelectorAll("iframe")).map(x=>({src:x.src,title:x.title}))};
        }
        if(p.action==="snapshot"){await ui?.probe(elements());return {ok:true,...snapshot()};}
        function resolve(){
          let found=null;
          if(p.ref){found=globalThis.__birdeyeElementRefs?.get(p.ref)||null;if(!found){for(const el of elements())if(el.getAttribute("data-birdeye-ref")===p.ref){found=el;break;}}}
          else if(p.selector){const hits=document.querySelectorAll(p.selector);if(hits.length!==1)throw Error("BROWSER_SELECTOR_NOT_UNIQUE");found=hits[0];}
          if(!found||!found.isConnected)throw Error("BROWSER_ELEMENT_NOT_FOUND");
          if(found.disabled)throw Error("BROWSER_ELEMENT_DISABLED");
          return found;
        }
        if(p.action==="scroll"){await ui?.move(innerWidth-60,innerHeight*.5);ui?.pulse();const before={x:scrollX,y:scrollY};window.scrollBy({left:Number(p.x??0),top:Number(p.y??500),behavior:"instant"});await sleep(80);return {ok:true,before,after:{x:scrollX,y:scrollY},url:location.href};}
        const el=resolve();await ui?.focus(el,p.action);el.scrollIntoView({block:"center"});el.focus();
        if(p.action==="click"){const before=(document.body?.innerText||"");ui?.pulse();el.click();return {ok:true,dispatched:true,visible_change:before!==(document.body?.innerText||""),...snapshot()};}
        if(p.action==="type"){
          const text=String(p.text??"");if(el.type==="file")throw Error("BROWSER_FILE_INPUT_REQUIRES_USER");
          if(el.isContentEditable){el.textContent=p.append?(el.textContent||"")+text:text;}
          else {const proto=el.tagName==="TEXTAREA"?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;const setter=Object.getOwnPropertyDescriptor(proto,"value")?.set;if(!setter)throw Error("BROWSER_NOT_TEXT_INPUT");setter.call(el,p.append?String(el.value||"")+text:text);}
          el.dispatchEvent(new InputEvent("input",{bubbles:true,inputType:"insertText",data:text}));el.dispatchEvent(new Event("change",{bubbles:true}));
          const value=String(el.isContentEditable?el.textContent:el.value);
          return {ok:p.append?value.endsWith(text):value===text,verified:true,value:el.type==="password"?"[redacted]":value,url:location.href};
        }
        if(p.action==="select"){el.value=String(p.value);el.dispatchEvent(new Event("input",{bubbles:true}));el.dispatchEvent(new Event("change",{bubbles:true}));return {ok:el.value===String(p.value),value:el.value,url:location.href};}
        if(p.action==="press"){
          const key=String(p.key||"Enter");if(!["Enter","Tab","Escape","ArrowUp","ArrowDown","ArrowLeft","ArrowRight"," "].includes(key))throw Error("BROWSER_KEY_UNSUPPORTED");
          const before=document.body?.innerText||"";el.dispatchEvent(new KeyboardEvent("keydown",{key,bubbles:true}));el.dispatchEvent(new KeyboardEvent("keyup",{key,bubbles:true}));await sleep(100);
          return {ok:true,dispatched:true,trusted:false,visible_change:before!==(document.body?.innerText||""),...snapshot()};
        }
        throw Error("BROWSER_ACTION_UNSUPPORTED");
        }catch(e){return {ok:false,error:String(e.message||e)};}
      },args:[p]
    });
    }catch(e){
      const after=await chrome.tabs.get(id);
      if(a==="click"&&after.url!==p.expected_url){const loaded=await waitTab(id);return {ok:true,tab_id:String(id),navigated:true,url:loaded.url,title:loaded.title};}
      throw e;
    }
    const r=results[0]?.result;if(!r)throw Error("BROWSER_NO_RESULT");
    return {...r,tab_id:String(id)};
  }
  async function poll(){
    if(polling)return;polling=true;
    try{
      await hello();
      // Flush an acknowledged local result before claiming any later action.
      for(const [id,v] of done){await post("/browser/complete",v);done.delete(id);await chrome.storage.local.remove("browser-result-"+id);}
      const c=(await post("/browser/next",{})).command;
      if(!c)return;
      let result;let timer;try{result=await Promise.race([execute(c.request),new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error("BROWSER_EXECUTION_UNCERTAIN")),20000);})]);}catch(e){result={ok:false,error:String(e.message||e),uncertain:String(e.message||e)==="BROWSER_EXECUTION_UNCERTAIN"};}finally{clearTimeout(timer);}
      const completion={request_id:c.request_id,claim:c.claim,result};
      done.set(c.request_id,completion);
      await chrome.storage.local.set({["browser-result-"+c.request_id]:completion});
      await post("/browser/complete",completion);
      done.delete(c.request_id);await chrome.storage.local.remove("browser-result-"+c.request_id);
    }catch(e){console.warn("BirdEye browser transport",e.message);}finally{polling=false;}
  }
  chrome.runtime.onMessage.addListener(m=>{if(m?.type==="BIRDEYE_BROWSER_HEARTBEAT")poll();});
  chrome.alarms.onAlarm.addListener(a=>{if(a.name==="birdeye-relay-poll")poll();});
  chrome.tabs.onUpdated.addListener((_id,change)=>{if(change.status==="complete")poll();});
  chrome.storage.local.get(null).then(all=>{for(const [k,v]of Object.entries(all))if(k.startsWith("browser-result-"))done.set(v.request_id,v);return poll();});
  setInterval(poll,750);
})();
