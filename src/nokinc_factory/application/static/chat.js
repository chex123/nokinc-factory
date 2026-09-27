const STORAGE_KEY = "nokinc.factory.chat.session.v1";
const MAX_TURNS = 60;
const MAX_CONTEXT_MESSAGES = 12;
const MAX_CONTEXT_CHARS = 24_000;
const ROLE_LABELS = {
  business_analyst: "Business Analyst",
  architect: "Architect",
  code_analyst: "Code Analyst",
};

const elements = {
  account: document.querySelector("#account-action"),
  chatForm: document.querySelector("#chat-form"),
  composerStatus: document.querySelector("#composer-status"),
  connection: document.querySelector("#connection"),
  conversations: document.querySelector("#conversations"),
  empty: document.querySelector("#empty-state"),
  menu: document.querySelector("#menu-toggle"),
  message: document.querySelector("#message"),
  messages: document.querySelector("#messages"),
  mode: document.querySelector("#mode"),
  newChat: document.querySelector("#new-chat"),
  notice: document.querySelector("#notice"),
  pending: document.querySelector("#pending"),
  repositories: document.querySelector("#repositories"),
  send: document.querySelector("#send"),
  session: document.querySelector("#session-state"),
  sidebar: document.querySelector("#sidebar"),
  title: document.querySelector("#conversation-title"),
};

const state = {
  conversations: [],
  activeId: null,
  busy: false,
  budgetExhausted: false,
  signedIn: false,
  repositories: [],
};

function readSession() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || "{}");
    if (!Array.isArray(saved.conversations)) return;
    state.conversations = saved.conversations
      .filter((item) => item && typeof item.id === "string" && Array.isArray(item.turns))
      .slice(0, 20)
      .map((item) => ({
        id: item.id,
        workItemId: typeof item.workItemId === "string" ? item.workItemId : null,
        title: typeof item.title === "string" ? item.title.slice(0, 100) : "Conversation",
        repositories: (Array.isArray(item.repositories)
          ? item.repositories
          : (typeof item.repository === "string" && item.repository ? [item.repository] : []))
          .filter((repository) => typeof repository === "string")
          .slice(0, 8),
        mode: ["auto", "business", "architecture", "coding"].includes(item.mode)
          ? item.mode
          : "auto",
        turns: item.turns
          .filter((turn) => turn && ["user", "assistant"].includes(turn.role))
          .slice(-MAX_TURNS)
          .map((turn) => ({
            role: turn.role,
            content: String(turn.content || "").slice(0, 4000),
            contextContent: typeof turn.contextContent === "string"
              ? turn.contextContent.slice(0, 4000)
              : String(turn.content || "").slice(0, 4000),
            agentRole: ROLE_LABELS[turn.agentRole] ? turn.agentRole : null,
            analysis: turn.analysis && typeof turn.analysis === "object" ? turn.analysis : null,
            businessAnalysis: turn.businessAnalysis && typeof turn.businessAnalysis === "object"
              ? turn.businessAnalysis
              : null,
          })),
      }));
    state.activeId = typeof saved.activeId === "string" ? saved.activeId : null;
  } catch {
    state.conversations = [];
    state.activeId = null;
  }
}

function saveSession() {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify({
      activeId: state.activeId,
      conversations: state.conversations.slice(0, 20),
    }));
  } catch {
    showNotice("This browser could not retain this conversation for the current tab.");
  }
}

function activeConversation() {
  return state.conversations.find((conversation) => conversation.id === state.activeId) || null;
}

function showNotice(message) {
  elements.notice.textContent = message;
  elements.notice.hidden = !message;
}

function setConnection(ready, message) {
  elements.connection.textContent = message;
  elements.connection.dataset.state = ready ? "ready" : "error";
  elements.session.textContent = ready ? "Session ready" : "Sign-in required";
  elements.session.dataset.ready = String(ready);
  elements.composerStatus.textContent = ready
    ? (state.budgetExhausted
      ? "This pilot's authorized model-test allowance has been used."
      : "Code answers use selected repositories; runtime checks need an isolated worker.")
    : "Sign in to start a conversation.";
  elements.account.textContent = ready ? "Sign out" : "Sign in";
  elements.account.href = ready ? "/auth/logout" : "/auth/login";
  elements.message.disabled = !ready || state.busy || state.budgetExhausted;
  elements.send.disabled = !ready || state.busy || state.budgetExhausted;
  elements.repositories.disabled = !ready || state.busy || state.budgetExhausted;
  elements.mode.disabled = !ready || state.busy || state.budgetExhausted;
}

function appendText(parent, text, className) {
  const paragraph = document.createElement("p");
  paragraph.className = className;
  paragraph.textContent = String(text || "");
  parent.append(paragraph);
  return paragraph;
}

function renderCitation(parent, repository, citation, branch) {
  const row = document.createElement("div");
  row.className = "citation";
  const link = document.createElement("a");
  const segments = String(citation.path || "").split("/").map(encodeURIComponent);
  const citationRepository = String(citation.repository || repository || "");
  const safeRepository = citationRepository.split("/").map(encodeURIComponent).join("/");
  const citationBranch = citation.default_branch || branch || "main";
  link.href = `https://github.com/${safeRepository}/blob/${encodeURIComponent(citationBranch)}/${segments.join("/")}#L${citation.line_start}-L${citation.line_end}`;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.textContent = `${citationRepository}:${citation.path}:${citation.line_start}-${citation.line_end}`;
  row.append(link);
  if (citation.quote) {
    const quote = document.createElement("blockquote");
    quote.textContent = citation.quote;
    row.append(quote);
  }
  parent.append(row);
}

function renderAnalysis(parent, analysis) {
  if (!analysis || typeof analysis !== "object") return;
  appendText(parent, analysis.summary, "message-text");
  const claims = Array.isArray(analysis.claims) ? analysis.claims : [];
  if (claims.length) {
    const list = document.createElement("div");
    list.className = "claims";
    for (const claim of claims) {
      const entry = document.createElement("article");
      entry.className = "claim";
      appendText(entry, claim.statement, "");
      for (const citation of Array.isArray(claim.citations) ? claim.citations : []) {
        renderCitation(entry, analysis.repository, citation, analysis.default_branch);
      }
      list.append(entry);
    }
    parent.append(list);
  }
  renderQuestions(parent, analysis.open_questions);
}

function renderQuestions(parent, questions) {
  if (!Array.isArray(questions) || questions.length === 0) return;
  const list = document.createElement("ol");
  list.className = "questions";
  for (const question of questions) {
    const item = document.createElement("li");
    item.textContent = String(question);
    list.append(item);
  }
  parent.append(list);
}

function renderTurn(turn) {
  const article = document.createElement("article");
  article.className = "message";
  article.dataset.role = turn.role;
  const avatar = document.createElement("div");
  avatar.className = "message-avatar";
  avatar.setAttribute("aria-hidden", "true");
  avatar.textContent = turn.role === "user" ? "Y" : "N";
  const body = document.createElement("div");
  body.className = "message-body";
  const heading = document.createElement("div");
  heading.className = "message-heading";
  const name = document.createElement("span");
  name.className = "message-name";
  name.textContent = turn.role === "user" ? "You" : (ROLE_LABELS[turn.agentRole] || "Factory");
  heading.append(name);
  if (turn.role === "assistant" && turn.analysis?.status) {
    const status = document.createElement("span");
    status.className = "message-mode";
    status.textContent = turn.analysis.status === "ANSWERED" ? "Evidence reviewed" : "Needs clarification";
    heading.append(status);
  }
  if (turn.role === "assistant" && turn.businessAnalysis?.status) {
    const status = document.createElement("span");
    status.className = "message-mode";
    status.textContent = turn.businessAnalysis.status === "ELICITING" ? "Clarifying requirements" : "Review required";
    heading.append(status);
  }
  body.append(heading);
  if (turn.role === "assistant" && turn.analysis) {
    renderAnalysis(body, turn.analysis);
  } else {
    appendText(body, turn.content, "message-text");
    if (turn.role === "assistant" && turn.businessAnalysis) {
      renderQuestions(body, turn.businessAnalysis.open_questions);
    }
  }
  article.append(avatar, body);
  return article;
}

function renderConversations() {
  elements.conversations.replaceChildren();
  for (const conversation of state.conversations) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "conversation-link";
    button.setAttribute("aria-current", String(conversation.id === state.activeId));
    const title = document.createElement("span");
    title.textContent = conversation.title || "Conversation";
    button.append(title);
    button.addEventListener("click", () => selectConversation(conversation.id));
    elements.conversations.append(button);
  }
}

function renderConversation() {
  const conversation = activeConversation();
  elements.title.textContent = conversation?.title || "New conversation";
  elements.messages.replaceChildren();
  elements.empty.hidden = Boolean(conversation?.turns.length);
  if (conversation) {
    for (const turn of conversation.turns) elements.messages.append(renderTurn(turn));
    const selectedRepositories = new Set(conversation.repositories);
    for (const option of elements.repositories.options) {
      option.selected = selectedRepositories.has(option.value);
    }
    elements.mode.value = conversation.mode;
  } else {
    for (const option of elements.repositories.options) option.selected = false;
  }
  renderConversations();
  elements.messages.lastElementChild?.scrollIntoView({ block: "end", behavior: "smooth" });
}

function selectConversation(id) {
  state.activeId = id;
  saveSession();
  renderConversation();
  elements.sidebar.dataset.open = "false";
  elements.menu.setAttribute("aria-expanded", "false");
}

function startConversation() {
  showNotice("");
  state.activeId = null;
  for (const option of elements.repositories.options) option.selected = false;
  elements.mode.value = "auto";
  renderConversation();
  elements.message.focus();
  elements.sidebar.dataset.open = "false";
  elements.menu.setAttribute("aria-expanded", "false");
}

function contextHistory(conversation) {
  const selected = conversation?.turns.slice(-MAX_CONTEXT_MESSAGES) || [];
  const history = selected.map((turn) => ({
    role: turn.role,
    content: String(turn.contextContent || turn.content || "").slice(0, 4000),
  }));
  let total = history.reduce((sum, turn) => sum + turn.content.length, 0);
  while (history.length && total > MAX_CONTEXT_CHARS) {
    total -= history.shift().content.length;
  }
  return history;
}

function assistantContext(response) {
  if (response.business_analysis) {
    const questions = response.business_analysis.open_questions || [];
    return [response.reply || "", ...questions.map((question) => `Question: ${question}`)]
      .join("\n")
      .slice(0, 4000);
  }
  if (response.analysis) {
    const claims = (response.analysis.claims || []).map((claim) => claim.statement);
    return [response.analysis.summary || response.reply || "", ...claims].join("\n").slice(0, 4000);
  }
  return String(response.reply || "").slice(0, 4000);
}

function showPending(value) {
  state.busy = value;
  elements.pending.hidden = !value;
  setConnection(state.signedIn, value ? "Working" : (state.signedIn ? "Connected" : "Sign in required"));
}

async function sendMessage(event) {
  event.preventDefault();
  if (!state.signedIn || state.busy) return;
  const message = elements.message.value.trim();
  if (!message) return;
  showNotice("");
  const existing = activeConversation();
  const selectedMode = elements.mode.value;
  const selectedRepositories = Array.from(
    elements.repositories.selectedOptions,
    (option) => option.value,
  );
  const request = {
    message,
    mode: selectedMode,
    repositories: selectedRepositories,
    history: contextHistory(existing),
  };
  if (existing?.workItemId) request.work_item_id = existing.workItemId;
  showPending(true);
  elements.message.value = "";
  try {
    const response = await fetch("/v1/chat", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(request),
    });
    const result = await response.json().catch(() => null);
    if (response.status === 401) {
      window.location.assign("/auth/login");
      return;
    }
    if (!response.ok) {
      if (result?.detail?.code === "CHAT_MODEL_TURN_BUDGET_EXHAUSTED") {
        state.budgetExhausted = true;
        setConnection(true, "Test allowance used");
        showNotice("The authorized model-test allowance for this pilot has been used. No further model calls will be made.");
        elements.message.value = message;
        return;
      }
      if (result?.detail?.code === "RUNTIME_EVIDENCE_NOT_CONFIGURED") {
        showNotice("No isolated local runtime worker is connected. No runtime claim was made; source-backed discussion remains available.");
        elements.message.value = message;
        return;
      }
      const workItemId = result?.detail?.work_item_id;
      if (typeof workItemId === "string" && !existing) {
        const failed = {
          id: workItemId,
          workItemId,
          title: message.slice(0, 64),
          repositories: selectedRepositories,
          mode: selectedMode,
          turns: [],
        };
        state.conversations.unshift(failed);
        state.activeId = failed.id;
        saveSession();
        renderConversation();
      }
      showNotice(typeof result?.detail === "string"
        ? result.detail
        : "The selected role could not answer. Your conversation remains available for retry.");
      elements.message.value = message;
      return;
    }
    if (!result || typeof result.work_item_id !== "string") {
      showNotice("The service returned an invalid conversation response.");
      return;
    }
    let conversation = activeConversation();
    if (!conversation) {
      conversation = {
        id: result.work_item_id,
        workItemId: result.work_item_id,
        title: message.slice(0, 64),
        repositories: selectedRepositories,
        mode: selectedMode,
        turns: [],
      };
      state.conversations.unshift(conversation);
    }
    conversation.workItemId = result.work_item_id;
    conversation.repositories = selectedRepositories;
    conversation.mode = selectedMode;
    conversation.turns.push({ role: "user", content: message });
    if (result.reply || result.analysis || result.business_analysis) {
      conversation.turns.push({
        role: "assistant",
        content: String(result.reply || result.analysis?.summary || ""),
        contextContent: assistantContext(result),
        agentRole: result.agent_role || null,
        analysis: result.analysis || null,
        businessAnalysis: result.business_analysis || null,
      });
    } else {
      showNotice("Intake was recorded, but no agent is configured for this conversation.");
    }
    conversation.turns = conversation.turns.slice(-MAX_TURNS);
    state.activeId = conversation.id;
    saveSession();
    renderConversation();
  } catch {
    showNotice("The Factory could not be reached. Check the connection and retry.");
    elements.message.value = message;
  } finally {
    showPending(false);
    if (state.signedIn) elements.message.focus();
  }
}

async function loadRepositories() {
  try {
    const response = await fetch("/v1/github/repositories", {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    });
    if (response.status === 401) {
      window.location.assign("/auth/login");
      return;
    }
    if (!response.ok) return;
    const result = await response.json();
    state.repositories = Array.isArray(result.repositories) ? result.repositories : [];
    for (const repository of state.repositories) {
      if (typeof repository.repository !== "string") continue;
      const option = document.createElement("option");
      option.value = repository.repository;
      option.textContent = repository.full_name || repository.repository;
      elements.repositories.append(option);
    }
  } catch {
    showNotice("Repository access is temporarily unavailable. Business Analyst chat remains available.");
  }
}

async function initialize() {
  readSession();
  renderConversations();
  elements.chatForm.addEventListener("submit", sendMessage);
  elements.newChat.addEventListener("click", startConversation);
  elements.menu.addEventListener("click", () => {
    const open = elements.sidebar.dataset.open !== "true";
    elements.sidebar.dataset.open = String(open);
    elements.menu.setAttribute("aria-expanded", String(open));
  });
  elements.message.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      elements.chatForm.requestSubmit();
    }
  });

  try {
    const response = await fetch("/v1/status", {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    });
    if (response.status === 401) {
      window.location.assign("/auth/login");
      return;
    }
    if (!response.ok) throw new Error("status unavailable");
    state.signedIn = true;
    setConnection(true, "Connected");
    await loadRepositories();
    if (state.activeId && state.conversations.some((item) => item.id === state.activeId)) {
      renderConversation();
    } else {
      startConversation();
    }
  } catch {
    state.signedIn = false;
    setConnection(false, "Unavailable");
    showNotice("Sign in to continue. The Factory API is not available right now.");
  }
}

void initialize();
