/**
 * crypto_scan.js — 虛擬貨幣訊號雲端自動掃描 + Telegram 推送
 *
 * 每 4H(cron)跑：讀公開 Gist 的「我的幣清單」(tw_crypto_basket) → 逐幣抓派網 4H K 線
 * → 60MA 穿越策略 → 收盤上穿60MA=做多、下穿60MA=做空（取最後一根「已收」K）→ 推 TG。
 * 只推「剛穿越那一根」（rising edge，無狀態去重：同一根 4H 只在它收線後那次掃描觸發）。純通知，不下單。
 *
 *   env: TG_BOT_TOKEN, TG_CHAT_ID
 *   node scripts/crypto_scan.js [--dry]   (--dry: 只印不推 TG)
 */
const path = require('path');
const E = require(path.join(__dirname, '..', 'backtest', 'engine.js'));   // 用 E.sma
const { getCryptoCandles } = require(path.join(__dirname, '..', 'backtest', 'fetch_pionex.js'));

const GIST_ID = '0b9966cb6fc32b5aeffe4ad7bdc07836';
const DEFAULT_BASKET = ['BTC_USDT', 'ETH_USDT', 'SOL_USDT', 'BNB_USDT', 'XRP_USDT', 'DOGE_USDT', 'ADA_USDT', 'AVAX_USDT'];
const INTERVAL = '4H';            // 4H K 線（雜訊比 1H 少）
const INTERVAL_MS = 4 * 3600e3;   // 一根 4H 的毫秒，判斷 K 是否已收
const MA_PERIOD = 60;             // 60MA 穿越
const LIMIT = 300;                // 60MA 暖機 + 緩衝（4H×300≈50 天）
const THROTTLE_MS = 200;          // 逐幣節流，對派網客氣
const DRY = process.argv.includes('--dry');

const sleep = ms => new Promise(r => setTimeout(r, ms));

// 找「最後一根已收線」的索引（派網最新一根是形成中未收 K）：barStart(UTC) + 一根時長 <= now
function lastClosedIdx(candles) {
  const now = Date.now();
  for (let i = candles.length - 1; i >= 0; i--) {
    const t = Date.parse(candles[i].time.replace(' ', 'T') + 'Z');   // 'YYYY-MM-DD HH:mm' UTC
    if (!isNaN(t) && now - t >= INTERVAL_MS) return i;
  }
  return -1;
}

// 60MA 穿越訊號（取最後已收 K）：rising edge = 該根剛穿越
function maCrossSignal(candles) {
  const closes = candles.map(c => c.close);
  const ma = E.sma(closes, MA_PERIOD);
  const i = lastClosedIdx(candles);
  if (i < 1 || ma[i] == null || ma[i - 1] == null) return { ready: false };
  const up = closes[i] > ma[i] && closes[i - 1] <= ma[i - 1];   // 上穿
  const down = closes[i] < ma[i] && closes[i - 1] >= ma[i - 1]; // 下穿
  return { ready: true, long: up, short: down, price: closes[i] };
}

// 讀公開 Gist 的幣清單；失敗/空 → 退預設 8 檔
async function getBasket() {
  try {
    const r = await fetch(`https://api.github.com/gists/${GIST_ID}`);
    const g = await r.json();
    const files = g.files || {};
    const f = files['tw-stock-settings.json'] || Object.values(files)[0];
    const cfg = JSON.parse(f.content);
    const b = cfg.tw_crypto_basket;
    if (Array.isArray(b) && b.length) return b;
  } catch (e) {
    console.warn('[Gist] 讀清單失敗，退預設:', e.message);
  }
  return DEFAULT_BASKET;
}

// 顯示名稱：BTC_USDT→BTC/USDT、MUX_USDT_PERP→MUX/USDT 永續
function label(sym) {
  const perp = sym.endsWith('_PERP');
  const base = sym.replace('_PERP', '').replace('_USDT', '');
  return `${base}/USDT${perp ? ' 永續' : ''}`;
}

async function tgSend(text) {
  const token = process.env.TG_BOT_TOKEN, chat = process.env.TG_CHAT_ID;
  if (!token || !chat) { console.warn('[TG] 缺 TG_BOT_TOKEN/TG_CHAT_ID，略過推送'); return; }
  const r = await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ chat_id: chat, text, parse_mode: 'HTML', disable_web_page_preview: true })
  });
  const j = await r.json();
  if (!j.ok) console.warn('[TG] 推送失敗:', JSON.stringify(j).slice(0, 150));
  else console.log('[TG] 已推送');
}

async function main() {
  if (process.argv.includes('--test')) {   // 驗證 TG 通道用：送一則測試訊息後結束
    await tgSend('🪙 派網訊號掃描 — TG 通道測試 ✅（這是一次性測試訊息）');
    return;
  }
  const basket = await getBasket();
  console.log(`掃描 ${basket.length} 檔（60MA穿越·4H）:`, basket.join(', '));
  const hits = [];   // { sym, dir }
  for (const sym of basket) {
    try {
      const candles = await getCryptoCandles(sym, INTERVAL, LIMIT);
      const s = maCrossSignal(candles);
      if (!s.ready) { console.log(`  ${sym}: 資料不足暖機`); }
      else if (s.long)  { hits.push({ sym, dir: 'long',  price: s.price }); console.log(`  ${sym}: 🟢 上穿60MA @ ${s.price}`); }
      else if (s.short) { hits.push({ sym, dir: 'short', price: s.price }); console.log(`  ${sym}: 🔴 下穿60MA @ ${s.price}`); }
      else              { console.log(`  ${sym}: 無新訊號`); }
    } catch (e) {
      console.warn(`  ${sym}: 抓取/計算失敗 - ${e.message}`);
    }
    await sleep(THROTTLE_MS);
  }

  if (!hits.length) { console.log('本次無新訊號，不推送'); return; }

  const lines = hits.map(h =>
    `${h.dir === 'long' ? '🟢' : '🔴'} <b>${label(h.sym)}</b> ${h.dir === 'long' ? '上穿60MA 做多' : '下穿60MA 做空'} @ ${h.price}`);
  const msg = `🪙 <b>派網訊號掃描</b>（60MA穿越 · 4H）\n剛出現穿越訊號 ${hits.length} 檔：\n\n` + lines.join('\n')
            + `\n\n<i>純技術面訊號通知，非投資建議；不代下單。</i>`;
  console.log('\n--- 推送內容 ---\n' + msg.replace(/<[^>]+>/g, ''));
  if (!DRY) await tgSend(msg);
}

main().catch(e => { console.error('crypto_scan 失敗:', e); process.exit(1); });
