# weread-daily-read

[![tests](https://github.com/shichen35/weread-daily-read/actions/workflows/tests.yml/badge.svg)](https://github.com/shichen35/weread-daily-read/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

用 GitHub Actions 每天定时为微信读书上报阅读时长。

- **独立设备登录**：扫码登录并生成新的设备指纹，不会挤掉手机或浏览器上的登录。
- **凭据自动续期**：每次续期后立即写回仓库 Secret，一次登录长期可用。
- **不改阅读进度**：始终上报服务端记录的当前位置。
- **随机时间和时长**：开始时间在窗口内随机，时长可写成范围或 `60±10`。
- **从书架选书**：终端里多选书籍，每天随机读其中一本。

> [!WARNING]
> 非官方项目，仅供学习交流，使用风险自负。

## 快速开始

需要 Python 3.10+ 和已登录的 [GitHub CLI](https://cli.github.com/)（`gh auth login`）。

**1. Fork 并克隆本仓库**，在 fork 的 **Actions** 页面启用 workflow（fork 默认不运行定时任务）。

```bash
git clone https://github.com/<你的用户名>/weread-daily-read.git
cd weread-daily-read
gh repo set-default <你的用户名>/weread-daily-read   # 让 gh 写到你的 fork，而不是上游
python3 -m venv .venv && .venv/bin/pip install -r requirements-local.txt
```

**2. 扫码登录**，凭据会写入 Secret `WEREAD_STATE`：

```bash
.venv/bin/python login.py --push
```

**3. 创建写回凭据用的 token。** 新建一个 [fine-grained PAT](https://github.com/settings/personal-access-tokens/new)：Repository access 只选这个仓库，Permissions 中 **Secrets** 设为 Read and write。然后：

```bash
gh secret set STATE_WRITER_PAT
```

**4. 从书架选书：**

```bash
.venv/bin/python books.py
```

它会触发 `shelf` workflow，用 `WEREAD_STATE` 在 Actions 里拉取书架（本地不用再登录），约一分钟后显示书架：↑↓ 移动，PgUp/PgDn 翻页，Home/End 跳到首尾，空格勾选，回车确认，再确认一次后写入 `WEREAD_BOOK`。

之后想换书，重新运行即可：已选的书排在最上面并默认勾上，只需增减。加 `--dry-run` 只打印结果不写入（下次也不会预选）。

**5. 试运行两分钟：**

```bash
gh workflow run daily-read -f minutes=2
```

## 配置

| 名称 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `WEREAD_STATE` | Secret | — | 登录凭据，由 `login.py --push` 写入，运行时自动更新 |
| `STATE_WRITER_PAT` | Secret | — | 有 Secrets 读写权限的 PAT，用于写回凭据 |
| `WEREAD_BOOK` | Variable | — | 逗号分隔的 bookId，由 `books.py` 写入，每次随机读一本。也可以手动填 bookId 或阅读页链接 |
| `READ_MINUTES` | Variable | `60` | 每天时长：`60`、范围 `45-75`，或 `60±10`（也可写 `60+-10`） |
| `READ_WINDOW` | Variable | `0` | 随机开始窗口（分钟，最大 340）。`180` 表示阅读落在 01:00–04:00 |
| `RUNNER` | Variable | `ubuntu-latest` | 例如 `self-hosted` |

定时任务在北京时间 01:00 触发（GitHub 可能延迟启动）。手动运行默认立即开始，可用 `-f window=10` 测试随机开始。日志第一行 `plan:` 会显示本次的等待时间、开始时间、时长和选书情况。

## 工作原理

1. 启动时续期 Cookie 并写回 Secret，之后每 20 分钟续期一次。
2. 打开阅读页，读取 `psvts`、`token` 和当前阅读位置，发送一次“进入阅读”上报。
3. 每 30 秒上报一次 `rt=30`；只有返回 `synckey` 的上报才计入时长。阅读页信息每 15 分钟刷新。
4. 失败时依次：同步章节信息（缺少 `synckey` 时）→ 刷新阅读页重试 → 续期后重试。连续 3 次失败则退出。

`daily-read` 和 `shelf` 用同一个 `concurrency` 组，保证同一时间只有一个任务写凭据。

## 退出码

| 退出码 | 含义 | 处理 |
|---|---|---|
| 0 | 完成 | — |
| 1 | 连续上报失败 | 查看日志中 `report rejected:` 后的错误码 |
| 2 | 登录失效 | 重新运行 `login.py --push` |
| 3 | 写回 Secret 失败 | 检查 `STATE_WRITER_PAT` 的权限和有效期 |

## 注意事项

- **不要在本地用同一份凭据运行 `report.py`**：本地续期会让 Secret 里的凭据失效。本地调试请另外登录一份：

  ```bash
  .venv/bin/python login.py --out state.json
  .venv/bin/python report.py --state-file state.json --book <bookId> --minutes 2
  ```

- GitHub 托管 runner 使用海外 IP，长期稳定性未经验证；国内的 self-hosted runner 更稳妥。
- 公开仓库 60 天没有提交时，GitHub 会停用定时任务，需要到 Actions 页面重新启用。

## 开发

```bash
.venv/bin/python -m unittest discover -s tests
```

欢迎提交 issue 和 PR。

## 许可证

[MIT](LICENSE)
