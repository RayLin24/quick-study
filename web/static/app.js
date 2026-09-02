const form = document.getElementById("job-form");
const submitBtn = document.getElementById("submit-btn");
const cancelBtn = document.getElementById("cancel-btn");
const errorEl = document.getElementById("form-error");
const logEl = document.getElementById("log");
const statusEl = document.getElementById("job-status");
const resultEl = document.getElementById("result");
const listEl = document.getElementById("tutorial-list");

let eventSource = null;
let logCursor = 0;

function sourceType() {
  return form.querySelector('input[name="source_type"]:checked').value;
}

function syncSourceFields() {
  const type = sourceType();
  document.querySelectorAll("[data-for]").forEach((node) => {
    node.hidden = node.dataset.for !== type;
  });
}

function setError(message) {
  if (!message) {
    errorEl.hidden = true;
    errorEl.textContent = "";
    return;
  }
  errorEl.hidden = false;
  errorEl.textContent = message;
}

function setStatus(text) {
  statusEl.textContent = text;
}

function setRunning(running) {
  submitBtn.disabled = running;
  cancelBtn.hidden = !running;
}

function appendLog(line) {
  logEl.textContent += `${line}\n`;
  logEl.scrollTop = logEl.scrollHeight;
}

async function refreshTutorials() {
  const res = await fetch("/api/tutorials");
  const data = await res.json();
  if (!data.items.length) {
    listEl.innerHTML = '<li class="empty"></li>';
    listEl.querySelector(".empty").textContent = "还没有教程。生成完成后会出现在这里。";
    return;
  }
  listEl.innerHTML = "";
  for (const item of data.items) {
    const li = document.createElement("li");
    const a = document.createElement("a");
    a.href = `/t/${encodeURIComponent(item.name)}`;
    a.textContent = item.name;
    li.appendChild(a);
    listEl.appendChild(li);
  }
}

function showResult(snap) {
  if (snap.status === "succeeded") {
    resultEl.hidden = false;
    resultEl.className = "result result-card";
    resultEl.innerHTML = "";
    const title = document.createElement("strong");
    title.textContent = "生成成功";
    resultEl.appendChild(title);
    const meta = [];
    if (snap.file_count != null) {
      meta.push(`${snap.file_count} 个文件`);
    }
    if (snap.map_mode != null) {
      meta.push(`map_mode=${snap.map_mode ? "true" : "false"}`);
    }
    if (meta.length) {
      resultEl.appendChild(document.createElement("br"));
      const info = document.createElement("span");
      info.textContent = meta.join(" · ");
      resultEl.appendChild(info);
    }
    if (snap.output_name) {
      resultEl.appendChild(document.createElement("br"));
      const link = document.createElement("a");
      link.href = `/t/${encodeURIComponent(snap.output_name)}`;
      link.textContent = `打开教程 ${snap.output_name}`;
      resultEl.appendChild(link);
    }
    setStatus("已完成");
    refreshTutorials();
    return;
  }
  if (snap.status === "failed" || snap.status === "cancelled") {
    resultEl.hidden = false;
    resultEl.className = "result result-card is-error";
    resultEl.textContent = snap.error || (snap.status === "cancelled" ? "任务已取消" : "生成失败");
    setStatus(snap.status === "cancelled" ? "已取消" : "失败");
    return;
  }
  resultEl.hidden = true;
  resultEl.className = "result";
}

function closeEvents() {
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
}

function connectEvents(after) {
  closeEvents();
  const cursor = after == null ? logCursor : after;
  const source = new EventSource(`/api/jobs/current/events?after=${encodeURIComponent(cursor)}`);
  eventSource = source;
  source.onmessage = (event) => {
    if (!event.data) {
      return;
    }
    let payload;
    try {
      payload = JSON.parse(event.data);
    } catch {
      return;
    }
    if (payload.type === "heartbeat") {
      return;
    }
    if (payload.type === "log") {
      appendLog(payload.line);
      logCursor += 1;
    }
    if (payload.type === "done") {
      closeEvents();
      setRunning(false);
      showResult(payload);
    }
  };
  source.onerror = async () => {
    // Disconnect is not completion. Only `type:done` may finish the UI.
    closeEvents();
    try {
      const res = await fetch("/api/jobs/current");
      const snap = await res.json();
      if (snap.status === "running") {
        logCursor = snap.log_cursor || logCursor;
        connectEvents(logCursor);
        return;
      }
    } catch {
      // stay disconnected until the user retries
    }
    setStatus("日志连接中断，将在任务仍运行时自动重连");
  };
}

form.addEventListener("change", (event) => {
  if (event.target.name === "source_type") {
    syncSourceFields();
  }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  setError("");
  resultEl.hidden = true;
  const body = {
    source_type: sourceType(),
    repo_url: form.repo_url.value.trim(),
    local_dir: form.local_dir.value.trim(),
    language: form.language.value,
    name: form.name.value.trim(),
    github_token: form.github_token.value.trim(),
    max_abstractions: Number(form.max_abstractions.value || 10),
    include: form.include.value.trim(),
    exclude: form.exclude.value.trim(),
    max_size: form.max_size.value ? Number(form.max_size.value) : null,
  };
  setRunning(true);
  setStatus("启动中…");
  logEl.textContent = "";
  logCursor = 0;
  try {
    const res = await fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || "无法启动任务");
    }
    setStatus("运行中");
    logCursor = data.log_cursor || 0;
    if (data.logs) {
      data.logs.forEach(appendLog);
    }
    connectEvents(logCursor);
  } catch (err) {
    setRunning(false);
    setStatus("空闲");
    setError(err.message);
  }
});

cancelBtn.addEventListener("click", async () => {
  try {
    const res = await fetch("/api/jobs/current/cancel", { method: "POST" });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || "无法取消");
    }
    setStatus("正在取消…");
  } catch (err) {
    setError(err.message);
  }
});

syncSourceFields();

fetch("/api/jobs/current")
  .then(async (res) => {
    const text = await res.text();
    return text ? JSON.parse(text) : { status: "idle" };
  })
  .then((snap) => {
    if (snap.status === "running") {
      setRunning(true);
      setStatus("运行中");
      logCursor = snap.log_start || 0;
      (snap.logs || []).forEach(appendLog);
      logCursor = snap.log_cursor || logCursor;
      connectEvents(logCursor);
    } else if (snap.status === "succeeded" || snap.status === "failed" || snap.status === "cancelled") {
      (snap.logs || []).forEach(appendLog);
      showResult(snap);
    }
  })
  .catch(() => {});
