// SmartAssist admin panel logic. Talks to the REAL protected /admin/*
// FastAPI endpoints — no fake data anywhere here.
//
// Security notes:
// - The admin token is kept in sessionStorage (cleared when the tab
//   closes), not localStorage, since an admin session should be
//   shorter-lived than a customer chat session.
// - Every admin fetch() sends "Authorization: Bearer <token>" — the
//   backend (app/admin_auth.py) is what actually enforces this; this
//   file never assumes it's "logged in" just because a token exists
//   client-side. A 401 from any request immediately logs the UI out.
// - All content from the backend (article titles/content, log details)
//   is inserted with textContent, never innerHTML.

const TOKEN_KEY = "smartassist_admin_token";

const loginPanel = document.getElementById("login-panel");
const dashboard = document.getElementById("dashboard");
const loginForm = document.getElementById("login-form");
const loginError = document.getElementById("login-error");
const logoutButton = document.getElementById("logout-button");
const statusBanner = document.getElementById("status-banner");

const articleList = document.getElementById("article-list");
const articleForm = document.getElementById("article-form");
const editorHeading = document.getElementById("editor-heading");
const titleInput = document.getElementById("article-title");
const categoryInput = document.getElementById("article-category");
const contentInput = document.getElementById("article-content");
const saveButton = document.getElementById("save-article-button");
const cancelEditButton = document.getElementById("cancel-edit-button");
const refreshIndexButton = document.getElementById("refresh-index-button");

const logsBody = document.getElementById("logs-body");
const reloadLogsButton = document.getElementById("reload-logs-button");

const feedbackBody = document.getElementById("feedback-body");
const reloadFeedbackButton = document.getElementById("reload-feedback-button");

let editingArticleId = null;

function getToken() {
  try {
    return sessionStorage.getItem(TOKEN_KEY);
  } catch (err) {
    return null;
  }
}

function setToken(token) {
  try {
    sessionStorage.setItem(TOKEN_KEY, token);
  } catch (err) {
    // Admin panel still works for this page load even if storage fails.
  }
}

function clearToken() {
  try {
    sessionStorage.removeItem(TOKEN_KEY);
  } catch (err) {
    // no-op
  }
}

function showStatus(message, isError) {
  statusBanner.textContent = message;
  statusBanner.classList.toggle("error", Boolean(isError));
  statusBanner.hidden = false;
}

function clearStatus() {
  statusBanner.hidden = true;
  statusBanner.textContent = "";
}

function showLoginError(message) {
  loginError.textContent = message;
  loginError.hidden = false;
}

function clearLoginError() {
  loginError.hidden = true;
  loginError.textContent = "";
}

async function adminFetch(path, options = {}) {
  const token = getToken();
  const headers = Object.assign({}, options.headers, {
    Authorization: `Bearer ${token || ""}`,
  });

  const response = await fetch(path, Object.assign({}, options, { headers }));

  if (response.status === 401) {
    // Session expired or invalid — log out and force re-authentication
    // rather than showing confusing partial data.
    clearToken();
    showDashboard(false);
    showLoginError("Your admin session has expired. Please log in again.");
    throw new Error("Unauthorized");
  }

  return response;
}

function showDashboard(isLoggedIn) {
  loginPanel.hidden = isLoggedIn;
  dashboard.hidden = !isLoggedIn;
}

function switchTab(tabId) {
  document.querySelectorAll(".tab-panel").forEach((el) => {
    el.hidden = el.id !== tabId;
  });
  document.querySelectorAll(".tab-button[data-tab]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === tabId);
  });
}

// --- Login -----------------------------------------------------------

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearLoginError();

  const username = document.getElementById("username").value.trim();
  const password = document.getElementById("password").value;

  try {
    const response = await fetch("/admin/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });

    if (!response.ok) {
      showLoginError("Invalid admin credentials.");
      return;
    }

    const data = await response.json();
    setToken(data.token);
    showDashboard(true);
    await loadArticles();
  } catch (err) {
    showLoginError("Can't reach the server. Please try again.");
  }
});

logoutButton.addEventListener("click", () => {
  clearToken();
  showDashboard(false);
});

// --- Knowledge base ----------------------------------------------------

function resetArticleForm() {
  editingArticleId = null;
  articleForm.reset();
  editorHeading.textContent = "Add New Article";
  saveButton.textContent = "Create Article";
  cancelEditButton.hidden = true;
  categoryInput.disabled = false;
}

async function loadArticles() {
  try {
    const response = await adminFetch("/admin/knowledge");
    if (!response.ok) {
      showStatus("Could not load articles.", true);
      return;
    }
    const articles = await response.json();
    articleList.textContent = "";

    for (const article of articles) {
      const li = document.createElement("li");
      const titleEl = document.createElement("span");
      titleEl.textContent = article.title;
      const categoryEl = document.createElement("span");
      categoryEl.className = "article-category";
      categoryEl.textContent = article.category;

      li.appendChild(titleEl);
      li.appendChild(categoryEl);
      li.addEventListener("click", () => loadArticleForEdit(article.id));
      articleList.appendChild(li);
    }
  } catch (err) {
    // adminFetch already handled 401; other errors just leave the list as-is.
  }
}

async function loadArticleForEdit(articleId) {
  try {
    const response = await adminFetch(`/admin/knowledge/${encodeURIComponent(articleId)}`);
    if (!response.ok) {
      showStatus("Could not load that article.", true);
      return;
    }
    const article = await response.json();

    editingArticleId = article.id;
    titleInput.value = article.title;
    categoryInput.value = article.category;
    categoryInput.disabled = true; // category/title are fixed once created; only content is editable
    contentInput.value = article.content;
    editorHeading.textContent = `Edit: ${article.title}`;
    saveButton.textContent = "Save Changes";
    cancelEditButton.hidden = false;
  } catch (err) {
    // 401 already handled by adminFetch
  }
}

cancelEditButton.addEventListener("click", () => {
  resetArticleForm();
});

articleForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearStatus();

  const title = titleInput.value.trim();
  const category = categoryInput.value.trim();
  const content = contentInput.value.trim();

  if (!title || !category || !content) {
    showStatus("Title, category, and content are all required.", true);
    return;
  }

  try {
    let response;
    if (editingArticleId) {
      response = await adminFetch(`/admin/knowledge/${encodeURIComponent(editingArticleId)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
      });
    } else {
      response = await adminFetch("/admin/knowledge", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, category, content }),
      });
    }

    if (!response.ok) {
      const errorBody = await response.json().catch(() => ({}));
      showStatus(errorBody.detail || "Could not save the article.", true);
      return;
    }

    showStatus(editingArticleId ? "Article updated." : "Article created.", false);
    resetArticleForm();
    await loadArticles();
  } catch (err) {
    // 401 already handled
  }
});

refreshIndexButton.addEventListener("click", async () => {
  clearStatus();
  refreshIndexButton.disabled = true;
  try {
    const response = await adminFetch("/admin/knowledge/refresh-index", { method: "POST" });
    const data = await response.json();
    if (data.success) {
      showStatus(`Index refreshed — ${data.articles_indexed} articles indexed.`, false);
    } else {
      showStatus(`Index refresh failed: ${data.error || "unknown error"}`, true);
    }
  } catch (err) {
    // 401 already handled
  } finally {
    refreshIndexButton.disabled = false;
  }
});

// --- Logs ----------------------------------------------------------------

async function loadLogs() {
  try {
    const response = await adminFetch("/admin/logs");
    if (!response.ok) {
      return;
    }
    const logs = await response.json();
    logsBody.textContent = "";

    for (const entry of logs) {
      const row = document.createElement("tr");
      for (const value of [entry.created_at, entry.action, entry.detail, entry.status]) {
        const cell = document.createElement("td");
        cell.textContent = value;
        row.appendChild(cell);
      }
      logsBody.appendChild(row);
    }
  } catch (err) {
    // 401 already handled
  }
}

reloadLogsButton.addEventListener("click", loadLogs);

// --- Feedback (Day 12) -----------------------------------------------

async function loadFeedback() {
  try {
    const response = await adminFetch("/admin/feedback");
    if (!response.ok) {
      return;
    }
    const entries = await response.json();
    feedbackBody.textContent = "";

    for (const entry of entries) {
      const row = document.createElement("tr");
      const values = [
        entry.created_at,
        entry.session_id.slice(0, 8) + "…",
        String(entry.message_id),
        entry.rating,
        entry.comment || "",
      ];
      for (const value of values) {
        const cell = document.createElement("td");
        cell.textContent = value;
        row.appendChild(cell);
      }
      feedbackBody.appendChild(row);
    }
  } catch (err) {
    // 401 already handled by adminFetch
  }
}

reloadFeedbackButton.addEventListener("click", loadFeedback);

// --- Tabs ----------------------------------------------------------------

document.querySelectorAll(".tab-button[data-tab]").forEach((button) => {
  button.addEventListener("click", () => {
    switchTab(button.dataset.tab);
    if (button.dataset.tab === "logs-tab") {
      loadLogs();
    }
    if (button.dataset.tab === "feedback-tab") {
      loadFeedback();
    }
  });
});

// --- Init ------------------------------------------------------------

(function init() {
  const hasToken = Boolean(getToken());
  showDashboard(hasToken);
  if (hasToken) {
    loadArticles();
  }
})();
