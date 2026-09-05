# GitLab CI 组件

文件：`.gitlab-ci/quick-study.yml`（GitLab CI component 同构）。

```yaml
include:
  - local: .gitlab-ci/quick-study.yml
    inputs:
      repo: https://gitlab.com/group/proj
      language: Chinese
```

或作为 CI/CD Catalog 组件引用。变量 `OPENROUTER_API_KEY` 放在 GitLab CI Variables，不要写进仓库。
