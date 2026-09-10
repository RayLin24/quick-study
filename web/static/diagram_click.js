/* Robust mermaid node → chapter (#32). data-id / click row, not fragile textContent.
   Also honors #3 diagram-nodes blob URLs when present. */
(function (global) {
  function extractNodeId() {
    for (let i = 0; i < arguments.length; i += 1) {
      const text = String(arguments[i] || "");
      const isolated = text.match(/(?:^|[\s#_.-])(A\d+)(?:$|[\s#_.-])/);
      if (isolated) return isolated[1];
      if (/^A\d+$/.test(text.trim())) return text.trim();
    }
    return null;
  }

  function parseJsonScript(id, root) {
    const el = root.getElementById ? root.getElementById(id) : null;
    if (!el) return [];
    try { return JSON.parse(el.textContent || "[]"); } catch { return []; }
  }

  function resolveDiagramHref(node, chapters, diagramNodes) {
    const dataId = node.getAttribute("data-id") || node.getAttribute("data-node-id") || "";
    const elementId = node.id || "";
    const clickFile = node.getAttribute("data-filename") || node.getAttribute("data-click") || "";
    const nodeId = extractNodeId(dataId, elementId);
    const nodes = diagramNodes || [];
    if (nodeId) {
      const dn = nodes.find((item) => item.id === nodeId);
      if (dn && (dn.blob || dn.href || dn.chapter)) return dn.blob || dn.href || dn.chapter;
      const hit = chapters.find((ch) => ch.node_id === nodeId);
      if (hit && hit.href) return hit.href;
    }
    if (clickFile) {
      const name = clickFile.split("/").pop();
      const byFile = chapters.find((ch) => ch.filename === name);
      if (byFile && byFile.href) return byFile.href;
    }
    const label = (node.getAttribute("data-title") || "").trim();
    if (label) {
      const dn = nodes.find((item) => String(item.title || "").trim() === label);
      if (dn && (dn.blob || dn.href || dn.chapter)) return dn.blob || dn.href || dn.chapter;
      const exact = chapters.find((ch) => String(ch.title || "").trim() === label);
      if (exact && exact.href) return exact.href;
    }
    return null;
  }

  function bindMermaidChapterClicks(root) {
    const scope = root || document;
    const raw = scope.getElementById ? scope.getElementById("chapter-nav") : null;
    if (!raw) return;
    let chapters = [];
    try { chapters = JSON.parse(raw.textContent || "[]"); } catch { return; }
    const diagramNodes = parseJsonScript("diagram-nodes", scope);
    const byId = {};
    (diagramNodes || []).forEach((item) => {
      if (item.id) {
        byId[item.id] = {
          href: item.blob || item.href || item.chapter || "",
          blob: item.blob || null,
          chapter: item.chapter || item.href || "",
          files: item.files || [],
        };
      }
    });
    scope.querySelectorAll(".mermaid .node").forEach((node) => {
      const nodeId = extractNodeId(node.getAttribute("data-id") || "", node.id || "");
      const rec = (nodeId && byId[nodeId]) || null;
      const href = resolveDiagramHref(node, chapters, diagramNodes);
      const blob = rec && rec.blob;
      const chapter = (rec && (rec.chapter || rec.href)) || href;
      const target = blob || chapter || href;
      if (!target) return;
      node.style.cursor = "pointer";
      node.setAttribute("tabindex", "0");
      if (rec && rec.files && rec.files.length) node.setAttribute("data-files", rec.files.join(","));
      if (blob) node.setAttribute("data-blob-href", blob);
      if (chapter) node.setAttribute("data-chapter-href", chapter);
      const go = () => {
        if (blob && /^https?:\/\//.test(blob)) {
          window.open(blob, "_blank", "noopener,noreferrer");
          return;
        }
        if (chapter) window.location.href = chapter;
      };
      node.addEventListener("click", (ev) => { ev.preventDefault(); ev.stopPropagation(); go(); });
      node.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); go(); }
      });
    });
  }

  global.qsDiagramClick = { extractNodeId, resolveDiagramHref, bindMermaidChapterClicks };
})(typeof window !== "undefined" ? window : globalThis);
