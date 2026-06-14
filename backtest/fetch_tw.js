/**
 * fetch_tw.js — Node 端 FinMind 日K 抓取 + 本地 JSON 快取
 *
 * - 匿名 GET（FinMind 限額 300 次/小時），抓完快取到 backtest/cache/<id>.json
 * - 快取當日有效，重跑回測/最佳化不重複打 API
 * - candles 欄位轉成引擎要的 {time, open, high, low, close, volume}
 *   FinMind：max=high、min=low
 *
 * 用法（程式內 require）：
 *   const { getCandles } = require('./fetch_tw');
 *   const candles = await getCandles('2330', { years: 2 });
 */
const fs = require('fs');
const path = require('path');

const CACHE_DIR = path.join(__dirname, 'cache');
if (!fs.existsSync(CACHE_DIR)) fs.mkdirSync(CACHE_DIR, { recursive: true });

function ymd(d) {
  return d.toISOString().slice(0, 10);
}

function cachePath(id) {
  return path.join(CACHE_DIR, `${id}.json`);
}

// 讀快取：今天抓過且涵蓋所需起始日 → 直接用
function readCache(id, startDate) {
  const fp = cachePath(id);
  if (!fs.existsSync(fp)) return null;
  try {
    const obj = JSON.parse(fs.readFileSync(fp, 'utf8'));
    if (obj.fetchedAt !== ymd(new Date())) return null;
    if (obj.start > startDate) return null; // 快取起點比需要的晚 → 重抓
    return obj.candles;
  } catch { return null; }
}

function writeCache(id, startDate, candles) {
  const obj = { id, start: startDate, fetchedAt: ymd(new Date()), candles };
  fs.writeFileSync(cachePath(id), JSON.stringify(obj));
}

async function fetchFinMind(id, startDate, endDate) {
  const u = `https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockPrice` +
            `&data_id=${id}&start_date=${startDate}&end_date=${endDate}`;
  const r = await fetch(u);
  const j = await r.json();
  if (j.status !== 200) throw new Error(`FinMind ${id}: ${j.msg || j.status}`);
  return (j.data || [])
    .filter(d => d.close > 0 && d.max > 0)
    .map(d => ({
      time: d.date,
      open: d.open, high: d.max, low: d.min, close: d.close,
      volume: d.Trading_Volume
    }));
}

/**
 * 取得 candles（優先快取）
 * @param {string} id 股票代號
 * @param {object} opt { years=2, force=false }
 */
async function getCandles(id, opt = {}) {
  const years = opt.years || 2;
  const end = new Date();
  const start = new Date();
  start.setFullYear(start.getFullYear() - years);
  const startStr = ymd(start), endStr = ymd(end);

  let candles;
  const cached = !opt.force ? readCache(id, startStr) : null;
  if (cached) {
    candles = cached;
  } else {
    candles = await fetchFinMind(id, startStr, endStr);
    if (candles.length) writeCache(id, startStr, candles);
  }
  // 快取可能涵蓋比要求更長的區間 → 一律切到要求的起始日，確保 --years 精準
  return candles.filter(c => c.time >= startStr);
}

module.exports = { getCandles, fetchFinMind };
