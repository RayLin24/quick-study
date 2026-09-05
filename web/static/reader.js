(function () {
  const KEY = "qs-progress";
  const THEME = "qs-theme";
  const path = location.pathname;

  function loadProgress() {
    try {
      return JSON.parse(localStorage.getItem(KEY) || "{}");
    } catch {
      return {};
    }
  }

  function markProgress() {
    const data = loadProgress();
    data[path] = Date.now();
    localStorage.setItem(KEY, JSON.stringify(data));
    document.body.dataset.progress = "1";
  }

  markProgress();

  const themeBtn = document.getElementById("theme-toggle");
  if (localStorage.getItem(THEME) === "dark") {
    document.body.classList.add("theme-dark");
  }
  if (themeBtn) {
    themeBtn.addEventListener("click", () => {
      document.body.classList.toggle("theme-dark");
      localStorage.setItem(THEME, document.body.classList.contains("theme-dark") ? "dark" : "light");
    });
  }

  const drawerBtn = document.getElementById("toc-drawer-btn");
  const drawer = document.getElementById("toc-drawer");
  if (drawerBtn && drawer) {
    drawerBtn.addEventListener("click", () => drawer.classList.toggle("is-open"));
  }

  document.addEventListener("keydown", (ev) => {
    if (ev.target && (ev.target.tagName === "INPUT" || ev.target.tagName === "TEXTAREA")) return;
    if (ev.key === "j" || ev.key === "ArrowRight") {
      const next = document.querySelector(".pager-link-next");
      if (next) location.href = next.getAttribute("href");
    }
    if (ev.key === "k" || ev.key === "ArrowLeft") {
      const prev = document.querySelector(".pager-link:not(.pager-link-next)");
      if (prev) location.href = prev.getAttribute("href");
    }
    if (ev.key === "d") themeBtn && themeBtn.click();
    if (ev.key === "t") drawerBtn && drawerBtn.click();
  });

  const ttsBtn = document.getElementById("tts-btn");
  if (ttsBtn && window.speechSynthesis) {
    ttsBtn.addEventListener("click", () => {
      const text = (document.querySelector("article.prose") || document.body).innerText.slice(0, 4000);
      const utter = new SpeechSynthesisUtterance(text);
      window.speechSynthesis.cancel();
      window.speechSynthesis.speak(utter);
    });
  }

  const name = (path.split("/t/")[1] || "").split("/")[0];
  const starBtn = document.getElementById("star-btn");
  if (starBtn && name) {
    starBtn.addEventListener("click", async () => {
      await fetch(`/api/tutorials/${encodeURIComponent(name)}/star`, { method: "POST" });
      starBtn.textContent = "已收藏";
    });
  }

  const mermaidBtn = document.getElementById("mermaid-export-btn");
  if (mermaidBtn && name) {
    mermaidBtn.addEventListener("click", async () => {
      await fetch(`/api/tutorials/${encodeURIComponent(name)}/mermaid/export`, { method: "POST" });
      mermaidBtn.textContent = "已导出 SVG";
    });
  }

  const saveBtn = document.getElementById("annotation-save");
  const noteEl = document.getElementById("annotation-note");
  const listEl = document.getElementById("annotation-list");
  async function refreshNotes() {
    if (!name || !listEl) return;
    const res = await fetch(`/api/tutorials/${encodeURIComponent(name)}/annotations`);
    const data = await res.json();
    listEl.innerHTML = "";
    (data.items || []).forEach((item) => {
      const li = document.createElement("li");
      li.textContent = `${item.filename}: ${item.note}`;
      listEl.appendChild(li);
    });
  }
  if (saveBtn && noteEl && name) {
    saveBtn.addEventListener("click", async () => {
      const quote = (window.getSelection && String(window.getSelection())) || "";
      await fetch(`/api/tutorials/${encodeURIComponent(name)}/annotations`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename: path.split("/").pop() || "index.md", quote, note: noteEl.value }),
      });
      noteEl.value = "";
      refreshNotes();
    });
    refreshNotes();
  }

  const quizBox = document.getElementById("quiz-box");
  const quizList = document.getElementById("quiz-list");
  if (quizBox && quizList && name) {
    fetch(`/api/tutorials/${encodeURIComponent(name)}/quiz`)
      .then((r) => r.json())
      .then((data) => {
        const file = decodeURIComponent(path.split("/").pop() || "");
        const item = (data.items || []).find((q) => q.filename === file);
        if (!item) return;
        quizBox.hidden = false;
        item.questions.forEach((q) => {
          const li = document.createElement("li");
          li.textContent = q.prompt;
          quizList.appendChild(li);
        });
      })
      .catch(() => {});
  }

  document.querySelectorAll("code, .copy-path").forEach((node) => {
    const text = node.getAttribute("data-path") || node.textContent || "";
    if (text.includes("#") && text.includes("/")) {
      node.classList.add("source-symbol");
    }
  });
})();
