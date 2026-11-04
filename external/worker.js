/**
 * 外部触发：Cloudflare Worker Cron -> GitHub workflow_dispatch
 *
 * 用法：
 *   https://<worker>/          -> 触发一次 dispatch，返回 HTTP 状态（204 = 受理）
 *   https://<worker>/debug     -> 只做自检，不触发：看 GH_TOKEN 有没有配、能不能读到仓库
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

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(
      dispatch(env)
        .then((r) => console.log(`cron dispatch ${r.status} ${r.text}`))
        .catch((e) => console.error(`cron dispatch failed: ${e && e.stack || e}`)),
    );
  },

  async fetch(request, env) {
    const path = new URL(request.url).pathname;
    try {
      if (path.startsWith("/debug")) {
        const info = await selfCheck(env);
        return new Response(JSON.stringify(info, null, 2) + "\n", {
          headers: { "content-type": "application/json; charset=utf-8" },
        });
      }
      const r = await dispatch(env);
      return new Response(`dispatch -> ${r.status} ${r.text}\n`);
    } catch (err) {
      return new Response(`EXCEPTION: ${(err && err.stack) || err}\n`, { status: 200 });
    }
  },
};