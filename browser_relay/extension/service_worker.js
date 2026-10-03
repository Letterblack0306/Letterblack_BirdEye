const RELAY = "http://127.0.0.1:7726";
const ALARM = "birdeye-relay-poll";

async function post(path, body) {
  const response = await fetch(RELAY + path, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body)
  });
  return response.json();
}

function isChatGPT(tab) {
  return !!(tab && tab.id && tab.url && /^https:\/\/(chatgpt\.com|chat\.openai\.com)\//.test(tab.url));
}

async function register(tab) {
  if (!isChatGPT(tab)) return;
  try {
    await post("/tabs/register", {
      tab_id: String(tab.id), url: tab.url, title: tab.title || "", thread_url: tab.url
    });
  } catch (_) {}
}

async function executeInTab(tabId, command) {
  const [{result}] = await chrome.scripting.executeScript({
    target: {tabId},
    func: async (command) => {
      function assistantText() {
        const nodes = Array.from(document.querySelectorAll(
          '[data-message-author-role="assistant"], article[data-testid*="conversation-turn"]'
        ));
        return nodes.map(n => (n.innerText || "").trim()).filter(Boolean).pop() || "";
      }
      function composer() {
        return document.querySelector(
          'textarea:not([disabled]):not([readonly]), [contenteditable="true"][role="textbox"], [contenteditable="true"]'
        );
      }
      function sendButton() {
        const selectors = [
          'button[data-testid="send-button"]',
          'button[data-testid*="send" i]:not([disabled])',
          'button[aria-label="Send prompt" i]',
          'button[aria-label*="Send" i]:not([disabled])',
          'form button[type="submit"]:not([disabled])',
          'button[type="submit"]:not([disabled])'
        ];
        for (const s of selectors) {
          const b = document.querySelector(s);
          if (b && !b.disabled && b.getAttribute("aria-hidden") !== "true") return b;
        }
        return null;
      }
      function setText(el, text) {
        el.focus();
        if (el.tagName === "TEXTAREA") {
          const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
          if (!setter) throw new Error("TEXTAREA_VALUE_SETTER_NOT_FOUND");
          setter.call(el, text);
        } else {
          const range = document.createRange();
          range.selectNodeContents(el);
          const selection = window.getSelection();
          selection.removeAllRanges();
          selection.addRange(range);
          if (!document.execCommand("insertText", false, text)) el.textContent = text;
        }
        el.dispatchEvent(new InputEvent("input", {
          bubbles: true, inputType: "insertText", data: text
        }));
      }
      async function waitForSubmission(el, text, timeout = 5000) {
        const deadline = Date.now() + timeout;
        while (Date.now() < deadline) {
          const users = Array.from(document.querySelectorAll('[data-message-author-role="user"]'))
            .map(n => (n.innerText || "").trim());
          const current = (el.innerText || el.textContent || "").trim();
          if (users.some(x => x === text || x.includes(text)) || !current || !current.includes(text)) return true;
          await new Promise(r => setTimeout(r, 100));
        }
        return false;
      }

      if (command.action === "read") {
        return {ok: true, text: assistantText(), url: location.href, title: document.title};
      }
      if (command.action === "diagnose") {
        const c = composer();
        return {
          ok: true, url: location.href, title: document.title,
          composer: c ? {tag:c.tagName, role:c.getAttribute("role"), aria:c.getAttribute("aria-label"), outer:c.outerHTML.slice(0,1200)} : null,
          buttons: Array.from(document.querySelectorAll("button")).slice(-40).map(b => ({
            aria:b.getAttribute("aria-label"), testid:b.getAttribute("data-testid"),
            type:b.getAttribute("type"), disabled:!!b.disabled
          }))
        };
      }
      if (command.action === "reply") {
        const c = composer();
        if (!c) throw new Error("CHAT_COMPOSER_NOT_FOUND");
        const text = command.payload?.text || "";
        setText(c, text);
        const b = sendButton();
        if (b) {
          b.click();
          if (!await waitForSubmission(c, text)) throw new Error("CHAT_SUBMISSION_NOT_VERIFIED");
          return {ok:true, sent:true, ui_verified:true, method:"button"};
        }
        const form = c.closest("form");
        if (form && typeof form.requestSubmit === "function") {
          form.requestSubmit();
          if (await waitForSubmission(c, text)) return {ok:true, sent:true, ui_verified:true, method:"form.requestSubmit"};
        }
        throw new Error("CHAT_SEND_CONTROL_NOT_FOUND");
      }
      throw new Error("UNKNOWN_RELAY_ACTION");
    },
    args: [command]
  });
  return result;
}

async function dispatchOne(tab) {
  if (!isChatGPT(tab)) return;
  try {
    await register(tab);
    const data = await post("/commands/claim", {tab_id: String(tab.id)});
    if (!data.command) return;
    await chrome.tabs.update(tab.id, {active: true});
    let result;
    try {
      result = await executeInTab(tab.id, data.command);
    } catch (error) {
      result = {ok:false, error:String(error?.message || error)};
    }
    await post("/commands/complete", {command_id:data.command.command_id, result:result || {ok:false,error:"NO_RESULT"}});
  } catch (_) {}
}

async function poll() {
  const tabs = await chrome.tabs.query({});
  for (const tab of tabs) await dispatchOne(tab);
}

function ensureAlarm() {
  chrome.alarms.create(ALARM, {periodInMinutes:0.5});
}

chrome.runtime.onInstalled.addListener(() => { ensureAlarm(); poll(); });
chrome.runtime.onStartup.addListener(() => ensureAlarm());
chrome.alarms.onAlarm.addListener(alarm => { if (alarm.name === ALARM) poll(); });
chrome.tabs.onCreated.addListener(register);
chrome.tabs.onUpdated.addListener((_tabId, _change, tab) => register(tab));
chrome.tabs.onRemoved.addListener(tabId => { post("/tabs/unregister", {tab_id:String(tabId)}).catch(() => {}); });
ensureAlarm();
poll();
