# 外部触发（让 30 分钟真正准点）

## 为什么

GitHub 自带的 `on.schedule` 不是闹钟，是队列：到点只是把任务扔进待执行的池子，
被空闲 runner 取走才开跑。高峰期实测延迟 5～30 分钟，偶发整跳。

当前仓库已经用了「每 5 分钟一跳 + 采集器自身去重」的笨办法兜底（每 30 分钟窗口有 6 次机会），
不配外部触发也能用。若要求准点，二选一。

---

---

## 方案 A0：Cloudflare 面板部署（不用命令行，5 分钟）

1. <https://dash.cloudflare.com> → 左侧 **Workers & Pages** → **Create** → **Worker**
2. 名字填 `cfip-trigger` → **Deploy**（默认的 hello-world 代码先留着，下一步替换）
3. 点 **Edit code**，把 `external/worker.js` 全文粘进去覆盖 → 右上 **Deploy**
4. **Settings** → **Variables and Secrets** → **Add** → 类型选 **Secret**
   Name = `GH_TOKEN`，Value = 你刚建的那个 `github_pat_...`
   ⚠️ 一定选 Secret，别选 Text（Text 在面板里是明文可回显）
5. **Settings** → **Trigger Events**（或 Triggers 标签）→ **Cron Triggers** → **Add Cron Trigger**
   填 `*/30 * * * *`（UTC，正好每半小时）
6. 验证：浏览器直接访问 `https://cfip-trigger.<你的子域>.workers.dev/`
   页面显示 `dispatch -> 204` 就算通了；再看 GitHub Actions 多出一条 `workflow_dispatch` 运行

## 方案 A：Cloudflare Worker Cron（命令行 wrangler）

token 存在自己的 CF 账号里，不经过任何第三方站点。需要 PAT（10 天后自动过期）。

1. 建 fine-grained PAT
   <https://github.com/settings/tokens?type=beta>
   - Resource owner: `nantingyouyu-art`
   - Repository access: **Only select repositories** → `cf-fastip`
   - Permissions: **Actions → Read and write**，其余保持 No access
   - Expiration: 10 天

2. 部署
   ```bash
   cd external
   npx wrangler login      # 浏览器授权你的 CF 账号
   npx wrangler deploy
   npx wrangler secret put GH_TOKEN   # 粘贴 PAT
   ```

3. 验证
   ```bash
   curl https://cfip-trigger.<你的子域>.workers.dev/   # 返回 dispatch -> 204
   npx wrangler tail                                   # 看 cron 每次的日志
   ```
   然后去 Actions 页面确认出现一条 `workflow_dispatch` 运行。

---

## 方案 B：cron-job.org（最省事，token 交给第三方站点）

1. 注册 <https://cron-job.org>（邮箱验证）
2. Create cronjob：
   - Title: `cfip dispatch`
   - URL:
     ```
     https://api.github.com/repos/nantingyouyu-art/cf-fastip/actions/workflows/cfip.yml/dispatches
     ```
   - Schedule: Every **30** minutes
   - Method: **POST**
   - Headers（逐条加）:

     | Header | Value |
     |---|---|
     | `Authorization` | `Bearer github_pat_xxx` |
     | `Accept` | `application/vnd.github+json` |
     | `Content-Type` | `application/json` |
     | `X-GitHub-Api-Version` | `2022-11-28` |
     | `User-Agent` | `cfip-cron` |

   - Request body: `{"ref":"main"}`
3. 保存后点 “TEST RUN”，看到 **204 No Content** 就是成功（GitHub 受理即 204，无正文）。

PAT 用方案 A 里同样的 fine-grained token，权限只给这一个仓库的 Actions: write。

---

## 通用注意

- GitHub 收到 `dispatch` 后通常 10 秒内起跑，这一步不受 cron 排队影响。
- `204` 代表「已受理」，不代表运行成功；要看
  <https://github.com/nantingyouyu-art/cf-fastip/actions>
- 触发太频繁没意义：采集器按天去重，同一 IP 只会有一行。
- 测试结束把 Worker 删掉（`npx wrangler delete`）或停用 cron-job 任务，并让 PAT 自然过期。