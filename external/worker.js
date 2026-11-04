/**
 * 外部触发：Cloudflare Worker Cron -> GitHub workflow_dispatch
 *
 * 为什么需要它：GitHub 自带的 schedule 是「排队尽力而为」，延迟 5~30 分钟很常见，
 * 偶尔整跳。Worker 的 cron 触发由 Cloudflare 调度，通常秒级准点。
 *
 * 部署（3 步，全在你的 CF 账号内，token 不落到第三方）：
 *   1. 建 fine-grained PAT：GitHub -> Settings -> Developer settings ->
 *      Personal access tokens -> Fine-grained tokens
 *      - Repository access: Only select repositories -> cf-fastip
 *      - Permissions: Actions = Read and write（别的全 None）
 *      - Expiration: 10 天后自动过期（符合测试周期）
 *   2. cd external && npx wrangler deploy
 *      npx wrangler secret put GH_TOKEN      # 粘贴上面生成的 PAT
 *   3. 看 Worker 日志：npx wrangler tail
 *
 * 仓库/分支/工作流要改就改下面常量。
 */

const OWNER = "nantingyouyu-art";
const REPO = "cf-fastip";
const WORKFLOW = "cfip.yml";
const REF = "main";

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(trigger(env));
  },
  // 手动验证：浏览器访问 https://<worker>.workers.dev/
  async fetch(request, env) {
    const r = await trigger(env);
    return new Response(`dispatch -> ${r.status} ${r.body}\n`, { status: 200 });
  },
};

async function trigger(env) {
  const url = `https://api.github.com/repos/${OWNER}/${REPO}/actions/workflows/${WORKFLOW}/dispatches`;
  const res = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GH_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "cfip-worker",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ref: REF }),
  });
  const body = await res.text();
  // 204 = 已受理
  console.log(`dispatch ${res.status} ${body}`);
  return { status: res.status, body };
}