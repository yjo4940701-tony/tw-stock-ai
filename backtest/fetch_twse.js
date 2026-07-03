/**
 * fetch_twse.js — Node 端台股即時報價（TWSE mis 端點）
 *
 * 盤中用官方即時端點抓「當前成交價」，供 tw_scan.js 把「今天這一根」接在歷史日K 後面算訊號。
 * - 端點：https://mis.twse.com.tw/stock/api/getStockInfo.jsp（伺服器端抓無 CORS 問題，需帶 Referer）
 * - 不知上市/上櫃 → 同時查 tse_ 與 otc_ 兩個前綴，回應只會回真正存在的那個
 * - 欄位：z=當前成交價、o=開、h=高、l=低、y=昨收、c=代號、n=名稱、t=撮合時間、tv=量
 *
 *   const { getRealtimeQuotes } = require('./fetch_twse');
 *   const q = await getRealtimeQuotes(['2330','5483']);  // → { '2330': {price, open, high, low, prevClose, name, time}, ... }
 */
const CHUNK = 40;          // 每次請求最多 ex_ch 數（保守，避免被截）
const THROTTLE_MS = 350;

const sleep = ms => new Promise(r => setTimeout(r, ms));
const num = v => (v == null || v === '' || v === '-') ? null : (isNaN(+v) ? null : +v);

async function fetchChunk(exchStr) {
  const url = `https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=${exchStr}&json=1&delay=0&_=${Date.now()}`;
  const r = await fetch(url, {
    headers: {
      'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
      'Referer': 'https://mis.twse.com.tw/stock/index.jsp'
    }
  });
  const txt = await r.text();
  let j;
  try { j = JSON.parse(txt); } catch (e) { throw new Error('mis 回應非 JSON'); }
  return (j && j.msgArray) || [];
}

// tickers: 代號字串陣列 → { sid: {price, open, high, low, prevClose, name, time} }
async function getRealtimeQuotes(tickers) {
  // 每檔同時查上市(tse)與上櫃(otc)，回應只會回真正存在的市場
  const exch = [];
  for (const sid of tickers) { exch.push(`tse_${sid}.tw`); exch.push(`otc_${sid}.tw`); }
  const out = {};
  for (let i = 0; i < exch.length; i += CHUNK) {
    const chunk = exch.slice(i, i + CHUNK).join('|');
    let arr = [];
    try { arr = await fetchChunk(chunk); }
    catch (e) { console.warn('[TWSE] 一批抓取失敗，略過:', e.message); }
    for (const m of arr) {
      const sid = m.c;
      if (!sid) continue;
      const price = num(m.z);
      // 有些盤中無成交價(z='-')→ 退用揭示參考價 pz，再退昨收 y
      const usePrice = price != null ? price : (num(m.pz) != null ? num(m.pz) : num(m.y));
      if (usePrice == null) continue;
      out[sid] = {
        price: usePrice, open: num(m.o), high: num(m.h), low: num(m.l),
        prevClose: num(m.y), name: m.n || '', time: m.t || ''
      };
    }
    if (i + CHUNK < exch.length) await sleep(THROTTLE_MS);
  }
  return out;
}

module.exports = { getRealtimeQuotes };
