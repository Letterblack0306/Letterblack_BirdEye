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
      'textarea, [contenteditable="true"][role="textbox"], div[contenteditable="true"]'
    );
  }

  function findSendButton() {
    return document.querySelector(
      'button[data-testid="send-button"], button[aria-label*="Send" i], button[type="submit"]'
    );
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
        const composer = findComposer();
        if (!composer) throw new Error("CHAT_COMPOSER_NOT_FOUND");
        const text = command.payload?.text || "";
        composer.focus();

        if (composer.tagName === "TEXTAREA") {
          const setter = Object.getOwnPropertyDescriptor(
            HTMLTextAreaElement.prototype, "value"
          )?.set;
          if (!setter) throw new Error("TEXTAREA_VALUE_SETTER_NOT_FOUND");
          setter.call(composer, text);
          composer.dispatchEvent(new Event("input", {bubbles: true}));
        } else {
          composer.textContent = text;
          composer.dispatchEvent(new InputEvent("input", {
            bubbles: true, inputType: "insertText", data: text
          }));
        }

        const send = findSendButton();
        if (!send) throw new Error("CHAT_SEND_BUTTON_NOT_FOUND");
        send.click();
        await complete(command.command_id, {ok: true, sent: true});
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
