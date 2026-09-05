# GitHub 扩展入口

浏览器书签（在 GitHub 仓库页点击后打开本机 Quick Study）：

```
javascript:void((function(){const m=location.href.match(/github\.com\/([^/]+)\/([^/?#]+)/);if(!m){alert('请在 GitHub 仓库页使用');return;}const url='https://github.com/'+m[1]+'/'+m[2];open('http://127.0.0.1:8000/?repo='+encodeURIComponent(url),'_blank');})())
```

本仓库不发布 Chrome 商店扩展；书签即入口。
