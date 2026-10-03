(function () {
  const RELAY = "http://127.0.0.1:7726";
  let lastAssistant = "";
  let tabId = null;
  let polling = false;

  function assistantMessages() {
    const nodes = Array.from(document.querySelectorAll(
      '[data-message-author-role="assistant"], article[data-testid*="conversation-turn"]'
    ));
    return nodes.map(node => ({text: (node.innerText || "").trim()})).filter(x => x.text);
  }

  function latestAssistantText() {
    const messages = assistantMessages();
    return messages.length ? messages[messages.length - 1].text : "";
  }

  function findComposer() {
    return document.querySelector(
      'textarea:not([disabled]):not([readonly]), [contenteditable="true"][role="textbox"], [contenteditable="true"][data-lexical-editor="true"], [contenteditable="true"]'
    );
  }

  async function waitForComposer(timeoutMs = 15000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const composer = findComposer();
      if (composer) return composer;
      await new Promise(resolve => setTimeout(resolve, 250));
    }
    return null;
  }

  function userMessages() {
    const nodes = Array.from(document.querySelectorAll('[data-message-author-role="user"]'));
    return nodes.map(node => (node.innerText || "").trim()).filter(Boolean);
  }

  async function waitForSubmission(expected, composer, timeoutMs = 5000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      if (userMessages().some(text => text === expected || text.includes(expected))) return true;
      const current = (composer.innerText || composer.textContent || "").trim();
      if (!current || !current.includes(expected)) return true;
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    return false;
  }

  function findSendButton() {
    const selectors = [
      'button[data-testid="send-button"]',
      'button[data-testid*="send" i]:not([disabled])',
      'button[aria-label="Send prompt" i]',
      'button[aria-label*="Send" i]:not([disabled])',
      'form button[type="submit"]:not([disabled])',
      'button[type="submit"]:not([disabled])'
    ];
    for (const selector of selectors) {
      const button = document.querySelector(selector);
      if (button && !button.disabled && button.getAttribute("aria-hidden") !== "true") return button;
    }
    return null;
  }

  function setComposerText(composer, text) {
    composer.focus();
    if (composer.tagName === "TEXTAREA") {
      const setter = Object.getOwnPropertyDescriptor(
        HTMLTextAreaElement.prototype, "value"
      )?.set;
      if (!setter) throw new Error("TEXTAREA_VALUE_SETTER_NOT_FOUND");
      setter.call(composer, text);
    } else {
      const selection = window.getSelection();
      const range = document.createRange();
      range.selectNodeContents(composer);
      selection.removeAllRanges();
      selection.addRange(range);
      if (!document.execCommand("insertText", false, text)) {
        composer.textContent = text;
      }
    }
    composer.dispatchEvent(new InputEvent("input", {
      bubbles: true, inputType: "insertText", data: text
    }));
  }

  async function waitForSendControl(timeoutMs = 3000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const button = findSendButton();
      if (button) return {type: "button", element: button};
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    return null;
  }

  async function complete(commandId, result) {
    await fetch(RELAY + "/commands/complete", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({command_id: commandId, result})
    });
  }

  async function execute(command) {
    try {
      if (command.action === "diagnose") {
        const composer = findComposer();
        const buttons = Array.from(document.querySelectorAll("button")).slice(-40).map(button => ({
          text: (button.innerText || "").trim().slice(0,120),
          aria: button.getAttribute("aria-label"),
          testid: button.getAttribute("data-testid"),
          type: button.getAttribute("type"),
          disabled: !!button.disabled
        }));
        await complete(command.command_id, {
          ok: true,
          url: location.href,
          title: document.title,
          composer: composer ? {tag: composer.tagName, role: composer.getAttribute("role"), aria: composer.getAttribute("aria-label"), placeholder: composer.getAttribute("placeholder"), outer: composer.outerHTML.slice(0,1200)} : null,
          form: composer?.closest("form") ? composer.closest("form").outerHTML.slice(0,2000) : null,
          buttons
        });
        return;
      }

      if (command.action === "read") {
        const text = latestAssistantText();
        const changed = text !== lastAssistant;
        if (changed) lastAssistant = text;
        await complete(command.command_id, {
          ok: true, text, changed, url: location.href, title: document.title
        });
        return;
      }

      if (command.action === "reply") {
        const composer = await waitForComposer();
        if (!composer) throw new Error("CHAT_COMPOSER_NOT_FOUND_AFTER_15S");
        const text = command.payload?.text || "";
        setComposerText(composer, text);

        const control = await waitForSendControl();
        if (control?.element) {
          control.element.click();
          const verified = await waitForSubmission(text, composer);
          if (!verified) throw new Error("CHAT_SUBMISSION_NOT_VERIFIED");
          await complete(command.command_id, {ok: true, sent: true, ui_verified: true, method: "button"});
          return;
        }

        // Some ChatGPT builds expose no stable send-button selector. Prefer
        // the containing form's native submission API; only use Enter as the
        // last fallback, and never report success until the user turn appears.
        const form = composer.closest("form");
        if (form && typeof form.requestSubmit === "function") {
          form.requestSubmit();
          const verified = await waitForSubmission(text, composer);
          if (verified) {
            await complete(command.command_id, {ok: true, sent: true, ui_verified: true, method: "form.requestSubmit"});
            return;
          }
        }

        composer.focus();
        const native = await fetch(RELAY + "/native-submit", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({window_title: document.title})
        });
        const nativeResult = await native.json();
        if (!native.ok || !nativeResult.ok) throw new Error(nativeResult.error || "NATIVE_SUBMIT_FAILED");
        const verified = await waitForSubmission(text, composer);
        if (!verified) throw new Error("CHAT_SUBMISSION_NOT_VERIFIED");
        await complete(command.command_id, {ok: true, sent: true, ui_verified: true, method: "native-os-enter"});
        return;
      }

      throw new Error("UNKNOWN_RELAY_ACTION");
    } catch (error) {
      await complete(command.command_id, {
        ok: false, error: String(error?.message || error)
      });
    }
  }

  async function pollCommands() {
    if (!tabId || polling) return;
    polling = true;
    try {
      const response = await fetch(RELAY + "/commands/claim", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({tab_id: tabId})
      });
      const data = await response.json();
      if (data.command) await execute(data.command);
    } catch (_) {
      // Relay may be restarting; the next poll retries.
    } finally {
      polling = false;
    }
  }

  function startPolling() {
    if (polling) return;
    setInterval(pollCommands, 500);
    pollCommands();
  }

  chrome.runtime.sendMessage({type: "BIRDEYE_GET_TAB_ID"}, response => {
    if (!chrome.runtime.lastError && response?.tab_id) {
      tabId = response.tab_id;
      fetch(RELAY + "/tabs/register", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          tab_id: tabId,
          url: location.href,
          title: document.title,
          thread_url: location.href
        })
      }).then(startPolling).catch(startPolling);
    }
  });
})();
