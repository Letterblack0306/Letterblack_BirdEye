const RELAY = "http://127.0.0.1:7726";
const ALARM = "birdeye-relay-poll";
async function post(path, body) {
  const response = await fetch(RELAY + path, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)});
  return response.json();
}
function isChatGPT(tab) { return !!(tab && tab.id && tab.url && /^https:\/\/(chatgpt\.com|chat\.openai\.com)\//.test(tab.url)); }
async function register(tab) {
  if (!isChatGPT(tab)) return;
  try { await post("/tabs/register", {tab_id:String(tab.id), url:tab.url, title:tab.title||"", thread_url:tab.url}); } catch (_) {}
}
async function poll() {
  chrome.tabs.query({}, async tabs => {
    for (const tab of tabs) {
      if (!isChatGPT(tab)) continue;
      try {
        await register(tab);
        const response = await fetch(RELAY + "/commands/next", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({tab_id:String(tab.id)})});
        const data = await response.json();
        for (const command of data.commands || []) {
          chrome.tabs.sendMessage(tab.id, {type:"BIRDEYE_RELAY_COMMAND", command}).catch(() => {});
        }
      } catch (_) {}
    }
  });
}
function ensureAlarm() { chrome.alarms.create(ALARM, {periodInMinutes:0.012}); }
chrome.runtime.onInstalled.addListener(() => { ensureAlarm(); chrome.tabs.query({}, tabs => tabs.forEach(register)); });
chrome.runtime.onStartup.addListener(() => ensureAlarm());
chrome.alarms.onAlarm.addListener(alarm => { if (alarm.name === ALARM) poll(); });
chrome.tabs.onCreated.addListener(register);
chrome.tabs.onUpdated.addListener((_tabId, _change, tab) => register(tab));
chrome.tabs.onRemoved.addListener(tabId => { post("/tabs/unregister", {tab_id:String(tabId)}).catch(() => {}); });
ensureAlarm();
poll();

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type === "BIRDEYE_GET_TAB_ID" && sender.tab?.id != null) {
    sendResponse({tab_id: String(sender.tab.id)});
  }
  return true;
});
