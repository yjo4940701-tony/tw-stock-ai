// Cloudflare Worker: 台股新聞代理
// 用途：抓 Google News RSS 並回傳 JSON（含 CORS header，供 GitHub Pages 使用）

export default {
  async fetch(request, env) {
    try {
      return await handleRequest(request, env);
    } catch (e) {
      // 頂層保底：任何意外都回 200+空結果，永遠不讓 Cloudflare 噴 502
      return json({ ok: true, count: 0, items: [], note: 'Worker 例外: ' + e.message });
    }
  }
};

async function handleRequest(request, env) {
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

    // ── 路由：派網 Pionex 公開行情（type=crypto） ──────────
    // 例：?type=crypto&ep=market/klines&symbol=BTC_USDT&interval=1D&limit=200
    //     ?type=crypto&ep=market/tickers
    //     ?type=crypto&ep=common/symbols                （現貨商品）
    //     ?type=crypto&ep=common/symbols&pxtype=PERP    （永續合約商品；pxtype 會轉成派網的 type）
    if (type === 'crypto') {
      const ep = url.searchParams.get('ep') || '';
      // 白名單：只放公開行情端點，絕不轉發簽名/交易端點
      const ALLOWED = ['market/klines', 'market/tickers', 'market/bookTickers', 'market/depth', 'market/trades', 'common/symbols'];
      if (!ALLOWED.includes(ep)) {
        return json({ ok: false, message: '不允許的端點: ' + ep }, 400);
      }
      // 透傳除 type/ep/pxtype 外的所有參數；pxtype → 派網的 type（避免與 worker 的 type=crypto 撞名）
      const fwd = new URLSearchParams();
      for (const [k, v] of url.searchParams) {
        if (k !== 'type' && k !== 'ep' && k !== 'pxtype') fwd.set(k, v);
      }
      const pxtype = url.searchParams.get('pxtype');
      if (pxtype) fwd.set('type', pxtype);
      const pxUrl = `https://api.pionex.com/api/v1/${ep}?${fwd.toString()}`;
      // 商品清單（common/symbols）幾乎不變 → 快取 1 小時；報價/K線即時 → 維持 30 秒
      const ttl = ep === 'common/symbols' ? 3600 : 30;
      try {
        const resp = await fetch(pxUrl, {
          headers: { 'User-Agent': 'Mozilla/5.0' },
          cf: { cacheTtl: ttl, cacheEverything: true }
        });
        const text = await resp.text();
        return new Response(text, {
          status: 200,
          headers: {
            'Content-Type': 'application/json;charset=utf-8',
            'Access-Control-Allow-Origin': '*',
            'Cache-Control': 'public, max-age=' + ttl,
            'X-PX-Status': String(resp.status)
          }
        });
      } catch (e) {
        return json({ ok: false, message: '派網 fetch 失敗: ' + e.message }, 502);
      }
    }

    // ── 路由：TradingView 財經日曆（type=tvcal） ──────────
    // 例：?type=tvcal&from=2026-07-01T00:00:00.000Z&to=2026-07-14T00:00:00.000Z&countries=US,TW
    // 前端不能設 Referer/User-Agent（被瀏覽器保護），故由 Worker 補上這兩個標頭繞過 TV 的 403。
    if (type === 'tvcal') {
      const now = new Date();
      const from = url.searchParams.get('from') || new Date(now.getTime() - 2 * 864e5).toISOString();
      const to   = url.searchParams.get('to')   || new Date(now.getTime() + 14 * 864e5).toISOString();
      const countries = url.searchParams.get('countries') || 'US,TW';
      const tvUrl = `https://economic-calendar.tradingview.com/events?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}&countries=${encodeURIComponent(countries)}`;
      try {
        const resp = await fetch(tvUrl, {
          headers: {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
            'Referer': 'https://www.tradingview.com/',
            'Origin': 'https://www.tradingview.com',
            'Accept': 'application/json'
          },
          cf: { cacheTtl: 1800, cacheEverything: true }  // 日曆變動慢，快取 30 分鐘 → TV 極少被打
        });
        const text = await resp.text();
        return new Response(text, {
          status: 200,
          headers: {
            'Content-Type': 'application/json;charset=utf-8',
            'Access-Control-Allow-Origin': '*',
            'Cache-Control': 'public, max-age=1800',
            'X-TV-Status': String(resp.status)
          }
        });
      } catch (e) {
        return json({ ok: false, message: 'TV 日曆 fetch 失敗: ' + e.message }, 502);
      }
    }

    // ── 路由：Google News（type=news，預設） ────────────
    const stockId   = url.searchParams.get('id')   || '';
    const stockName = url.searchParams.get('name') || '';
    const count     = Math.min(+(url.searchParams.get('count') || 10), 20);

    if (!stockId) {
      return json({ ok: false, message: '缺少 id 參數' }, 400);
    }

    // Yahoo 台股個股新聞 RSS（不封 Cloudflare IP，取代原本 Google News）
    const query   = stockName ? `${stockName} ${stockId}` : stockId;
    const rssUrl  = `https://tw.stock.yahoo.com/rss?s=${encodeURIComponent(stockId)}`;

    // KV cache key（per 股票代號）
    const kvKey = 'gnews_' + stockId;

    // 重試最多 3 次，每次 4 秒 timeout
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
      // Google 抓不到 → 嘗試回傳 KV 快取的舊結果
      const cached = env.NEWS_CACHE ? await env.NEWS_CACHE.get(kvKey, 'json') : null;
      if (cached) {
        return json({ ...cached, cached: true, note: 'Google News 暫時不可用，顯示快取資料' });
      }
      return json({ ok: true, count: 0, items: [], note: 'Yahoo 新聞暫時無法存取 (HTTP ' + lastStatus + ')' });
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

    const result = { ok: true, query, count: items.length, items };

    // 成功抓到新聞 → 存進 KV（TTL 4 小時）
    if (env.NEWS_CACHE && items.length > 0) {
      await env.NEWS_CACHE.put(kvKey, JSON.stringify(result), { expirationTtl: 14400 });
    }

    return json(result);
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
