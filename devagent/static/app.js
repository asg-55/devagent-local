const state = { projects: [], project: null, session: null, files: [] };
const $ = (selector) => document.querySelector(selector);

async function api(url, options = {}) {
  const response = await fetch(url, { headers: { "Content-Type": "application/json" }, ...options });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

function addMessage(role, content, files = [], issues = [], steps = [], screenshots = [], modelUsed = "") {
  const element = document.createElement("div");
  element.className = `message ${role}`;
  element.textContent = content;
  if (files.length) {
    const meta = document.createElement("span");
    meta.className = "meta";
    meta.textContent = `Изменено: ${files.map((item) => item.path).join(", ")}`;
    element.append(meta);
  }
  if (role === "assistant" && modelUsed) {
    const modelMeta = document.createElement("span");
    modelMeta.className = "meta model-used";
    modelMeta.textContent = `Модель: ${modelUsed}`;
    element.append(modelMeta);
  }
  if (issues.length) {
    const warning = document.createElement("span");
    warning.className = "meta warning";
    warning.textContent = `Проверка: ${issues.map((item) => item.message).join("; ")}`;
    element.append(warning);
  }
  if (steps.length) {
    const details = document.createElement("details");
    details.className = "agent-steps";
    const summary = document.createElement("summary");
    summary.textContent = `Действия агента: ${steps.length}`;
    details.append(summary);
    for (const step of steps) {
      const row = document.createElement("div");
      row.className = `agent-step ${step.status === "error" ? "failed" : ""}`;
      row.textContent = `${step.step}. ${step.tool} — ${step.result}`;
      details.append(row);
    }
    element.append(details);
  }
  if (screenshots.length) {
    const gallery = document.createElement("div");
    gallery.className = "browser-screenshots";
    for (const path of screenshots) {
      const link = document.createElement("a");
      link.href = `/artifacts/${path.split("/").map(encodeURIComponent).join("/")}`;
      link.target = "_blank";
      link.rel = "noopener";
      const image = document.createElement("img");
      image.src = link.href;
      image.alt = `Browser check: ${path.split("/").pop()}`;
      link.append(image);
      gallery.append(link);
    }
    element.append(gallery);
  }
  $("#chat").append(element);
  $("#chat").scrollTop = $("#chat").scrollHeight;
}

async function loadHealth() {
  try {
    const data = await api("/api/health");
    const select = $("#model");
    select.replaceChildren();
    for (const name of data.models) {
      const option = new Option(name, name, false, name === document.body.dataset.defaultModel);
      select.add(option);
    }
    $("#status").textContent = data.ollama === "ok" ? "Ollama подключена" : data.ollama;
    $("#status").className = `status ${data.ollama === "ok" ? "ok" : "bad"}`;
  } catch (error) {
    $("#status").textContent = error.message;
    $("#status").className = "status bad";
  }
}

async function loadProjects() {
  state.projects = (await api("/api/projects")).projects;
  renderProjects();
  if (state.projects.length && !state.project) await selectProject(state.projects[0]);
}

function renderProjects() {
  const list = $("#projects");
  list.replaceChildren();
  for (const project of state.projects) {
    const button = document.createElement("button");
    button.className = `project-item ${state.project?.id === project.id ? "active" : ""}`;
    button.textContent = project.title;
    button.onclick = () => selectProject(project);
    list.append(button);
  }
}

async function selectProject(project) {
  state.project = project;
  const sessions = (await api(`/api/projects/${project.id}/sessions`)).sessions;
  state.session = sessions[0] || (await api(`/api/projects/${project.id}/sessions`, {
    method: "POST", body: JSON.stringify({ model: $("#model").value })
  })).session;
  $("#empty").classList.add("hidden");
  $("#workspace").classList.remove("hidden");
  $("#project-title").textContent = project.title;
  renderProjects();
  await Promise.all([loadFiles(), loadMessages()]);
}

async function loadMessages() {
  $("#chat").replaceChildren();
  const messages = (await api(`/api/sessions/${state.session.id}/messages`)).messages;
  for (const message of messages) {
    addMessage(
      message.role, message.content, message.metadata.files || [], message.metadata.issues || [],
      message.metadata.steps || [], message.metadata.screenshots || [], message.metadata.model_used || ""
    );
  }
}

async function loadFiles() {
  state.files = (await api(`/api/projects/${state.project.id}/files`)).files;
  const list = $("#files");
  list.replaceChildren();
  if (!state.files.length) list.innerHTML = '<span class="muted">Проект пока пуст</span>';
  for (const file of state.files) {
    const link = document.createElement("a");
    link.className = "file-item";
    link.textContent = file.path;
    link.href = `/preview/${state.project.id}/${encodeURI(file.path)}`;
    link.target = "_blank";
    link.rel = "noopener";
    list.append(link);
  }
  const index = state.files.find((file) => file.path === "index.html");
  $("#preview").disabled = !index;
  $("#preview").onclick = () => window.open(`/preview/${state.project.id}/index.html`, "_blank", "noopener");
}

function openProjectDialog() {
  $("#dialog-error").textContent = "";
  $("#project-dialog").showModal();
  $("#new-title").focus();
}

$("#new-title").addEventListener("input", (event) => {
  $("#new-slug").value = event.target.value.toLowerCase().trim()
    .replace(/[^a-z0-9_-]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 63);
});

$("#project-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const data = await api("/api/projects", { method: "POST", body: JSON.stringify({
      title: $("#new-title").value, slug: $("#new-slug").value
    }) });
    $("#project-dialog").close();
    state.projects.unshift(data.project);
    state.session = data.session;
    await selectProject(data.project);
  } catch (error) {
    $("#dialog-error").textContent = error.message;
  }
});

$("#composer").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = $("#message");
  const message = input.value.trim();
  if (!message || !state.session) return;
  addMessage("user", message);
  input.value = "";
  $("#send").disabled = true;
  $("#send").textContent = "Работаю…";
  try {
    const data = await api("/api/chat", { method: "POST", body: JSON.stringify({
      session_id: state.session.id, message, model: $("#model").value
    }) });
    addMessage("assistant", data.message, data.files, data.issues, data.steps, data.screenshots, data.model_used);
    await loadFiles();
  } catch (error) {
    addMessage("error", error.message);
  } finally {
    $("#send").disabled = false;
    $("#send").textContent = "Запустить";
  }
});

$("#new-project").onclick = openProjectDialog;
$("#empty-create").onclick = openProjectDialog;
loadHealth();
loadProjects().catch((error) => addMessage("error", error.message));
