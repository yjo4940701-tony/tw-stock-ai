// Cloudflare Worker: 台股新聞代理
// 用途：抓 Google News RSS 並回傳 JSON（含 CORS header，供 GitHub Pages 使用）

export default {
  async fetch(request) {
    try {
      return await handleRequest(request);
    } catch (e) {
      // 頂層保底：任何意外都回 200+空結果，永遠不讓 Cloudflare 噴 502
      return json({ ok: true, count: 0, items: [], note: 'Worker 例外: ' + e.message });
    }
  }
};

async function handleRequest(request) {
    // 處理 CORS preflight
    if (request.method === 'OPTIONS') {
      return new Response(null, {
        headers: {
          'Access-Control-Allow-Origin': '*',
          'Access-Control-Allow-Methods': 'GET, OPTIONS',
          'Access-Control-Max-Age': '86400',
        }
      });
    }

    const url = new URL(request.url);
    const type = url.searchParams.get('type') || 'news';

    // ── 路由：三大法人（type=ii） ──────────────────────
    if (type === 'ii') {
      const stockId   = url.searchParams.get('id') || '';
      const startDate = url.searchParams.get('start') || '';
      if (!stockId) return json({ ok: false, message: '缺少 id 參數' }, 400);

      const token = url.searchParams.get('token') || '';
      const fmUrl = `https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInstitutionalInvestors&data_id=${stockId}&start_date=${startDate}${token ? '&token=' + token : ''}`;
      try {
        const resp = await fetch(fmUrl, { headers: { 'User-Agent': 'Mozilla/5.0' } });
        const text = await resp.text();
        // 不管狀態碼，直接把 FinMind 回應透傳（方便 debug）
        return new Response(text, {
          status: 200,
          headers: {
            'Content-Type': 'application/json;charset=utf-8',
            'Access-Control-Allow-Origin': '*',
            'X-FM-Status': String(resp.status)
          }
        });
      } catch (e) {
        return json({ ok: false, message: 'Worker fetch 失敗: ' + e.message }, 502);
      }
    }

    // ── 路由：Google News（type=news，預設） ────────────
    const stockId   = url.searchParams.get('id')   || '';
    const stockName = url.searchParams.get('name') || '';
    const count     = Math.min(+(url.searchParams.get('count') || 10), 20);

    if (!stockId) {
      return json({ ok: false, message: '缺少 id 參數' }, 400);
    }

    // 搜尋關鍵字：公司名稱 + 股號（提升相關性）
    const query   = stockName ? `${stockName} ${stockId}` : stockId;
    const rssUrl  = `https://news.google.com/rss/search?q=${encodeURIComponent(query)}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant`;

    // Google News 對 Cloudflare IP 會間歇性回 503，重試最多 3 次
    // 每次加 4 秒 timeout，防止慢回應累積超過 Cloudflare 30 秒 wall-time → 502
    const UA_LIST = [
      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
      'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15',
      'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile Safari/604.1'
    ];
    let xml = null, lastStatus = 0;
    for (let attempt = 0; attempt < 3; attempt++) {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), 4000); // 每次最多等 4 秒
      try {
        const resp = await fetch(rssUrl, {
          signal: ctrl.signal,
          headers: {
            'User-Agent': UA_LIST[attempt % UA_LIST.length],
            'Accept': 'application/rss+xml, application/xml, text/xml, */*',
            'Accept-Language': 'zh-TW,zh;q=0.9,en;q=0.8'
          },
          cf: { cacheTtl: 1800, cacheEverything: true }
        });
        lastStatus = resp.status;
        if (resp.ok) { xml = await resp.text(); break; }
      } catch (e) {
        lastStatus = e.name === 'AbortError' ? -408 : -1;
      } finally {
        clearTimeout(timer);
      }
    }
    if (xml === null) {
      // 抓不到新聞時回 200 + 空陣列（讓前端優雅降級，不在 console 跳紅錯）
      return json({ ok: true, query, count: 0, items: [], note: 'Google News 暫時無法存取 (HTTP ' + lastStatus + ')' });
    }

    // 解析 RSS XML → 取 title / link / pubDate / source
    const items = [];
    const re = /<item>([\s\S]*?)<\/item>/g;
    let m;
    while ((m = re.exec(xml)) !== null && items.length < count) {
      const block = m[1];
      const title   = extract(block, 'title');
      const link    = extract(block, 'link') || extractLink(block);
      const pubDate = extract(block, 'pubDate');
      const source  = extract(block, 'source');
      if (title) items.push({ title, link, pubDate, source });
    }

    return json({ ok: true, query, count: items.length, items });
}

// ── 工具函數 ─────────────────────────────────────────
function extract(str, tag) {
  // 支援 CDATA 和純文字
  const r = new RegExp(`<${tag}[^>]*>(?:<!\\[CDATA\\[([\\s\\S]*?)\\]\\]>|([\\s\\S]*?))</${tag}>`, 'i');
  const m = str.match(r);
  return m ? (m[1] || m[2] || '').trim() : '';
}

function extractLink(str) {
  // <link> 在 RSS 裡有時沒有結束標籤
  const m = str.match(/<link\s*\/>[\s\S]*?<([^/])/);
  if (m) return '';
  const m2 = str.match(/(?<=<\/title>\s*)<[^<]*?(https?:\/\/[^\s<>"]+)/);
  return m2 ? m2[1] : '';
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      'Content-Type': 'application/json;charset=utf-8',
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, OPTIONS',
      'Cache-Control': 'public, max-age=1800'
    }
  });
}
