# 上游维护

`Upstream Maintenance` 合入 `master` 后按下列规则运行；测试分支不会运行定时维护。它只允许在 `Nyox-x/running_page_kith` 运行，并固定从 `yihong0618/running_page` 的 `master` 做标准三方合并。

- 每天北京时间 06:17 检查上游，也支持手动运行。`Run Data Sync` 的 `master` 首次失败且没有上游更新时，只重跑失败任务一次；第二次失败由 GitHub Actions 原生通知，不再循环。
- 合并冲突会列出冲突路径并中止；不会 `force`、`reset`、自动选 `ours/theirs` 或自动解冲突。合并前后比较当前同步 workflow 的全部 15 个顶层环境变量、`theme_preset`、classic 隐私常量和两句个人文案，任一变化都会中止。
- 只有发现上游新提交才安装依赖并执行 `pnpm check`、只读 ESLint、`pnpm build`、现有 CI 的 `python run_page/gpx_sync.py` smoke、Black check 和 Ruff check。现有 `pnpm lint` 固定带 `--fix`，因此候选 workflow 直接运行等价的 `pnpm exec eslint src --ext .ts,.tsx`。发布仅提交合并已暂存的内容，不执行 `git add .`，测试生成的数据不会进入合并提交。
- 检查全部通过后才创建带 `[skip ci]` 的 merge commit，并普通 push `master`；push 被远端新提交拒绝时不会 dispatch。成功 push 后显式 dispatch `Run Data Sync`，不依赖 token push 的触发行为。
- 普通源码更新可使用 `GITHUB_TOKEN`。上游若修改 `.github/workflows/**`，必须配置可选仓库 secret `UPSTREAM_SYNC_TOKEN`：fine-grained token 仅限本仓库，授予 Contents、Workflows、Actions write。缺少时会在合并提交前明确失败。
- 合并前每月更新一次 `.github/last-maintenance-month`，提交信息含 `[skip ci]`，用于避免公开 fork 因 60 天无活动停用定时任务；后续合并冲突或缺少 workflow token 不会阻断当月保活。只在 API 状态恰为 `disabled_inactivity` 时启用 `Run Data Sync`，不会反转人工禁用。若本维护 workflow 已先被 GitHub 停用，仍需人工启用一次；已停用的 workflow 无法自我运行修复。
- 构建只验证仓库示例源码，不下载、不发布跑步数据，不输出 secret，也不部署页面。

离线验证：`python -m unittest discover -s .github/scripts -p test_upstream_maintenance.py -v`。
