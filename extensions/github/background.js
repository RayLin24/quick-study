chrome.action.onClicked.addListener((tab) => {
  const href = tab && tab.url ? String(tab.url) : "";
  const m = href.match(/github\.com\/([^/]+)\/([^/?#]+)/);
  if (!m) {
    return;
  }
  const url = "https://github.com/" + m[1] + "/" + m[2];
  chrome.tabs.create({ url: "http://127.0.0.1:8000/?repo=" + encodeURIComponent(url) });
});
