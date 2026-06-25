/**
 * crypto_scan.js — 虛擬貨幣訊號雲端自動掃描 + Telegram 推送
 *
 * 每小時(cron)跑：讀公開 Gist 的幣清單(tw_crypto_basket) + 掃描設定(tw_crypto_scan_cfg)
 * → 逐幣抓派網 K 線(設定的週期) → 依設定策略(60MA穿越/三國/三刀流)算「最後一根已收 K」的訊號
 * → 只推「剛收線那根」剛出現的訊號(無狀態去重，任何週期都只推一次) → TG。純通知，不下單。
 *
 * 設定從網頁可調(寫進 Gist)；缺/壞 → 退預設 60MA穿越·4H·雙向。
 *   env: TG_BOT_TOKEN, TG_CHAT_ID
 *   node scripts/crypto_scan.js [--dry|--test]
 */
const path = require('path');
const E = require(path.join(__dirname, '..', 'backtest', 'engine.js'));
const { getCryptoCandles } = require(path.join(__dirname, '..', 'backtest', 'fetch_pionex.js'));

const GIST_ID = '0b9966cb6fc32b5aeffe4ad7bdc07836';
const DEFAULT_BASKET = ['BTC_USDT', 'ETH_USDT', 'SOL_USDT', 'BNB_USDT', 'XRP_USDT', 'DOGE_USDT', 'ADA_USDT', 'AVAX_USDT'];
const DEFAULT_CFG = { strategy: '60ma', tf: '4H', direction: 'both', maPeriod: 60 };
const TF_MAP = { '1H': { iv: '60M', ms: 3600e3 }, '4H': { iv: '4H', ms: 4 * 3600e3 }, '1D': { iv: '1D', ms: 24 * 3600e3 } };
const SCAN_INTERVAL_MS = 3600e3;   // 每小時掃 → 「剛收線」= 收線在本間隔內
const LIMIT = 500;                 // 三國劉備240暖機足夠（4H/1H/日K 皆 >240 根）
const THROTTLE_MS = 200;
const DRY = process.argv.includes('--dry');

const sleep = ms => new Promise(r => setTimeout(r, ms));
const clean = o => { const r = {}; for (const k in o) if (o[k] != null) r[k] = o[k]; return r; };

// 一次讀 Gist 設定檔，回傳幣清單與掃描設定（缺/壞各自退預設）
async function readGist() {
  try {
    const r = await fetch(`https://api.github.com/gists/${GIST_ID}`);
    const g = await r.json();
    const files = g.files || {};
    const f = files['tw-stock-settings.json'] || Object.values(files)[0];
    const all = JSON.parse(f.content);
    const basket = Array.isArray(all.tw_crypto_basket) && all.tw_crypto_basket.length ? all.tw_crypto_basket : DEFAULT_BASKET;
    const cfg = Object.assign({}, DEFAULT_CFG, all.tw_crypto_scan_cfg || {});
    if (!TF_MAP[cfg.tf]) cfg.tf = DEFAULT_CFG.tf;                      // 週期保險
    if (!['60ma', 'threeKingdoms', 'threeBlade'].includes(cfg.strategy)) cfg.strategy = DEFAULT_CFG.strategy;
    if (!['long', 'short', 'both'].includes(cfg.direction)) cfg.direction = 'both';
    return { basket, cfg };
  } catch (e) {
    console.warn('[Gist] 讀取失敗，全退預設:', e.message);
    return { basket: DEFAULT_BASKET, cfg: { ...DEFAULT_CFG } };
  }
}

// 找最後「已收 K」索引 + 是否「剛收線（本掃描間隔內）」→ 去重關鍵
function closedBar(candles, ms) {
  const now = Date.now();
  for (let i = candles.length - 1; i >= 0; i--) {
    const t = Date.parse(candles[i].time.replace(' ', 'T') + 'Z');
    if (!isNaN(t) && now - t >= ms) {
      return { i, justClosed: (now - (t + ms)) < SCAN_INTERVAL_MS };
    }
  }
  return { i: -1, justClosed: false };
}

// 依設定算單幣訊號（在最後已收 K）。回傳 {ready, long, short, price, stale}
function evalSignal(candles, cfg) {
  const ms = TF_MAP[cfg.tf].ms;
  const { i, justClosed } = closedBar(candles, ms);
  if (i < 1) return { ready: false };
  if (!justClosed) return { ready: true, stale: true };   // 不是剛收線 → 去重，不推
  let long = false, short = false, price = candles[i].close;
  if (cfg.strategy === '60ma') {
    const closes = candles.map(c => c.close);
    const ma = E.sma(closes, cfg.maPeriod || 60);
    if (ma[i] == null || ma[i - 1] == null) return { ready: false };
    long = closes[i] > ma[i] && closes[i - 1] <= ma[i - 1];
    short = closes[i] < ma[i] && closes[i - 1] >= ma[i - 1];
  } else {
    const params = cfg.strategy === 'threeKingdoms'
      ? clean({ strategy: 'threeKingdoms', lbPeriod: cfg.tk && cfg.tk.lb, gyPeriod: cfg.tk && cfg.tk.gy, zfPeriod: cfg.tk && cfg.tk.zf, allowBounce: cfg.tk && cfg.tk.allowBounce })
      : clean({ strategy: 'threeBlade', ...(cfg.blade || {}) });
    const s = E.lastSignal(candles.slice(0, i + 1), params);   // slice → lastSignal 評估已收 K
    if (!s.ready) return { ready: false };
    long = !!s.fresh; short = !!s.freshShort; price = s.price;
  }
  if (cfg.direction === 'long') short = false;
  if (cfg.direction === 'short') long = false;
  return { ready: true, long, short, price };
}

function label(sym) {
  const perp = sym.endsWith('_PERP');
  const base = sym.replace('_PERP', '').replace('_USDT', '');
  return `${base}/USDT${perp ? ' 永續' : ''}`;
}
function stratLabel(cfg) {
  const m = { '60ma': `${cfg.maPeriod || 60}MA穿越`, threeKingdoms: '三國', threeBlade: '三刀流' };
  return `${m[cfg.strategy] || cfg.strategy} · ${cfg.tf}`;
}

async function tgSend(text) {
  const token = process.env.TG_BOT_TOKEN, chat = process.env.TG_CHAT_ID;
  if (!token || !chat) { console.warn('[TG] 缺 TG_BOT_TOKEN/TG_CHAT_ID，略過推送'); return; }
  const r = await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ chat_id: chat, text, parse_mode: 'HTML', disable_web_page_preview: true })
  });
  const j = await r.json();
  if (!j.ok) console.warn('[TG] 推送失敗:', JSON.stringify(j).slice(0, 150));
  else console.log('[TG] 已推送');
}

async function main() {
  if (process.argv.includes('--test')) {
    await tgSend('🪙 派網訊號掃描 — TG 通道測試 ✅（這是一次性測試訊息）');
    return;
  }
  const { basket, cfg } = await readGist();
  console.log(`掃描 ${basket.length} 檔（${stratLabel(cfg)}，方向 ${cfg.direction}）:`, basket.join(', '));
  const hits = [];
  for (const sym of basket) {
    try {
      const candles = await getCryptoCandles(sym, TF_MAP[cfg.tf].iv, LIMIT);
      const s = evalSignal(candles, cfg);
      if (!s.ready) console.log(`  ${sym}: 資料不足暖機`);
      else if (s.stale) console.log(`  ${sym}: 非剛收線（去重略過）`);
      else if (s.long)  { hits.push({ sym, dir: 'long', price: s.price }); console.log(`  ${sym}: 🟢 做多訊號 @ ${s.price}`); }
      else if (s.short) { hits.push({ sym, dir: 'short', price: s.price }); console.log(`  ${sym}: 🔴 做空訊號 @ ${s.price}`); }
      else console.log(`  ${sym}: 無新訊號`);
    } catch (e) {
      console.warn(`  ${sym}: 抓取/計算失敗 - ${e.message}`);
    }
    await sleep(THROTTLE_MS);
  }

  if (!hits.length) { console.log('本次無新訊號，不推送'); return; }
  const lines = hits.map(h => `${h.dir === 'long' ? '🟢' : '🔴'} <b>${label(h.sym)}</b> ${h.dir === 'long' ? '做多' : '做空'}訊號 @ ${h.price}`);
  const msg = `🪙 <b>派網訊號掃描</b>（${stratLabel(cfg)}）\n剛出現進場訊號 ${hits.length} 檔：\n\n` + lines.join('\n')
            + `\n\n<i>純技術面訊號通知，非投資建議；不代下單。</i>`;
  console.log('\n--- 推送內容 ---\n' + msg.replace(/<[^>]+>/g, ''));
  if (!DRY) await tgSend(msg);
}

if (require.main === module) {
  main().catch(e => { console.error('crypto_scan 失敗:', e); process.exit(1); });
}
module.exports = { evalSignal, closedBar, stratLabel, readGist, TF_MAP, DEFAULT_CFG };
