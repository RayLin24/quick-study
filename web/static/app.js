const form = document.getElementById("job-form");
const submitBtn = document.getElementById("submit-btn");
const previewBtn = document.getElementById("preview-btn");
const cancelBtn = document.getElementById("cancel-btn");
const errorEl = document.getElementById("form-error");
const logEl = document.getElementById("log");
const statusEl = document.getElementById("job-status");
const resultEl = document.getElementById("result");
const listEl = document.getElementById("tutorial-list");
const stepProgress = document.getElementById("step-progress");
const stepBarFill = document.getElementById("step-bar-fill");
const stepList = document.getElementById("step-list");
const previewCard = document.getElementById("preview-card");
const outlineCard = document.getElementById("outline-card");
const replaceBtn = document.getElementById("replace-btn");

const STEP_ORDER = ["fetch", "identify", "relationships", "order", "outline", "write", "combine"];

let eventSource = null;
let logCursor = 0;
let lastPreview = null;
let costRates = { rate_prompt_per_m: 0.15, rate_completion_per_m: 0.6, provider: "OPENROUTER" };

function estimateCalls(n) {
  const chapters = Math.max(1, Number(n) || 10);
  return {
    identify: 1,
    relationships: 1,
    order: 1,
    write_chapters: chapters,
    total: 3 + chapters,
  };
}

function estimateUsd(calls) {
  const avg = 2500;
  const prompt = calls * avg * 0.7;
  const completion = calls * avg * 0.3;
  const usd =
    (prompt / 1e6) * (costRates.rate_prompt_per_m || 0) +
    (completion / 1e6) * (costRates.rate_completion_per_m || 0);
  return {
    provider: costRates.provider || "OPENROUTER",
    usd: Math.round(usd * 1e6) / 1e6,
    note: "按供应商公开标价估算，实际账单以供应商为准",
    estimated: true,
  };
}

function applyEstimateToPreview() {
  if (!previewCard || previewCard.hidden || !lastPreview) return;
  const n = Number((document.getElementById("max_abstractions") || {}).value || 10);
  lastPreview.estimated_calls = estimateCalls(n);
  lastPreview.cost = estimateUsd(lastPreview.estimated_calls.total);
  showPreview(lastPreview);
}

fetch("/api/config")
  .then((res) => res.json())
  .then((cfg) => {
    if (cfg && cfg.cost_rates) costRates = { ...costRates, ...cfg.cost_rates };
  })
  .catch(() => {});

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
  const pauseBtn = document.getElementById("pause-btn");
  const resumeBtn = document.getElementById("resume-btn");
  if (pauseBtn) pauseBtn.hidden = !running;
  if (resumeBtn) resumeBtn.hidden = true;
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
    const name = document.createElement("span");
    name.className = "card-name";
    name.textContent = item.name;
    const meta = document.createElement("span");
    meta.className = "card-meta";
    const bits = [item.language || "语言未知"];
    if (item.mtime) bits.push(item.mtime);
    if (item.repo_url) bits.push(item.repo_url);
    meta.textContent = bits.join(" · ");
    a.appendChild(name);
    a.appendChild(meta);
    const actions = document.createElement("div");
    actions.className = "card-actions";
    const del = document.createElement("button");
    del.type = "button";
    del.textContent = "删除";
    del.addEventListener("click", async (ev) => {
      ev.preventDefault();
      if (!confirm(`删除教程 ${item.name}？`)) return;
      const res = await fetch(`/api/tutorials/${encodeURIComponent(item.name)}`, { method: "DELETE" });
      if (res.ok) refreshTutorials();
    });
    const ren = document.createElement("button");
    ren.type = "button";
    ren.textContent = "重命名";
    ren.addEventListener("click", async (ev) => {
      ev.preventDefault();
      const next = prompt("新名称", item.name);
      if (!next || next === item.name) return;
      const res = await fetch(`/api/tutorials/${encodeURIComponent(item.name)}/rename`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: next }),
      });
      if (res.ok) refreshTutorials();
    });
    actions.appendChild(del);
    actions.appendChild(ren);
    li.appendChild(a);
    li.appendChild(actions);
    listEl.appendChild(li);
  }
}

function setStep(step) {
  if (!stepProgress || !step) {
    return;
  }
  stepProgress.hidden = false;
  const index = STEP_ORDER.indexOf(step);
  const current = index >= 0 ? index + 1 : 0;
  if (stepBarFill) {
    stepBarFill.style.width = `${(current / STEP_ORDER.length) * 100}%`;
  }
  const bar = stepProgress.querySelector(".step-bar");
  if (bar) {
    bar.setAttribute("aria-valuenow", String(current));
  }
  if (stepList) {
    stepList.querySelectorAll("li").forEach((li) => {
      const name = li.getAttribute("data-step");
      const pos = STEP_ORDER.indexOf(name);
      li.classList.toggle("is-done", pos >= 0 && pos < index);
      li.classList.toggle("is-current", name === step);
    });
  }
}

function usageText(usage) {
  if (!usage) {
    return "用量未知";
  }
  if (usage.unknown || (usage.total_tokens == null && !usage.calls)) {
    return "用量未知";
  }
  const parts = [];
  if (usage.unknown) {
    parts.push("用量未知");
  } else if (usage.total_tokens != null) {
    parts.push(`tokens ${usage.prompt_tokens ?? "?"}+${usage.completion_tokens ?? "?"}=${usage.total_tokens}`);
  }
  if (usage.max_tokens) {
    parts.push(`max_tokens=${usage.max_tokens}`);
  }
  if (usage.calls) {
    parts.push(`${usage.calls} 次调用`);
  }
  if (usage.by_stage && typeof usage.by_stage === "object") {
    const stages = Object.entries(usage.by_stage)
      .map(([k, v]) => `${k}:${typeof v === "object" ? v.calls || 0 : v}`)
      .join(",");
    if (stages) parts.push(stages);
  }
  return parts.join(" · ");
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
    const usageLine = usageText(snap.usage);
    if (usageLine) {
      meta.push(usageLine);
    } else {
      meta.push("用量未知");
    }
    if (snap.retry_after) {
      meta.push(`GitHub 限流，约 ${snap.retry_after}s 后可重试`);
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
      const autoOpen = document.getElementById("auto-open");
      if (autoOpen && autoOpen.checked) {
        location.href = link.href;
      }
    }
    setStatus("已完成");
    refreshTutorials();
    return;
  }
  if (snap.status === "failed" || snap.status === "cancelled") {
    resultEl.hidden = false;
    resultEl.className = "result result-card is-error";
    resultEl.textContent = "";
    const errText = snap.error || (snap.status === "cancelled" ? "任务已取消" : "生成失败");
    const span = document.createElement("span");
    span.textContent = errText;
    resultEl.appendChild(span);
    if (errText.includes("QUICK_STUDY_ERROR")) {
      const copy = document.createElement("button");
      copy.type = "button";
      copy.id = "copy-error";
      copy.textContent = "复制错误摘录";
      copy.addEventListener("click", async () => {
        const excerpt = (errText.match(/QUICK_STUDY_ERROR:[^\n]+/) || [errText]).slice(0, 1)[0];
        try {
          await navigator.clipboard.writeText(excerpt);
          copy.textContent = "已复制";
        } catch {
          copy.textContent = "复制失败";
        }
      });
      resultEl.appendChild(copy);
    }
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
      maybeShowOutline();
      return;
    }
    if (payload.type === "log") {
      appendLog(payload.line);
      logCursor += 1;
      if (payload.step) {
        setStep(payload.step);
      } else {
        const match = /QUICK_STUDY_STEP:\s*(\w+)/.exec(payload.line || "");
        if (match) setStep(match[1]);
      }
      if (/QUICK_STUDY_OUTLINE:/.test(payload.line || "") || payload.step === "outline") {
        maybeShowOutline();
      }
    }
    if (payload.type === "done") {
      closeEvents();
      setRunning(false);
      if (payload.step) setStep(payload.step);
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

document.querySelectorAll(".preset").forEach((btn) => {
  btn.addEventListener("click", () => {
    form.include.value = btn.getAttribute("data-include") || "";
  });
});

if (replaceBtn) {
  replaceBtn.addEventListener("click", async () => {
    setError("");
    const body = jobBody();
    body.replace = true;
    setRunning(true);
    setStatus("取消并开始…");
    try {
      const res = await fetch("/api/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "无法启动任务");
      setStatus("运行中");
      logEl.textContent = "";
      logCursor = data.log_cursor || 0;
      connectEvents(logCursor);
    } catch (err) {
      setRunning(false);
      setError(err.message);
    }
  });
}

function jobBody() {
  return {
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
    queue: !!(document.getElementById("queue-job") && document.getElementById("queue-job").checked),
    resume: !!(form.resume && form.resume.checked),
    incremental: !!(form.incremental && form.incremental.checked),
    overview_only: !!(form.overview_only && form.overview_only.checked),
    strategy: form.strategy ? form.strategy.value : "",
    bilingual: !!(form.bilingual && form.bilingual.checked),
    pagerank_order: !!(form.pagerank_order && form.pagerank_order.checked),
    learning_goal: form.learning_goal ? form.learning_goal.value.trim() : "",
    replace: false,
    confirm_outline: !!(form.confirm_outline && form.confirm_outline.checked),
  };
}

function showPreview(data) {
  if (!previewCard) return;
  previewCard.hidden = false;
  previewCard.className = data.ok ? "preview-card" : "preview-card is-warn";
  const est = data.estimated_calls || {};
  const sample = (data.files_sample || []).slice(0, 12).map((p) => `· ${p}`).join("<br>");
  previewCard.innerHTML = "";
  const title = document.createElement("strong");
  title.textContent = "预检结果（只爬不写）";
  previewCard.appendChild(title);
  const info = document.createElement("p");
  info.textContent = [
    `文件 ${data.file_count ?? "?"}`,
    `估调用 ${est.total ?? "?"}`,
    `map_mode=${data.map_mode ? "true" : "false"}`,
    data.truncated_files ? `上下文截断 ${data.truncated_files}` : null,
  ].filter(Boolean).join(" · ");
  previewCard.appendChild(info);
  if (data.cost) {
    const cost = document.createElement("p");
    cost.className = "ask-hint";
    cost.textContent = `预估费用 ${data.cost.provider} $${data.cost.usd}（${data.cost.note}）`;
    previewCard.appendChild(cost);
  }
  if (data.warning) {
    const warn = document.createElement("p");
    warn.className = "error";
    warn.textContent = data.warning;
    previewCard.appendChild(warn);
  }
  if (sample) {
    const list = document.createElement("p");
    list.className = "preview-files";
    list.innerHTML = sample;
    previewCard.appendChild(list);
  }
  const hint = document.createElement("p");
  hint.textContent = data.ok ? "确认无误后点「确认生成」。" : "缩小 include 后再预检。";
  previewCard.appendChild(hint);
}

function showOutlineGate(outline) {
  if (!outlineCard || !outline) return;
  outlineCard.hidden = false;
  outlineCard.className = "preview-card";
  outlineCard.innerHTML = "";
  const title = document.createElement("strong");
  title.textContent = "大纲确认（写章前）";
  outlineCard.appendChild(title);
  const info = document.createElement("p");
  const remaining = (outline.remaining_calls || {}).remaining;
  const est = outline.estimated_calls || {};
  info.textContent = [
    `${outline.chapter_count ?? (outline.abstractions || []).length} 个抽象`,
    remaining != null ? `剩余写章调用 ${remaining}` : null,
    est.total != null ? `全程估调用 ${est.total}` : null,
  ].filter(Boolean).join(" · ");
  outlineCard.appendChild(info);
  const list = document.createElement("ol");
  list.className = "preview-files";
  (outline.abstractions || []).forEach((item) => {
    const li = document.createElement("li");
    li.textContent = `${item.name || "?"} — ${item.description || ""}`;
    list.appendChild(li);
  });
  outlineCard.appendChild(list);
  const btn = document.createElement("button");
  btn.type = "button";
  btn.textContent = "确认大纲，开始写章";
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    try {
      const res = await fetch("/api/jobs/current/confirm-outline", { method: "POST" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "确认失败");
      outlineCard.hidden = true;
      setStatus("写章中…");
    } catch (err) {
      btn.disabled = false;
      setError(err.message);
    }
  });
  outlineCard.appendChild(btn);
}

async function maybeShowOutline() {
  try {
    const res = await fetch("/api/jobs/current/outline");
    const data = await res.json();
    if (data && data.awaiting && data.outline) {
      setStep("outline");
      setStatus("等待大纲确认");
      showOutlineGate(data.outline);
    }
  } catch {
    // ignore
  }
}

async function runPreview() {
  setError("");
  if (previewCard) {
    previewCard.hidden = false;
    previewCard.className = "preview-card";
    previewCard.textContent = "正在预检（只爬不写）…";
  }
  const res = await fetch("/api/jobs/preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(jobBody()),
  });
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.detail || "预检失败");
  }
  lastPreview = data;
  showPreview(data);
  return data;
}

if (previewBtn) {
  previewBtn.addEventListener("click", async () => {
    previewBtn.disabled = true;
    try {
      await runPreview();
      setStatus("预检完成");
    } catch (err) {
      if (previewCard) previewCard.hidden = true;
      setError(err.message);
    } finally {
      previewBtn.disabled = false;
    }
  });
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  setError("");
  resultEl.hidden = true;
  const body = jobBody();
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
    if (data.queued || data.status === "queued") {
      setRunning(false);
      setStatus("已排队");
      const qel = document.getElementById("queue-status");
      if (qel) qel.textContent = `队列位置 ${data.position || "?"}`;
      return;
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

const pauseBtn = document.getElementById("pause-btn");
const resumeBtn = document.getElementById("resume-btn");
if (pauseBtn) {
  pauseBtn.addEventListener("click", async () => {
    const res = await fetch("/api/jobs/current/pause", { method: "POST" });
    if (res.ok) {
      setStatus("已暂停");
      pauseBtn.hidden = true;
      if (resumeBtn) resumeBtn.hidden = false;
    }
  });
}
if (resumeBtn) {
  resumeBtn.addEventListener("click", async () => {
    const res = await fetch("/api/jobs/current/resume", { method: "POST" });
    if (res.ok) {
      setStatus("运行中");
      resumeBtn.hidden = true;
      if (pauseBtn) pauseBtn.hidden = false;
    }
  });
}

const homeLang = document.getElementById("lang-toggle");
if (homeLang) {
  homeLang.addEventListener("click", () => {
    const next = (homeLang.getAttribute("data-lang") || "zh") === "zh" ? "en" : "zh";
    document.cookie = `qs_lang=${next};path=/;samesite=lax`;
    location.reload();
  });
}

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
      if (snap.step) setStep(snap.step);
      connectEvents(logCursor);
    } else if (snap.status === "succeeded" || snap.status === "failed" || snap.status === "cancelled") {
      (snap.logs || []).forEach(appendLog);
      showResult(snap);
    }
  })
  .catch(() => {});

const smokeBtn = document.getElementById("smoke-btn");
if (smokeBtn) {
  smokeBtn.addEventListener("click", () => {
    const repo = document.querySelector('input[name="source_type"][value="repo"]');
    if (repo) repo.checked = true;
    syncSourceFields();
    form.repo_url.value = "https://github.com/octocat/Hello-World";
    form.include.value = "*.md";
    form.max_abstractions.value = "4";
    setStatus("已预填公开仓，可先预检");
  });
}

const patBtn = document.getElementById("pat-check");
if (patBtn) {
  patBtn.addEventListener("click", async () => {
    const token = (document.getElementById("pat-input") || {}).value || "";
    const res = await fetch("/api/pat/check", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
    const data = await res.json();
    const hint = document.getElementById("pat-hint");
    if (hint) hint.textContent = data.hint || "";
  });
}

const zipInput = document.getElementById("zip-upload");
if (zipInput) {
  zipInput.addEventListener("change", async () => {
    if (!zipInput.files || !zipInput.files[0]) return;
    const fd = new FormData();
    fd.append("file", zipInput.files[0]);
    setStatus("上传 ZIP…");
    const res = await fetch("/api/jobs/upload", { method: "POST", body: fd });
    const data = await res.json();
    if (!res.ok) {
      setError(data.detail || "上传失败");
      return;
    }
    setRunning(true);
    connectEvents(data.log_cursor || 0);
  });
}

const maxAbs = document.getElementById("max_abstractions");
if (maxAbs) {
  const hint = document.createElement("p");
  hint.id = "cost-hint";
  hint.className = "ask-hint";
  maxAbs.parentElement.appendChild(hint);
  const syncHint = () => {
    const n = Number(maxAbs.value || 10);
    const est = estimateCalls(n);
    const cost = estimateUsd(est.total);
    hint.textContent = `估 ${est.total} 次 · $${cost.usd}` + (n > 12 ? ` · max_abstractions=${n} 会增加写章次数与费用。` : "");
    applyEstimateToPreview();
  };
  maxAbs.addEventListener("input", syncHint);
  maxAbs.addEventListener("change", syncHint);
  syncHint();
}

const searchForm = document.getElementById("search-form");
const searchQ = document.getElementById("search-q");
const searchHits = document.getElementById("search-hits");
if (searchForm && searchQ && searchHits) {
  searchForm.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const res = await fetch(`/api/search?q=${encodeURIComponent(searchQ.value)}`);
    const data = await res.json();
    searchHits.hidden = false;
    searchHits.innerHTML = "";
    (data.items || []).forEach((item) => {
      const li = document.createElement("li");
      const a = document.createElement("a");
      a.href = item.href;
      a.textContent = `${item.tutorial} / ${item.title}`;
      li.appendChild(a);
      searchHits.appendChild(li);
    });
    if (!(data.items || []).length) {
      searchHits.innerHTML = "<li class='empty'>没有命中</li>";
    }
  });
}
const presetExport = document.getElementById("preset-export");
if (presetExport) {
  presetExport.addEventListener("click", async () => {
    const body = jobBody();
    const res = await fetch("/api/presets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: "last", payload: body }),
    });
    const data = await res.json();
    const blob = new Blob([JSON.stringify(data.preset || body, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "quick-study-preset.json";
    a.click();
  });
}
const presetFile = document.getElementById("preset-file");
if (presetFile) {
  presetFile.addEventListener("change", async () => {
    if (!presetFile.files || !presetFile.files[0]) return;
    const text = await presetFile.files[0].text();
    const data = JSON.parse(text);
    Object.entries(data).forEach(([k, v]) => {
      if (form[k] != null) form[k].value = v;
      if (form[k] && form[k].type === "checkbox") form[k].checked = !!v;
    });
  });
}

const importPack = document.getElementById("import-pack");
if (importPack) {
  importPack.addEventListener("change", async () => {
    if (!importPack.files || !importPack.files[0]) return;
    const fd = new FormData();
    fd.append("file", importPack.files[0]);
    const res = await fetch("/api/tutorials/import", { method: "POST", body: fd });
    const data = await res.json();
    if (!res.ok) {
      setError(data.detail || "导入失败");
      return;
    }
    refreshTutorials();
  });
}

if (form && form.repo_url) {
  const params = new URLSearchParams(location.search);
  if (params.get("repo")) {
    form.repo_url.value = params.get("repo");
  }
}
