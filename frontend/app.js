// SmartAssist chat UI logic.
//
// This talks to the REAL FastAPI backend (/chat and /history) — nothing
// here is a fake/hard-coded response. Session id is kept in
// localStorage so a page refresh continues the same conversation.
//
// Security note: message content is always inserted with textContent,
// never innerHTML — this avoids DOM-based XSS from anything a user (or,
// in principle, a compromised backend response) might send, per the
// project brief's security requirements.

const SESSION_STORAGE_KEY = "smartassist_session_id";

const chatWindow = document.getElementById("chat-window");
const emptyState = document.getElementById("empty-state");
const typingIndicator = document.getElementById("typing-indicator");
const errorBanner = document.getElementById("error-banner");
const composer = document.getElementById("composer");
const messageInput = document.getElementById("message-input");
const sendButton = document.getElementById("send-button");

function getStoredSessionId() {
  try {
    return localStorage.getItem(SESSION_STORAGE_KEY);
  } catch (err) {
    // localStorage can throw in some private-browsing modes — degrade
    // gracefully to "no stored session" instead of crashing the page.
    return null;
  }
}

function storeSessionId(sessionId) {
  try {
    localStorage.setItem(SESSION_STORAGE_KEY, sessionId);
  } catch (err) {
    // If storage isn't available, the chat still works for this page
    // load — it just won't persist across a refresh.
  }
}

function hideEmptyState() {
  if (emptyState) {
    emptyState.hidden = true;
  }
}

function addBubble(role, content, messageId) {
  hideEmptyState();
  const wrapper = document.createElement("div");
  wrapper.className = `bubble-wrapper ${role === "user" ? "user" : "assistant"}`;

  const bubble = document.createElement("div");
  bubble.className = `bubble ${role === "user" ? "user" : "assistant"}`;
  // textContent, not innerHTML: message content is untrusted and must
  // never be interpreted as HTML/JS.
  bubble.textContent = content;
  wrapper.appendChild(bubble);

  // Day 12: feedback controls only make sense on assistant responses
  // that have a real message_id to attach feedback to (the empty-message
  // fallback reply in main.py has no message_id, since nothing was
  // stored for it).
  if (role === "assistant" && messageId != null) {
    wrapper.appendChild(buildFeedbackControls(messageId));
  }

  chatWindow.appendChild(wrapper);
  chatWindow.scrollTop = chatWindow.scrollHeight;
}

function buildFeedbackControls(messageId) {
  const container = document.createElement("div");
  container.className = "feedback-controls";

  const upButton = document.createElement("button");
  upButton.type = "button";
  upButton.className = "feedback-button";
  upButton.textContent = "👍";
  upButton.setAttribute("aria-label", "Mark this response as helpful");

  const downButton = document.createElement("button");
  downButton.type = "button";
  downButton.className = "feedback-button";
  downButton.textContent = "👎";
  downButton.setAttribute("aria-label", "Mark this response as not helpful");

  container.appendChild(upButton);
  container.appendChild(downButton);

  const handleClick = async (rating) => {
    // Disable both buttons immediately — prevents a double-click from
    // firing two submissions, and prevents accidental re-submission
    // while the request is in flight.
    upButton.disabled = true;
    downButton.disabled = true;

    const sessionId = getStoredSessionId();
    if (!sessionId) {
      // Shouldn't normally happen (a session exists by the time a reply
      // is shown), but fail safely rather than sending a bad request.
      showFeedbackResult(container, "Couldn't send feedback — please try again.", true);
      return;
    }

    try {
      const response = await fetch("/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, message_id: messageId, rating }),
      });

      if (!response.ok) {
        showFeedbackResult(container, "Couldn't send feedback — please try again.", true);
        return;
      }

      showFeedbackResult(container, "Thanks for your feedback!", false);
    } catch (err) {
      showFeedbackResult(container, "Couldn't send feedback — please check your connection.", true);
    }
  };

  upButton.addEventListener("click", () => handleClick("helpful"));
  downButton.addEventListener("click", () => handleClick("not_helpful"));

  return container;
}

function showFeedbackResult(container, message, isError) {
  container.textContent = "";
  const label = document.createElement("span");
  label.className = isError ? "feedback-result feedback-error" : "feedback-result";
  label.textContent = message;
  container.appendChild(label);
}

function showTyping() {
  typingIndicator.hidden = false;
}

function hideTyping() {
  typingIndicator.hidden = true;
}

function showError(message) {
  errorBanner.textContent = message;
  errorBanner.hidden = false;
}

function clearError() {
  errorBanner.hidden = true;
  errorBanner.textContent = "";
}

function setSending(isSending) {
  sendButton.disabled = isSending;
  messageInput.disabled = isSending;
}

async function loadHistory(sessionId) {
  try {
    const response = await fetch(`/history?session_id=${encodeURIComponent(sessionId)}`);
    if (!response.ok) {
      // An unknown/expired session on the backend isn't a real error for
      // the UI — just start with an empty conversation.
      return;
    }
    const data = await response.json();
    if (Array.isArray(data.history) && data.history.length > 0) {
      for (const item of data.history) {
        addBubble(item.role, item.content, item.role === "assistant" ? item.id : undefined);
      }
    }
  } catch (err) {
    // History failing to load shouldn't block the user from starting a
    // new conversation — fail silently here, chat still works.
  }
}

async function sendMessage(message) {
  const sessionId = getStoredSessionId();
  const payload = { message };
  if (sessionId) {
    payload.session_id = sessionId;
  }

  let response;
  try {
    response = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch (networkErr) {
    // fetch() itself throwing means the backend is unreachable
    // (offline, server down, CORS, etc.) — a genuine network failure.
    throw new Error("Can't reach SmartAssist right now. Please check your connection and try again.");
  }

  if (!response.ok) {
    // Backend responded, but with an error status — show a friendly
    // message instead of exposing any internal error detail.
    throw new Error("SmartAssist had trouble responding. Please try again in a moment.");
  }

  let data;
  try {
    data = await response.json();
  } catch (parseErr) {
    throw new Error("Received an unexpected response from the server.");
  }

  if (!data || typeof data.reply !== "string" || typeof data.session_id !== "string") {
    throw new Error("Received an unexpected response from the server.");
  }

  return data;
}

composer.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearError();

  const message = messageInput.value.trim();
  if (!message) {
    // Empty message: nothing to send, nothing to show — just ignore
    // the submit rather than hitting the API or showing an error.
    return;
  }

  addBubble("user", message);
  messageInput.value = "";
  setSending(true);
  showTyping();

  try {
    const data = await sendMessage(message);
    storeSessionId(data.session_id);
    addBubble("assistant", data.reply, data.message_id);
  } catch (err) {
    showError(err.message || "Something went wrong. Please try again.");
  } finally {
    hideTyping();
    setSending(false);
    messageInput.focus();
  }
});

// On page load: if we already have a session, restore its history.
(function init() {
  const existingSessionId = getStoredSessionId();
  if (existingSessionId) {
    loadHistory(existingSessionId);
  }
  messageInput.focus();
})();
