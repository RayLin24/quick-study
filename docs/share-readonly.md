# 只读分享

把已生成的 `output/<教程>/` 发给同事即可离线阅读 Markdown。

需要本机 Web 只读访问时：

1. 设置 `QUICK_STUDY_READ_TOKEN`（只读：列表 / 阅读 / Ask）
2. 设置 `QUICK_STUDY_TOKEN`（可生成）
3. 非回环绑定必须带写令牌，否则拒绝启动
4. 只读令牌不能 `POST /api/jobs`

单文件离线页：`GET /api/tutorials/<name>/offline.html`
