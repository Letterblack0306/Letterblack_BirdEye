(function () {
  const RELAY = "http://127.0.0.1:7726";
  let lastAssistant = "";

  function assistantMessages() {
    const nodes = Array.from(document.querySelectorAll(
      '[data-message-author-role="assistant"], article[data-testid*="conversation-turn"]'
    ));
    return nodes.map(node => ({
      node,
      text: (node.innerText || "").trim()
    })).filter(x => x.text);
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
          ok: true,
          text,
          changed,
          url: location.href,
          title: document.title
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
          setter?.call(composer, text);
          composer.dispatchEvent(new Event("input", {bubbles: true}));
        } else {
          composer.textContent = text;
          composer.dispatchEvent(new InputEvent("input", {
            bubbles: true,
            inputType: "insertText",
            data: text
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
        ok: false,
        error: String(error?.message || error)
      });
    }
  }

  chrome.runtime.onMessage.addListener(message => {
    if (message?.type === "BIRDEYE_RELAY_COMMAND" && message.command) {
      execute(message.command);
    }
  });
})();
