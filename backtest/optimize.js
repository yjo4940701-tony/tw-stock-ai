#!/usr/bin/env node
/**
 * optimize.js — 三刀流參數網格最佳化（單股）
 *
 * 用法：
 *   node optimize.js --id 2330 [--years 2] [--top 10] [--by totalReturn] [--fast]
 *
 * --by  排序指標：totalReturn（預設）| sharpe | profitFactor | winRate
 * --fast 粗網格（快）；不加則細網格（慢，組合數較多）
 * --minTrades N  過濾交易數太少的組合（預設 5，避免樣本太小的假象）
 *
 * 資料只抓一次、反覆套用（避免重複打 FinMind）。
 */
const BT = require('./engine');
const { getCandles } = require('./fetch_tw');

function parseArgs(argv) {
  const a = {};
  for (let i = 2; i < argv.length; i++) {
    const k = argv[i];
    if (k === '--fast') { a.fast = true; continue; }
    if (k.startsWith('--')) a[k.slice(2)] = argv[++i];
  }
  return a;
}

const GRID_FULL = {
  slMult: [1.5, 2.0, 2.5, 3.0],
  tpMult: [2.0, 3.0, 4.0, 5.0],
  trailMult: [2.0, 2.5, 3.0, 3.5],
  confirmsNeeded: [2, 3],
  rsiLow: [45, 50, 55]
};
const GRID_FAST = {
  slMult: [2.0, 3.0],
  tpMult: [3.0, 5.0],
  trailMult: [2.5, 3.5],
  confirmsNeeded: [2, 3],
  rsiLow: [50]
};

function* combos(grid) {
  const keys = Object.keys(grid);
  const idx = keys.map(() => 0);
  while (true) {
    const p = {};
    keys.forEach((k, i) => p[k] = grid[k][idx[i]]);
    yield p;
    let pos = keys.length - 1;
    while (pos >= 0) {
      idx[pos]++;
      if (idx[pos] < grid[keys[pos]].length) break;
      idx[pos] = 0; pos--;
    }
    if (pos < 0) break;
  }
}

function pct(x) { return (x >= 0 ? '+' : '') + x.toFixed(2) + '%'; }

async function main() {
  const a = parseArgs(process.argv);
  if (!a.id) { console.error('用法：node optimize.js --id 2330 [--fast] [--top 10] [--by sharpe]'); process.exit(1); }
  const years = a.years ? +a.years : 2;
  const top = a.top ? +a.top : 10;
  const by = a.by || 'totalReturn';
  const minTrades = a.minTrades != null ? +a.minTrades : 5;

  const candles = await getCandles(a.id, { years });
  if (candles.length < 60) { console.error(`資料不足（${candles.length} 根）`); process.exit(1); }

  const grid = a.fast ? GRID_FAST : GRID_FULL;
  const results = [];
  let count = 0;
  for (const p of combos(grid)) {
    const res = BT.run(candles, p);
    count++;
    const s = res.stats;
    if (s.numTrades < minTrades) continue;
    const sortVal = s[by] === Infinity ? 1e9 : s[by];
    results.push({ p, s, sortVal });
  }
  results.sort((x, y) => y.sortVal - x.sortVal);

  console.log(`\n===== ${a.id} 參數最佳化（${a.fast ? '粗' : '細'}網格 ${count} 組，依 ${by} 排序）=====`);
  console.log(`期間 ${candles[0].time} ~ ${candles[candles.length - 1].time}　過濾 minTrades<${minTrades}　符合 ${results.length} 組\n`);
  console.log('排名  SL   TP   Trail 確認 rsiLow │ 總報酬   交易 勝率   PF    Sharpe MaxDD');
  for (let i = 0; i < Math.min(top, results.length); i++) {
    const { p, s } = results[i];
    console.log(
      `${String(i + 1).padStart(3)}  ` +
      `${String(p.slMult).padStart(3)}  ${String(p.tpMult).padStart(3)}  ${String(p.trailMult).padStart(4)}  ` +
      `${p.confirmsNeeded}刀  ${String(p.rsiLow).padStart(4)}  │ ` +
      `${pct(s.totalReturn).padStart(8)}  ${String(s.numTrades).padStart(3)}  ` +
      `${s.winRate.toFixed(0).padStart(3)}%  ${(s.profitFactor === Infinity ? '∞' : s.profitFactor.toFixed(2)).padStart(5)}  ` +
      `${s.sharpe.toFixed(2).padStart(5)}  ${s.maxDrawdown.toFixed(1)}%`
    );
  }
  if (results.length) {
    const buyHold = BT.run(candles, {}).stats.buyHold;
    console.log(`\nBuy & Hold 對照：${pct(buyHold)}`);
  }
}

main().catch(e => { console.error('ERR', e.message); process.exit(1); });
