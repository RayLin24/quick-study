# Mermaid 版本钉扎

阅读页使用本地 vendor，**不**走 jsDelivr。

- 文件：`web/static/vendor/mermaid.min.js`
- 当前钉扎：**mermaid@11.4.1**（与文件头 `version` 字段一致）
- 升级时：替换该文件并同步改本页版本号，跑 `tests/test_p2_features.py` 的 vendor 断言
