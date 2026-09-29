// 準時觸發總經報告：Cloudflare cron 比 GitHub Actions 免費排程準（後者常延遲 4~8 小時）。
// 到點呼叫 GitHub workflow_dispatch 跑 macro-report.yml，明確帶 slot=am/pm。
// Secret：GH_TOKEN（能觸發本 repo workflow 的 GitHub token），用 `npx wrangler secret put GH_TOKEN` 設定，不寫進程式碼。
// 手動測試：GET /run?slot=am&key=<TEST_KEY>（TEST_KEY 也是 secret，防止別人亂觸發）。

const REPO = 'yjo4940701-tony/tw-stock-ai';
const WORKFLOW = 'macro-report.yml';

async function dispatch(env, slot) {
  const r = await fetch(`https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${env.GH_TOKEN}`,
      'Accept': 'application/vnd.github+json',
      'User-Agent': 'macro-trigger-worker',
    },
    body: JSON.stringify({ ref: 'main', inputs: { slot } }),
  });
  return { status: r.status, body: r.status === 204 ? 'ok' : await r.text() };
}

export default {
  async scheduled(event, env, ctx) {
    const slot = event.cron === '0 9 * * *' ? 'pm' : 'am';
    const res = await dispatch(env, slot);
    console.log('dispatch', slot, res.status, res.body);
  },

  async fetch(req, env) {
    const url = new URL(req.url);
    if (url.pathname !== '/run') return new Response('not found', { status: 404 });
    if (!env.TEST_KEY || url.searchParams.get('key') !== env.TEST_KEY) return new Response('forbidden', { status: 403 });
    const slot = url.searchParams.get('slot') === 'pm' ? 'pm' : 'am';
    const res = await dispatch(env, slot);
    return new Response(JSON.stringify({ slot, ...res }), { status: 200, headers: { 'Content-Type': 'application/json' } });
  },
};
