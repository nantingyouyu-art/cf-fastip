# Cloudflare 电信优选 IP 采集器

从 `https://api.uouin.com/cloudflare.html` 抓取电信线路最优 Cloudflare 节点 IP，每 30 分钟一条，
当天去重，按 24 小时分文件存档，隔天邮件投递，3 天后自动删除。

## 目录

```
cloudflare-fastip/
├── cfip.py            # 主程序（采集/发送/清理/状态）
├── config.json        # 非敏感配置（线路、间隔、收件人、保留天数）
├── secrets.json       # SMTP 应用专用密码（不进版本库）
├── run.bat            # 计划任务入口
├── install_task.ps1   # 注册/更新 Windows 计划任务
├── state.json         # 运行状态（已发送日期、上次 IP）
├── data/              # 按日期分文件：YYYY-MM-DD.txt
└── logs/              # cfip.log（滚动）、task.log（计划任务输出）
```

## 数据源说明

页面表格由前端 JS 请求 `index.php/index/Cloudflare?key=&time=` 得到，`key` 每次请求都变：

```
key = md5( md5("DdlTxtN0sUOu") + "70cloudflareapikey" + time_ms )
```

`time` 与 `key` 用同一个毫秒时间戳，无需登录、无需 cookie。返回 JSON 中 `data.ctcc.info`
已按延迟升序排列，`info[0]` 就是电信最快 IP。

> 该接口的服务端快照每 10 分钟刷新一次；30 分钟采一次意味着同一 IP 常连续命中，
> 去重逻辑会自动跳过，不会刷出重复行。

## 常用命令

```bash
python cfip.py status              # 查看配置、上次 IP、各存档行数
python cfip.py collect             # 立刻抓一条并追加（去重）
python cfip.py run                 # 采集 + 发送到期存档 + 清理过期（计划任务跑这个）
python cfip.py --dry-run run       # 只演练，不落盘不发信
python cfip.py send 2026-11-04     # 手动补发某天
python cfip.py send all            # 补发所有待发送日期
python cfip.py purge               # 只清理过期存档
python cfip.py test-mail           # 发一封 SMTP 连通性测试邮件
```

## 配置

`config.json`

| 键 | 说明 |
|---|---|
| `line` | 线路：`ctcc`(电信) / `cmcc`(移动) / `cucc`(联通) / `bgp`(三网) / `ipv6` |
| `interval_minutes` | 采集间隔，仅用于文件头标注，实际节奏由计划任务控制 |
| `retention_days` | 存档保留天数，默认 3 |
| `proxy` | 留空直连；需要时填 `http://127.0.0.1:7890` |
| `mail.to` | 收件人列表 |
| `mail.subject` | 邮件标题，`{date}` 会被替换成日期 |

密码优先级：`config.json → mail.password` → 环境变量 `CFIP_SMTP_PASSWORD` → `secrets.json`。

Gmail 用**应用专用密码**（16 位，需先开两步验证）：
<https://myaccount.google.com/apppasswords>，写入 `secrets.json`：

```json
{ "smtp_password": "xxxxxxxxxxxxxxxx" }
```

## 定时任务

```powershell
# 注册（普通用户权限即可，每 30 分钟，从下一分钟开始）
powershell -ExecutionPolicy Bypass -File install_task.ps1

schtasks /Run    /TN CFIP-BestIP    # 立刻跑一次
schtasks /Query  /TN CFIP-BestIP /V /FO LIST
schtasks /Delete /TN CFIP-BestIP /F # 卸载
```

任务用「只使用交互方式」登录类型，登录后即生效；注销/睡眠期间不运行，
唤醒后下一次整 30 分会补跑（缺的时段不会补历史数据）。

## 云端部署（推荐，电脑关机也照跑）

仓库: <https://github.com/nantingyouyu-art/cf-fastip>（私有）

`.github/workflows/cfip.yml` 每 30 分钟由 GitHub Actions 执行，流程：

```
checkout -> 装 requests -> python cfip.py run -> 展示 status -> 提交 data/ 与 state.json
```

- 密码放仓库 Secrets: `CFIP_SMTP_PASSWORD`，不写进代码
- `TZ=Asia/Shanghai`，所以「按天归档 / 零点结算」都按北京时间
- 数据有变化才提交，提交带自动 rebase 重试（并发安全）
- 准点触发由 Cloudflare Worker 负责：`https://cfip.quach.cc.cd`（cron `*/30`）→ 调 GitHub dispatch API
  （本账号 workers.dev 子域有账号级 1101 故障，因此走自有域名 `cfip.quach.cc.cd`）
- 手动触发: `gh workflow run cfip.yml --repo nantingyouyu-art/cf-fastip -f task=test-mail`
  （`task` 可选 `run` / `test-mail` / `status`）

限制与须知：

| 项 | 说明 |
|---|---|
| 定时精度 | GitHub cron 是尽力而为，高峰常延迟 5–20 分钟，偶发跳过 |
| 计费 | 私有仓库每月 2000 分钟；每次运行约 1 分钟 → 约 1440 分钟/月，够用但别再加私有任务 |
| 免费方案 | 仓库改公开后 Actions 不限分钟（内容只有 IP 列表，无密钥） |
| 保活 | 每次提交都算仓库活动，不会被「60 天不活跃」停掉 schedule |
| 本地副本 | 本地已移除原有的 `CFIP-BestIP` 计划任务，避免和云端重复；需要恢复就跑 `install_task.ps1` |

## 本地部署（可选）

`install_task.ps1` 注册 `CFIP-BestIP`，每 30 分钟本地跑一次；关机/注销期间不运行。

## 输出格式

`data/2026-11-04.txt`

```
# Cloudflare 优选IP · 电信 · 2026-11-04
# 采集间隔: 30 分钟 | 数据源: https://api.uouin.com/index.php/index/Cloudflare
# 序号  采集时间              IP              延迟      速度        带宽
1     2026-11-04 19:34:04  172.64.229.233  52.18ms   60.07mb/s  480.56mb
2     2026-11-04 19:39:46  172.64.229.201  52.04ms   61.90mb/s  495.2mb
```

## 投递与清理节奏

- 每次 `run` 都检查 `data/` 里所有早于今天的文件，未发送的立即发信并记入 `state.json`
- 超过 `retention_days`（默认 3 天）的文件在发送成功后删除；未发送成功的会保留并告警，避免丢数据
- 机器停机跨越零点也能正确补发，不会漏掉某一天