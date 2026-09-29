const RELAY = "http://127.0.0.1:7726";

async function post(path, body) {
  const response = await fetch(RELAY + path, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body)
  });
  return response.json();
}

async function register(tab) {
  if (!tab || !tab.id || !tab.url) return;
  if (!/^https:\/\/(chatgpt\.com|chat\.openai\.com)\//.test(tab.url)) return;
  try {
    await post("/tabs/register", {
      tab_id: String(tab.id),
      url: tab.url,
      title: tab.title || "",
      thread_url: tab.url
    });
  } catch (_) {}
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.tabs.query({}, tabs => tabs.forEach(register));
});

chrome.tabs.onCreated.addListener(register);
chrome.tabs.onUpdated.addListener((_tabId, _change, tab) => register(tab));
chrome.tabs.onRemoved.addListener(tabId => {
  post("/tabs/unregister", {tab_id: String(tabId)}).catch(() => {});
});

async function poll() {
  chrome.tabs.query({}, async tabs => {
    for (const tab of tabs) {
      if (!tab.id || !tab.url || !/^https:\/\/(chatgpt\.com|chat\.openai\.com)\//.test(tab.url)) continue;
      try {
        await register(tab);
        const response = await fetch(RELAY + "/commands/next", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({tab_id: String(tab.id)})
        });
        const data = await response.json();
        for (const command of data.commands || []) {
          chrome.tabs.sendMessage(tab.id, {
            type: "BIRDEYE_RELAY_COMMAND",
            command
          });
        }
      } catch (_) {}
    }
  });
}

setInterval(poll, 750);
poll();
