/**
 * fetch_pionex.js — Node 端派網(Pionex)公開行情 K 線抓取
 *
 * Node 直連 api.pionex.com（無 CORS 限制），時間格式與網頁端 cryptoCandleTime 完全一致
 * （UTC ISO：日K 取 YYYY-MM-DD、分K 取 YYYY-MM-DD HH:mm）→ 確保網頁/CLI 回測結果相同。
 *
 *   const { getCryptoCandles } = require('./fetch_pionex');
 *   const candles = await getCryptoCandles('BTC_USDT', '1D', 300);
 *
 * interval：1D / 4H / 60M / 15M ...（注意無 1H，是 60M）
 */
async function getCryptoCandles(symbol, interval = '1D', limit = 300) {
  const u = `https://api.pionex.com/api/v1/market/klines` +
            `?symbol=${symbol}&interval=${interval}&limit=${limit}`;
  const r = await fetch(u);
  const j = await r.json();
  if (!j.result || !j.data || !j.data.klines) {
    throw new Error(`Pionex klines 失敗: ${j.message || JSON.stringify(j).slice(0, 120)}`);
  }
  const intraday = interval !== '1D';
  const ks = j.data.klines.slice().reverse(); // 派網新到舊 → 舊到新
  return ks.map(k => {
    const iso = new Date(k.time).toISOString();
    const time = intraday ? iso.slice(0, 16).replace('T', ' ') : iso.slice(0, 10);
    return { time, open: +k.open, high: +k.high, low: +k.low, close: +k.close, volume: +k.volume };
  });
}

module.exports = { getCryptoCandles };
