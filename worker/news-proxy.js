// Cloudflare Worker: 台股新聞代理
// 用途：抓 Google News RSS 並回傳 JSON（含 CORS header，供 GitHub Pages 使用）

export default {
  async fetch(request) {
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
    const stockId   = url.searchParams.get('id')   || '';
    const stockName = url.searchParams.get('name') || '';
    const count     = Math.min(+(url.searchParams.get('count') || 10), 20);

    if (!stockId) {
      return json({ ok: false, message: '缺少 id 參數' }, 400);
    }

    // 搜尋關鍵字：公司名稱 + 股號（提升相關性）
    const query   = stockName ? `${stockName} ${stockId}` : stockId;
    const rssUrl  = `https://news.google.com/rss/search?q=${encodeURIComponent(query)}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant`;

    let xml;
    try {
      const resp = await fetch(rssUrl, {
        headers: {
          'User-Agent': 'Mozilla/5.0 (compatible; RSSReader/1.0)',
          'Accept': 'application/rss+xml, application/xml, text/xml'
        },
        cf: { cacheTtl: 1800, cacheEverything: true } // Cloudflare 快取 30 分鐘
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      xml = await resp.text();
    } catch (e) {
      return json({ ok: false, message: '抓取失敗: ' + e.message }, 502);
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
};

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
