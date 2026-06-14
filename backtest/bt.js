#!/usr/bin/env node
/**
 * bt.js — 單股進階回測 CLI
 *
 * 用法：
 *   node bt.js --id 2330 [--years 2] [--trades] [--json]
 *   node bt.js --id 2330 --sl 2 --tp 3 --trail 2.5 --confirms 3
 *
 * 參數（覆寫引擎預設）：
 *   --id <代號>        必填
 *   --years <N>        抓幾年日K（預設 2）
 *   --sl/--tp/--trail  ATR 停損/停利/追蹤倍數
 *   --confirms <2|3>   三刀流確認數
 *   --capital <N>      本金（預設 100 萬）
 *   --no-trail         關閉追蹤止盈
 *   --trades           列出每筆交易
 *   --json             以 JSON 輸出（給程式接）
 */
const BT = require('./engine');
const { getCandles } = require('./fetch_tw');

function parseArgs(argv) {
  const a = {};
  for (let i = 2; i < argv.length; i++) {
    const k = argv[i];
    if (k === '--no-trail') { a.useTrailing = false; continue; }
    if (k === '--atr-risk') { a.atrRisk = true; continue; }
    if (k === '--trades') { a.showTrades = true; continue; }
    if (k === '--json') { a.json = true; continue; }
    if (k.startsWith('--')) { a[k.slice(2)] = argv[++i]; }
  }
  return a;
}

function buildParams(a) {
  const p = {};
  // 策略：3k / kingdoms / threeKingdoms → 三國；其餘預設三刀流
  if (a.strategy) {
    var s = String(a.strategy).toLowerCase();
    p.strategy = (s === '3k' || s === 'kingdoms' || s === 'threekingdoms' || s === '三國') ? 'threeKingdoms' : 'threeBlade';
  }
  if (a.sl != null) p.slMult = +a.sl;
  if (a.tp != null) p.tpMult = +a.tp;
  if (a.trail != null) p.trailMult = +a.trail;
  if (a.confirms != null) p.confirmsNeeded = +a.confirms;
  if (a.rsiLow != null) p.rsiLow = +a.rsiLow;
  if (a.lb != null) p.lbPeriod = +a.lb;
  if (a.gy != null) p.gyPeriod = +a.gy;
  if (a.zf != null) p.zfPeriod = +a.zf;
  if (a.atrRisk === true) p.useAtrRisk = true;   // 三國可選擇加 ATR 風控層
  if (a.capital != null) p.capital = +a.capital;
  if (a.useTrailing === false) p.useTrailing = false;
  return p;
}

function pct(x) { return (x >= 0 ? '+' : '') + x.toFixed(2) + '%'; }
function num(x) { return x.toLocaleString('en-US', { maximumFractionDigits: 0 }); }

function printReport(id, res) {
  const s = res.stats;
  const stratName = res.params.strategy === 'threeKingdoms'
    ? `三國 ${res.params.lbPeriod}/${res.params.gyPeriod}/${res.params.zfPeriod}MA` + (res.params.useAtrRisk ? ' + ATR風控' : '（純均線）')
    : '三刀流 + ATR 風控';
  console.log(`\n===== ${id} 進階回測（${stratName}）=====`);
  console.log(`期間          ${s.period}（${s.bars} 根日K）`);
  console.log(`本金          ${num(res.params.capital)}`);
  console.log(`最終權益      ${num(s.finalEquity)}`);
  console.log(`總報酬        ${pct(s.totalReturn)}   （Buy&Hold ${pct(s.buyHold)}）`);
  console.log(`交易次數      ${s.numTrades}`);
  console.log(`勝率          ${s.winRate.toFixed(1)}%`);
  console.log(`盈虧比 PF     ${s.profitFactor === Infinity ? '∞' : s.profitFactor.toFixed(2)}`);
  console.log(`平均獲利/虧損 ${num(s.avgWin)} / ${num(s.avgLoss)}`);
  console.log(`最大回撤      ${s.maxDrawdown.toFixed(2)}%`);
  console.log(`Sharpe        ${s.sharpe.toFixed(2)}`);
  console.log(`最長連勝/連敗 ${s.maxConsecWins} / ${s.maxConsecLosses}`);
  console.log(`出場原因      ${JSON.stringify(s.exitReasons)}`);
  console.log(`參數          SL×${res.params.slMult} TP×${res.params.tpMult} Trail×${res.params.trailMult} 確認${res.params.confirmsNeeded}刀 ${res.params.useTrailing ? '追蹤ON' : '追蹤OFF'}`);
}

function printTrades(res) {
  console.log(`\n----- 交易明細 -----`);
  console.log(`進場日       出場日       進場價   出場價   報酬     原因`);
  for (const t of res.trades) {
    console.log(
      `${t.entryTime}  ${t.exitTime}  ${String(t.entryPrice).padStart(7)}  ` +
      `${String(t.exitPrice).padStart(7)}  ${pct(t.retPct).padStart(7)}  ${t.reason}`
    );
  }
}

async function main() {
  const a = parseArgs(process.argv);

  // ── crypto 模式：node bt.js --crypto BTC_USDT [--tf 1D] [--bars 300] [--strategy 3k] ──
  if (a.crypto) {
    const { getCryptoCandles } = require('./fetch_pionex');
    const tf = a.tf || '1D';
    const bars = a.bars ? +a.bars : 300;
    const candles = await getCryptoCandles(a.crypto, tf, bars);
    if (candles.length < 60) { console.error(`資料不足（${candles.length} 根）`); process.exit(1); }
    const p = buildParams(a);
    // crypto 成本模型：taker 0.05%、無證交稅（可被 --capital 等覆寫）
    if (a.capital == null) p.capital = 10000;
    p.feeRate = a.fee != null ? +a.fee : 0.0005;
    p.taxRate = 0;
    p.fractional = true;  // crypto 可買小數單位
    const res = BT.run(candles, p);
    if (a.json) { console.log(JSON.stringify(res.stats)); return; }
    printReport(`${a.crypto} ${tf}`, res);
    if (a.showTrades) printTrades(res);
    return;
  }

  if (!a.id) { console.error('用法：node bt.js --id 2330 [--years 2] [--trades]　或　--crypto BTC_USDT [--tf 1D]'); process.exit(1); }
  const years = a.years ? +a.years : 2;
  const candles = await getCandles(a.id, { years });
  if (candles.length < 60) { console.error(`資料不足（${candles.length} 根），無法回測`); process.exit(1); }
  const res = BT.run(candles, buildParams(a));
  if (a.json) { console.log(JSON.stringify(res.stats)); return; }
  printReport(a.id, res);
  if (a.showTrades) printTrades(res);
}

main().catch(e => { console.error('ERR', e.message); process.exit(1); });
