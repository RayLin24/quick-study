# GitHub 扩展 / 书签（feature 41）

在 GitHub 仓库页一键填本机 Quick Study 表单。不发布商店；两种用法：

## 书签

```
javascript:void((function(){const m=location.href.match(/github\.com\/([^/]+)\/([^/?#]+)/);if(!m){alert('请在 GitHub 仓库页使用');return;}const url='https://github.com/'+m[1]+'/'+m[2];open('http://127.0.0.1:8000/?repo='+encodeURIComponent(url),'_blank');})())
```

## 最小 Chrome 扩展

目录 `extensions/github/`（MV3，只读当前 tab URL，打开 `127.0.0.1:8000/?repo=`）。

1. Chrome → 扩展 → 开发者模式 → 加载已解压的扩展 → 选 `extensions/github`
2. 在 github.com/owner/repo 点图标
