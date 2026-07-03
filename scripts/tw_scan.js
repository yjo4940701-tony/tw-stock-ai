/**
 * tw_scan.js — 台股自訂條件掃描 + Telegram 推送（盤中即時 + 收盤確認）
 *
 * 流程：讀公開 Gist（自選股 tw_groups + 掃描設定 tw_stock_scan_cfg）
 *   → 讀 data/ohlcv.json.gz 歷史日K（到昨天）
 *   → TWSE mis 抓自選股「現價」當作今天這一根 → engine.js 自訂條件 lastSignal
 *   → 剛觸發（rising edge）的做多/做空 → 當天去重 → TG。純通知，不下單。
 *
 * 兩段式：預設=盤中觸發（每 5 分鐘 cron）；--close=收盤確認（收盤後跑一次，各自去重）。
 * 去重狀態存本地檔（GitHub Actions cache 跨 run 保存），跨日自動清空。
 *
 *   env: TG_BOT_TOKEN, TG_CHAT_ID
 *   node scripts/tw_scan.js [--dry|--test|--close]
 */
const path = require('path');
const fs = require('fs');
const zlib = require('zlib');
const E = require(path.join(__dirname, '..', 'backtest', 'engine.js'));
const { getRealtimeQuotes } = require(path.join(__dirname, '..', 'backtest', 'fetch_twse.js'));

const GIST_ID = '0b9966cb6fc32b5aeffe4ad7bdc07836';
const OHLCV_PATH = path.join(__dirname, '..', 'data', 'ohlcv.json.gz');
const STATE_PATH = path.join(__dirname, '.tw_scan_state.json');  // 去重狀態（gitignore + Actions cache）
const DRY = process.argv.includes('--dry');
const CLOSE = process.argv.includes('--close');
const PHASE = CLOSE ? 'close' : 'intraday';

const twToday = () => new Date(Date.now() + 8 * 3600e3).toISOString().slice(0, 10);

// ── Gist：自選股清單 + 掃描設定 ──────────
async function readGist() {
  const r = await fetch(`https://api.github.com/gists/${GIST_ID}`);
  const g = await r.json();
  const files = g.files || {};
  const f = files['tw-stock-settings.json'] || Object.values(files)[0];
  const all = JSON.parse(f.content);
  // 自選股：蒐集所有群組的代號去重
  const groups = (all.tw_groups && all.tw_groups.list) || [];
  const set = new Set();
  groups.forEach(grp => (grp.tickers || []).forEach(t => set.add(String(t))));
  const tickers = [...set];
  const cfg = all.tw_stock_scan_cfg || null;
  return { tickers, cfg };
}

// ── 歷史日K（到昨天）──────────
function loadOhlcv() {
  const raw = zlib.gunzipSync(fs.readFileSync(OHLCV_PATH));
  return JSON.parse(raw.toString());
}
// 某檔的歷史 candles（跳 null 停牌日）
function histCandles(ohlcv, sid) {
  const s = ohlcv.stocks[sid];
  if (!s) return [];
  const out = [];
  for (let i = 0; i < ohlcv.dates.length; i++) {
    if (s.c[i] == null) continue;
    out.push({ time: ohlcv.dates[i], open: s.o[i], high: s.h[i], low: s.l[i], close: s.c[i] });
  }
  return out;
}

// ── 去重狀態（本地檔；跨日自動清空）──────────
function loadState() {
  try {
    const st = JSON.parse(fs.readFileSync(STATE_PATH, 'utf8'));
    if (st.date === twToday()) return st;
  } catch (e) {}
  return { date: twToday(), keys: [] };
}
function saveState(st) {
  try { fs.writeFileSync(STATE_PATH, JSON.stringify(st)); }
  catch (e) { console.warn('[state] 寫入失敗:', e.message); }
}

// ── 訊號判斷（自訂條件；用歷史日K + 今天現價當最新一根）──────────
function evalSignal(hist, todayBar, cfg, ohlcvUpdated) {
  let candles = hist;
  // ohlcv 尚未含今天 → 把今天現價接上去當最新一根；已含今天 → 用歷史即可（收盤後 build 完的情況）
  if (ohlcvUpdated !== twToday()) candles = hist.concat([todayBar]);
  const params = { strategy: 'custom', customLong: cfg.custom && cfg.custom.long, customShort: cfg.custom && cfg.custom.short };
  const s = E.lastSignal(candles, params);
  if (!s.ready) return { ready: false };
  let long = !!s.fresh, short = !!s.freshShort;
  if (cfg.direction === 'long') short = false;
  if (cfg.direction === 'short') long = false;
  return { ready: true, long, short, price: todayBar.close };
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

function grpCount(cfg) {
  return `做多${E.normalizeGroups(cfg.custom && cfg.custom.long).length}組/做空${E.normalizeGroups(cfg.custom && cfg.custom.short).length}組`;
}

async function main() {
  if (process.argv.includes('--test')) {
    await tgSend('📈 台股訊號掃描 — TG 通道測試 ✅（一次性測試訊息）');
    return;
  }
  const { tickers, cfg } = await readGist();
  if (!cfg || !cfg.custom || (!(cfg.custom.long || []).length && !(cfg.custom.short || []).length)) {
    console.log('尚未設定台股掃描條件（tw_stock_scan_cfg 空），跳過。'); return;
  }
  if (!tickers.length) { console.log('自選股清單為空，跳過。'); return; }
  console.log(`[${PHASE}] 掃描 ${tickers.length} 檔自選股（自訂條件 ${grpCount(cfg)}，方向 ${cfg.direction || 'both'}）`);

  const ohlcv = loadOhlcv();
  const quotes = await getRealtimeQuotes(tickers);
  const state = loadState();
  const firedSet = new Set(state.keys);

  const hits = [];
  for (const sid of tickers) {
    const q = quotes[sid];
    if (!q) { console.log(`  ${sid}: 無即時報價`); continue; }
    const hist = histCandles(ohlcv, sid);
    if (hist.length < 30) { console.log(`  ${sid}: 歷史日K 不足`); continue; }
    const todayBar = {
      time: twToday(),
      open: q.open != null ? q.open : q.price,
      high: q.high != null ? q.high : q.price,
      low: q.low != null ? q.low : q.price,
      close: q.price
    };
    const s = evalSignal(hist, todayBar, cfg, ohlcv.updated);
    if (!s.ready) { console.log(`  ${sid}: 暖機不足`); continue; }
    const dirs = [];
    if (s.long) dirs.push('long');
    if (s.short) dirs.push('short');
    if (!dirs.length) { console.log(`  ${sid}: 無新訊號`); continue; }
    for (const dir of dirs) {
      const key = `${PHASE}:${sid}:${dir}`;
      if (firedSet.has(key)) { console.log(`  ${sid} ${dir}: 今天已發過（去重略過）`); continue; }
      firedSet.add(key);
      hits.push({ sid, name: q.name, dir, price: s.price });
      console.log(`  ${sid} ${q.name}: ${dir === 'long' ? '🟢做多' : '🔴做空'}訊號 @ ${s.price}`);
    }
  }

  // 更新去重狀態（即使不 dry 也寫，讓 cache 保存）
  state.keys = [...firedSet];
  if (!DRY) saveState(state);

  if (!hits.length) { console.log('本次無新訊號，不推送'); return; }
  const phaseLabel = CLOSE ? '收盤確認' : '盤中觸發';
  const lines = hits.map(h => `${h.dir === 'long' ? '🟢' : '🔴'} <b>${h.sid} ${h.name}</b> ${h.dir === 'long' ? '做多' : '做空'} @ ${h.price}`);
  const msg = `📈 <b>台股訊號掃描</b>（自訂條件·${phaseLabel}）\n符合條件 ${hits.length} 檔：\n\n` + lines.join('\n')
            + `\n\n<i>純技術面訊號通知，非投資建議；不代下單。</i>`;
  console.log('\n--- 推送內容 ---\n' + msg.replace(/<[^>]+>/g, ''));
  if (!DRY) await tgSend(msg);
}

if (require.main === module) {
  main().catch(e => { console.error('tw_scan 失敗:', e); process.exit(1); });
}
module.exports = { evalSignal, histCandles, readGist };
