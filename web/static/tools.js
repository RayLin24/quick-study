const params = new URLSearchParams(location.search);
const presetTutorial = params.get("t") || (location.hash || "").replace(/^#/, "");

function lines(text) {
  return String(text || "")
    .split(/\r?\n/)
    .map((s) => s.trim())
    .filter(Boolean)
    .map((name) => ({ name }));
}

async function refreshWorkbench() {
  const res = await fetch("/api/workbench");
  const data = await res.json();
  const list = document.getElementById("wb-list");
  if (!list) return;
  list.innerHTML = "";
  (data.repos || []).forEach((item) => {
    const li = document.createElement("li");
    const a = document.createElement("a");
    a.href = `/?repo=${encodeURIComponent(item.repo_url || "")}`;
    a.textContent = item.name || item.repo_url;
    li.appendChild(a);
    list.appendChild(li);
  });
}

async function refreshPresets() {
  const res = await fetch("/api/presets");
  const data = await res.json();
  const list = document.getElementById("preset-list");
  if (!list) return;
  list.innerHTML = "";
  (data.items || data.presets || []).forEach((item) => {
    const name = item.name || item;
    const li = document.createElement("li");
    li.textContent = typeof name === "string" ? name : JSON.stringify(item);
    list.appendChild(li);
  });
}

document.getElementById("wb-add")?.addEventListener("click", async () => {
  const current = await fetch("/api/workbench").then((r) => r.json());
  const repos = current.repos || [];
  repos.push({
    repo_url: document.getElementById("wb-url").value.trim(),
    name: document.getElementById("wb-name").value.trim(),
    include: document.getElementById("wb-include").value.trim(),
  });
  await fetch("/api/workbench", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ repos }),
  });
  refreshWorkbench();
});

document.getElementById("cmp-run")?.addEventListener("click", async () => {
  const res = await fetch("/api/abstractions/compare", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      left: lines(document.getElementById("cmp-left").value),
      right: lines(document.getElementById("cmp-right").value),
    }),
  });
  document.getElementById("cmp-out").textContent = JSON.stringify(await res.json(), null, 2);
});

document.getElementById("digest-run")?.addEventListener("click", async () => {
  let files = [];
  try {
    files = JSON.parse(document.getElementById("digest-in").value || "[]");
  } catch {
    document.getElementById("digest-out").textContent = "JSON 无效";
    return;
  }
  const res = await fetch("/api/digest", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ files }),
  });
  const data = await res.json();
  document.getElementById("digest-out").textContent = data.digest || JSON.stringify(data, null, 2);
});

document.getElementById("pr-run")?.addEventListener("click", async () => {
  const res = await fetch("/api/guides/pr", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      title: document.getElementById("pr-title").value,
      diff: document.getElementById("pr-diff").value,
    }),
  });
  const data = await res.json();
  document.getElementById("pr-out").textContent = data.prompt || JSON.stringify(data, null, 2);
});

const tutorialSel = document.getElementById("tool-tutorial");
if (tutorialSel && presetTutorial) {
  const match = [...tutorialSel.options].find((o) => o.value === presetTutorial);
  if (match) tutorialSel.value = presetTutorial;
}

function selectedTutorial() {
  return (document.getElementById("tool-tutorial") || {}).value || "";
}

function syncPagesZip() {
  const name = selectedTutorial();
  const link = document.getElementById("pages-zip");
  if (link) link.href = name ? `/api/tutorials/${encodeURIComponent(name)}/pages.zip` : "#";
}

tutorialSel?.addEventListener("change", syncPagesZip);
syncPagesZip();

document.querySelectorAll("[data-tool]").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const name = selectedTutorial();
    const out = document.getElementById("quality-out");
    if (!name) {
      out.textContent = "请先选择教程";
      return;
    }
    const kind = btn.getAttribute("data-tool");
    if (kind === "pages") {
      const res = await fetch(`/api/tutorials/${encodeURIComponent(name)}/pages`, { method: "POST" });
      out.textContent = JSON.stringify(await res.json(), null, 2);
      return;
    }
    const path = {
      quality: `/api/tutorials/${encodeURIComponent(name)}/quality`,
      heatmap: `/api/tutorials/${encodeURIComponent(name)}/heatmap`,
      glossary: `/api/tutorials/${encodeURIComponent(name)}/glossary`,
      week: `/api/tutorials/${encodeURIComponent(name)}/week-path`,
    }[kind];
    const res = await fetch(path);
    const text = await res.text();
    out.textContent = text;
  });
});

refreshWorkbench();
refreshPresets();
