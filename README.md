# weread-daily-read

每天用 GitHub Actions 定时打开微信读书 Web 阅读页，每 30 秒上报一次阅读时长，持续一段时间（默认 60 分钟）。上报时长和开始时间都可以随机化，避免每天在同一时刻上报同样的时长。协议实现移植自 [weread.koplugin](https://github.com/shichen35/weread.koplugin)，测试用例的期望值由插件的 Lua 实现生成。

## 工作方式

- **登录**：在本地运行 `login.py` 扫码登录。脚本会生成一个新的 `wr_fp` 设备指纹，所以这个 runner 在服务端看来是一台独立设备，不会把 KOReader 或浏览器里的登录踢下线。登录成功后，脚本会先续期一次确认凭据可用，再把 Cookie 写入仓库 Secret `WEREAD_STATE`。
- **保持登录**：`report.py` 每次续期成功（启动时一次，之后每 20 分钟一次）后，都会立即通过 `gh secret set` 把新 Cookie 写回 `WEREAD_STATE`。这样即使任务中途被杀掉，Secret 里也是最新的凭据。workflow 用 `concurrency` 保证同一时间只有一个任务在写这个 Secret。
- **随机时间段**：定时任务在北京时间 21:00 触发。设置 `READ_WINDOW`（分钟）后，脚本会先随机等待一段时间，让整段阅读落在触发后的 `READ_WINDOW` 分钟内；`READ_MINUTES` 写成范围（如 `45-75`）或带随机误差（如 `60±10`，也可写 `60+-10`、`60+/-10`，等同于 `50-70`）时，每天的时长也会随机。窗口最大 340 分钟（GitHub 任务上限 360 分钟），时长超过窗口时按窗口截断。日志开头的 `plan:` 一行会显示本次的等待时间、开始时间和时长。
- **上报**：
  1. 打开阅读页，拿到 `psvts`/`token` 和服务端记录的当前阅读位置，然后发送一次“进入阅读”的上报。
  2. 之后每 30 秒发送一次 `rt=30` 的上报，位置始终用服务端记录的当前位置，所以不会改动你的阅读进度。
  3. 阅读页上下文每 15 分钟刷新一次。
  4. 只有返回里带 `synckey` 的上报才算计入时长。返回 `succ` 但没有 `synckey` 时，先请求一次 `chapterInfos` 同步章节信息，再按失败处理。
  5. 上报被拒绝时，先刷新上下文后重试；还失败且距上次续期超过 10 分钟，就续期后再重试。
  6. 连续 3 次失败就停止，任务标记为失败。

## 一次性设置

```bash
cd ~/Developer/weread-daily-read
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python login.py --push          # 终端里会显示二维码，用微信读书 App 扫码确认
```

创建一个 fine-grained PAT，用于在任务中写回 Secret：

- Repository access 只选择本仓库。
- Permissions 里把 **Secrets** 设为 Read and write。

```bash
gh secret set STATE_WRITER_PAT            # 粘贴上面创建的 PAT
gh variable set WEREAD_BOOK --body "<bookId 或 https://weread.qq.com/web/reader/... 链接>"
gh variable set READ_MINUTES --body 60    # 可选，默认 60；也可写 45-75 或 60±10，每天随机
gh variable set READ_WINDOW --body 180    # 可选，默认 0（立即开始）；180 表示阅读随机落在 21:00–24:00
gh variable set RUNNER --body self-hosted # 可选，使用自托管 runner
```

先手动跑 2 分钟验证（手动运行默认立即开始，不使用 `READ_WINDOW`，可用 `-f window=10` 测试随机开始）：

```bash
gh workflow run daily-read -f minutes=2
```

## 退出码

| 退出码 | 含义 | 处理方式 |
|---|---|---|
| 0 | 完成 | — |
| 1 | 连续上报失败 | 查看日志中 `report rejected:` 后面的错误码 |
| 2 | 凭据失效 | 重新运行 `login.py --push` |
| 3 | 写回 Secret 失败 | 检查 `STATE_WRITER_PAT` 的权限和有效期 |

## 注意

- 不要在本地用同一份凭据运行 `report.py`。本地续期会更换 `wr_skey`，Secret 里的旧值可能因此失效。本地调试请先用 `login.py --out state.json` 另外登录一份凭据，再运行 `report.py --state-file state.json`。
- GitHub 托管 runner 使用海外 IP，在这个环境下续期能否长期稳定还没有验证过；用国内的 self-hosted runner 更稳妥。
- 定时上报的时长属于自动产生的数据。用它换取阅读奖励可能违反微信读书规则，账号风险自负。
- GitHub 不允许把 Actions 用于与仓库软件无关的用途，请自行评估。
- 运行测试：`.venv/bin/python -m unittest discover -s tests`
