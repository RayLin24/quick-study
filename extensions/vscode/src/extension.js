/* Thin Quick Study sidebar: tutorials + Ask. MVP scaffold (#46). */
const vscode = require("vscode");

function activate(context) {
  const provider = {
    resolveWebviewView(webviewView) {
      webviewView.webview.options = { enableScripts: true };
      webviewView.webview.html = getHtml();
      webviewView.webview.onDidReceiveMessage(async (msg) => {
        const cfg = vscode.workspace.getConfiguration("quickStudy");
        const base = String(cfg.get("baseUrl") || "http://127.0.0.1:8000").replace(/\/$/, "");
        const token = String(cfg.get("token") || "");
        const headers = { "Content-Type": "application/json" };
        if (token) headers.Authorization = `Bearer ${token}`;
        try {
          if (msg.type === "list") {
            const res = await fetch(`${base}/api/tutorials`, { headers });
            const data = await res.json();
            webviewView.webview.postMessage({ type: "list", data });
          }
          if (msg.type === "ask") {
            const res = await fetch(
              `${base}/api/tutorials/${encodeURIComponent(msg.tutorial)}/ask`,
              { method: "POST", headers, body: JSON.stringify({ question: msg.question || "" }) }
            );
            const data = await res.json();
            webviewView.webview.postMessage({ type: "ask", data, ok: res.ok });
          }
        } catch (err) {
          webviewView.webview.postMessage({ type: "error", detail: String(err) });
        }
      });
    },
  };
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider("quickStudy.tutorials", provider)
  );
}

function getHtml() {
  return `<!DOCTYPE html>
<html><body>
  <p><button id="reload">刷新教程</button></p>
  <ul id="list"></ul>
  <p><label>教程 <input id="name" /></label></p>
  <p><textarea id="q" rows="3" placeholder="问这个仓"></textarea></p>
  <p><button id="ask">Ask</button></p>
  <pre id="out"></pre>
  <script>
    const vscode = acquireVsCodeApi();
    document.getElementById('reload').onclick = () => vscode.postMessage({type:'list'});
    document.getElementById('ask').onclick = () => vscode.postMessage({
      type:'ask',
      tutorial: document.getElementById('name').value,
      question: document.getElementById('q').value
    });
    window.addEventListener('message', (ev) => {
      const msg = ev.data || {};
      if (msg.type === 'list') {
        const items = (msg.data && msg.data.items) || [];
        document.getElementById('list').innerHTML = items.map(i =>
          '<li><button data-n="'+(i.name||i)+'">'+(i.name||i)+'</button></li>').join('');
        document.querySelectorAll('#list button').forEach(b => b.onclick = () => {
          document.getElementById('name').value = b.getAttribute('data-n');
        });
      } else {
        document.getElementById('out').textContent = JSON.stringify(msg.data || msg, null, 2);
      }
    });
    vscode.postMessage({type:'list'});
  </script>
</body></html>`;
}

function deactivate() {}

module.exports = { activate, deactivate };
