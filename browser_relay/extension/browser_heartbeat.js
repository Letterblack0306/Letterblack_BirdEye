setInterval(()=>{try{chrome.runtime.sendMessage({type:"BIRDEYE_BROWSER_HEARTBEAT"},()=>void chrome.runtime.lastError);}catch(_e){}},750);
