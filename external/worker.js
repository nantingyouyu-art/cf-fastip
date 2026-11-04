/**
 * 外部触发：Cloudflare Worker Cron -> GitHub workflow_dispatch
 *
 * 用法：
 *   https://<worker>/debug     -> 自检：GH_TOKEN 是否配置、能否读仓库（不触发）
 *   https://<worker>/go        -> 触发一次 dispatch，返回 HTTP 状态（204 = 受理）
 *   https://<worker>/          -> 说明页，不触发（避免浏览器预取/健康检查把 dispatch 打爆）
 *
 *   冷却：同一 isolate 内 20 秒只允许一次 dispatch，防止误刷/重试风暴。
 *
 * 必需机密（Settings -> Variables and Secrets -> Secret）：
 *   GH_TOKEN = fine-grained PAT，仓库 cf-fastip，权限 Actions: Read and write
 */

const OWNER = "nantingyouyu-art";
const REPO = "cf-fastip";
const WORKFLOW = "cfip.yml";
const REF = "main";
const API = "https://api.github.com";

function headers(token) {
  return {
    Authorization: `Bearer ${token}`,
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "cfip-worker",
    "Content-Type": "application/json",
  };
}

async function dispatch(env) {
  const url = `${API}/repos/${OWNER}/${REPO}/actions/workflows/${WORKFLOW}/dispatches`;
  const res = await fetch(url, {
    method: "POST",
    headers: headers(env.GH_TOKEN),
    body: JSON.stringify({ ref: REF }),
  });
  return { status: res.status, text: await res.text() };
}

async function selfCheck(env) {
  const token = env.GH_TOKEN;
  const out = {
    token_present: Boolean(token),
    token_prefix: token ? String(token).slice(0, 12) + "..." : null,
    token_length: token ? String(token).length : 0,
  };
  if (token) {
    const r = await fetch(`${API}/repos/${OWNER}/${REPO}`, { headers: headers(token) });
    out.repo_read_status = r.status;
    const a = await fetch(
      `${API}/repos/${OWNER}/${REPO}/actions/workflows/${WORKFLOW}`,
      { headers: headers(token) },
    );
    out.workflow_read_status = a.status;
  }
  return out;
}

// 同一 isolate 内的简易冷却（跨 isolate 不共享，够挡住误刷）
let lastDispatchAt = 0;
const COOLDOWN_MS = 20 * 1000;

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(
      dispatch(env)
        .then((r) => console.log(`cron dispatch ${r.status} ${r.text}`))
        .catch((e) => console.error(`cron dispatch failed: ${e && e.stack || e}`)),
    );
  },

  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;
    try {
      if (path.startsWith("/debug")) {
        const info = await selfCheck(env);
        return new Response(JSON.stringify(info, null, 2) + "\n", {
          headers: { "content-type": "application/json; charset=utf-8" },
        });
      }
      const wantsTrigger = path.startsWith("/go") || url.searchParams.get("go") === "1";
      if (!wantsTrigger) {
        return new Response(
          "cfip-trigger\n\n" +
          "  /debug  自检（不触发）\n" +
          "  /go     立即触发一次 GitHub workflow_dispatch\n\n" +
          "定时器由 cron 触发器负责，页面访问不会触发。\n",
          { headers: { "content-type": "text/plain; charset=utf-8" } },
        );
      }
      const now = Date.now();
      if (now - lastDispatchAt < COOLDOWN_MS) {
        return new Response(
          `cooling down: ${Math.ceil((COOLDOWN_MS - (now - lastDispatchAt)) / 1000)}s\n`);
      }
      lastDispatchAt = now;
      const r = await dispatch(env);
      console.log(`manual dispatch ${r.status} ua=${request.headers.get("user-agent") || "-"}`);
      return new Response(`dispatch -> ${r.status} ${r.text}\n`);
    } catch (err) {
      return new Response(`EXCEPTION: ${(err && err.stack) || err}\n`, { status: 200 });
    }
  },
};