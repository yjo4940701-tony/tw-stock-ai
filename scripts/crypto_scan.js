/**
 * crypto_scan.js — 虛擬貨幣訊號雲端自動掃描 + Telegram 推送
 *
 * 每小時(cron)跑：讀公開 Gist 的「我的幣清單」(tw_crypto_basket) → 逐幣抓派網 1H K 線
 * → 三國策略 engine.lastSignal() → 挑「最新一根剛進場」的做多/做空 rising edge → 推 TG。
 * 只推新訊號（rising edge 無狀態去重，同訊號下小時不再是最新根 → 不重推）。純通知，不下單。
 *
 *   env: TG_BOT_TOKEN, TG_CHAT_ID
 *   node scripts/crypto_scan.js [--dry]   (--dry: 只印不推 TG)
 */
const path = require('path');
const E = require(path.join(__dirname, '..', 'backtest', 'engine.js'));
const { getCryptoCandles } = require(path.join(__dirname, '..', 'backtest', 'fetch_pionex.js'));

const GIST_ID = '0b9966cb6fc32b5aeffe4ad7bdc07836';
const DEFAULT_BASKET = ['BTC_USDT', 'ETH_USDT', 'SOL_USDT', 'BNB_USDT', 'XRP_USDT', 'DOGE_USDT', 'ADA_USDT', 'AVAX_USDT'];
const INTERVAL = '60M';   // 派網 1H = 60M
const LIMIT = 500;        // 派網單次上限；三國劉備240需 ~240 根暖機
const THROTTLE_MS = 200;  // 逐幣節流，對派網客氣
const DRY = process.argv.includes('--dry');

const sleep = ms => new Promise(r => setTimeout(r, ms));

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
  console.log(`掃描 ${basket.length} 檔（三國·1H）:`, basket.join(', '));
  const hits = [];   // { sym, dir }
  for (const sym of basket) {
    try {
      const candles = await getCryptoCandles(sym, INTERVAL, LIMIT);
      const s = E.lastSignal(candles, { strategy: 'threeKingdoms' });
      if (!s.ready) { console.log(`  ${sym}: 資料不足暖機`); }
      else if (s.fresh)      { hits.push({ sym, dir: 'long',  price: s.price }); console.log(`  ${sym}: 🟢 做多 rising edge @ ${s.price}`); }
      else if (s.freshShort) { hits.push({ sym, dir: 'short', price: s.price }); console.log(`  ${sym}: 🔴 做空 rising edge @ ${s.price}`); }
      else                   { console.log(`  ${sym}: 無新訊號`); }
    } catch (e) {
      console.warn(`  ${sym}: 抓取/計算失敗 - ${e.message}`);
    }
    await sleep(THROTTLE_MS);
  }

  if (!hits.length) { console.log('本次無新訊號，不推送'); return; }

  const lines = hits.map(h =>
    `${h.dir === 'long' ? '🟢' : '🔴'} <b>${label(h.sym)}</b> ${h.dir === 'long' ? '做多' : '做空'}訊號 @ ${h.price}`);
  const msg = `🪙 <b>派網訊號掃描</b>（三國 · 1H）\n剛出現進場訊號 ${hits.length} 檔：\n\n` + lines.join('\n')
            + `\n\n<i>純技術面訊號通知，非投資建議；不代下單。</i>`;
  console.log('\n--- 推送內容 ---\n' + msg.replace(/<[^>]+>/g, ''));
  if (!DRY) await tgSend(msg);
}

main().catch(e => { console.error('crypto_scan 失敗:', e); process.exit(1); });
