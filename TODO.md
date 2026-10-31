# 开源 checklist

目前仓库保持私有、自用。以后要开源时，按顺序执行下面的步骤。

## 1. 清理 git 历史

`main` 的历史已经重写干净，但旧历史还留在：

- 远程分支：`claude/keen-cannon-79dkt2`、`feat/random-read-schedule`、`feat/synckey-and-early-schedule`、`feat/dependabot`、`feat/open-source-polish`、`dependabot/*`
- 已合并的 PR #1–#3（GitHub 会一直保留 PR 里的 commit，无法删除）

所以要删掉仓库再重建，只推送干净的 `main`：

- [ ] 只克隆 `main` 到新目录，并去掉远程：
  ```bash
  git clone --single-branch -b main https://github.com/shichen35/weread-daily-read.git ~/Developer/weread-clean
  cd ~/Developer/weread-clean && git remote remove origin
  ```
- [ ] 检查历史里没有不想公开的内容（下面两条命令都应该没有输出）：
  ```bash
  git grep -niI "koplugin\|koreader\|agpl\|wr_skey=\|wr_rt=" $(git rev-list main)
  git log --format='%an %ae %B' | grep -i claude
  ```
- [ ] 删除旧仓库（会同时删除 Secrets、Variables、PR 和运行记录）：
  ```bash
  gh auth refresh -h github.com -s delete_repo
  gh repo delete shichen35/weread-daily-read --yes
  ```
- [ ] 用同名新建私有仓库并推送：
  ```bash
  gh repo create shichen35/weread-daily-read --private --source=. --remote=origin --push
  ```
- [ ] 删除本地旧目录，避免误推旧历史。

## 2. 确认许可证

- [ ] 确认自己有权用 MIT 发布（协议代码的来源是否允许）。不确定就先别公开。

## 3. 重新配置运行环境

- [ ] 扫码登录：`.venv/bin/python login.py --push --repo shichen35/weread-daily-read`
- [ ] 新建 fine-grained PAT（只选新仓库，Secrets: Read and write），然后 `gh secret set STATE_WRITER_PAT`
- [ ] 设置变量：`WEREAD_BOOK`、`READ_MINUTES`、`READ_WINDOW`（可选 `RUNNER`）
- [ ] 手动跑一次：`gh workflow run daily-read -f minutes=2`
- [ ] 如果 Claude GitHub App 只装在选定仓库上，把新仓库重新加进去

## 4. 仓库设置

- [ ] 填写 Description 和 topics（如 `weread`、`github-actions`）
- [ ] Settings → Code security：打开 Dependabot alerts、Dependabot security updates、Secret scanning、Private vulnerability reporting
- [ ] Settings → Branches：给 `main` 加保护规则（要求 PR 和 tests 通过）
- [ ] Settings → Actions → General：来自 fork 的 PR 需要批准后才运行 workflow

## 5. 公开后的维护

- [ ] 公开仓库 60 天没有提交，GitHub 会停用定时任务。加一个 keepalive，或者记得定期提交、到 Actions 页面重新启用
- [ ] 可选：issue 模板、`CONTRIBUTING.md`、`SECURITY.md`

## 6. 公开

- [ ] Settings → General → Danger Zone → Change visibility → Public
- [ ] 公开后再用无痕窗口看一遍仓库首页、README 和 Actions 页面
