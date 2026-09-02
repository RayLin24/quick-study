const form = document.getElementById("job-form");
const submitBtn = document.getElementById("submit-btn");
const errorEl = document.getElementById("form-error");
const logEl = document.getElementById("log");
const statusEl = document.getElementById("job-status");
const resultEl = document.getElementById("result");
const listEl = document.getElementById("tutorial-list");

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
  if (snap.status === "succeeded" && snap.output_name) {
    resultEl.hidden = false;
    resultEl.className = "result result-card";
    resultEl.innerHTML = "";
    const title = document.createElement("strong");
    title.textContent = "生成成功";
    const link = document.createElement("a");
    link.href = `/t/${encodeURIComponent(snap.output_name)}`;
    link.textContent = `打开教程 ${snap.output_name}`;
    resultEl.appendChild(title);
    resultEl.appendChild(document.createElement("br"));
    resultEl.appendChild(link);
    setStatus("已完成");
    refreshTutorials();
    return;
  }
  if (snap.status === "failed") {
    resultEl.hidden = false;
    resultEl.className = "result result-card is-error";
    resultEl.textContent = snap.error || "生成失败";
    setStatus("失败");
    return;
  }
  resultEl.hidden = true;
  resultEl.className = "result";
}

function connectEvents() {
  const source = new EventSource("/api/jobs/current/events");
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
    if (payload.type === "log") {
      appendLog(payload.line);
    }
    if (payload.type === "done") {
      source.close();
      submitBtn.disabled = false;
      showResult(payload);
    }
  };
  source.onerror = () => {
    source.close();
    submitBtn.disabled = false;
    setStatus("日志连接中断，可刷新页面查看当前状态");
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
  };
  submitBtn.disabled = true;
  setStatus("启动中…");
  logEl.textContent = "";
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
    if (data.logs) {
      data.logs.forEach(appendLog);
    }
    connectEvents();
  } catch (err) {
    submitBtn.disabled = false;
    setStatus("空闲");
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
      submitBtn.disabled = true;
      setStatus("运行中");
      (snap.logs || []).forEach(appendLog);
      connectEvents();
    } else if (snap.status === "succeeded" || snap.status === "failed") {
      (snap.logs || []).forEach(appendLog);
      showResult(snap);
    }
  })
  .catch(() => {});
