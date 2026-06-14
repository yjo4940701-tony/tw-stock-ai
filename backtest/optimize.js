#!/usr/bin/env node
/**
 * optimize.js — 參數網格最佳化（單股，三刀流 / 三國）
 *
 * 用法：
 *   node optimize.js --id 2330 [--years 2] [--top 10] [--by totalReturn] [--fast]
 *   node optimize.js --id 2330 --strategy 3k --years 3   # 三國（掃 240/60/20MA + ATR保護）
 *
 * --strategy 3k  三國策略（預設三刀流）
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

// 三國網格（劉備/關羽/張飛 MA + 熊市反彈 + ATR保護層）
const GRID_3K_FULL = {
  lbPeriod: [200, 240],
  gyPeriod: [40, 50, 60],
  zfPeriod: [10, 20],
  allowBounce: [true, false],
  useAtrRisk: [false, true]
};
const GRID_3K_FAST = {
  lbPeriod: [240],
  gyPeriod: [50, 60],
  zfPeriod: [20],
  allowBounce: [true, false],
  useAtrRisk: [false, true]
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

  const sv = String(a.strategy || '').toLowerCase();
  const is3k = (sv === '3k' || sv === 'kingdoms' || sv === 'threekingdoms' || sv === '三國');

  const candles = await getCandles(a.id, { years });
  if (candles.length < 60) { console.error(`資料不足（${candles.length} 根）`); process.exit(1); }
  if (is3k && candles.length < 270) {
    console.error(`三國需 240MA 暖機，資料僅 ${candles.length} 根，建議 --years 3`); process.exit(1);
  }

  const grid = is3k ? (a.fast ? GRID_3K_FAST : GRID_3K_FULL) : (a.fast ? GRID_FAST : GRID_FULL);
  const results = [];
  let count = 0;
  for (const combo of combos(grid)) {
    const p = is3k ? Object.assign({ strategy: 'threeKingdoms' }, combo) : combo;
    const res = BT.run(candles, p);
    count++;
    const s = res.stats;
    if (s.numTrades < minTrades) continue;
    const sortVal = s[by] === Infinity ? 1e9 : s[by];
    results.push({ p, s, sortVal });
  }
  results.sort((x, y) => y.sortVal - x.sortVal);

  const stratName = is3k ? '三國 240/60/20MA' : '三刀流';
  console.log(`\n===== ${a.id} 參數最佳化｜${stratName}（${a.fast ? '粗' : '細'}網格 ${count} 組，依 ${by} 排序）=====`);
  console.log(`期間 ${candles[0].time} ~ ${candles[candles.length - 1].time}　過濾 minTrades<${minTrades}　符合 ${results.length} 組\n`);

  if (is3k) {
    console.log('排名  劉備 關羽 張飛 反彈 ATR保護 │ 總報酬   交易 勝率   PF    Sharpe MaxDD');
    for (let i = 0; i < Math.min(top, results.length); i++) {
      const { p, s } = results[i];
      console.log(
        `${String(i + 1).padStart(3)}  ` +
        `${String(p.lbPeriod).padStart(3)}  ${String(p.gyPeriod).padStart(3)}  ${String(p.zfPeriod).padStart(3)}  ` +
        `${p.allowBounce ? '是' : '否'}   ${p.useAtrRisk ? '開' : '關'}    │ ` +
        `${pct(s.totalReturn).padStart(8)}  ${String(s.numTrades).padStart(3)}  ` +
        `${s.winRate.toFixed(0).padStart(3)}%  ${(s.profitFactor === Infinity ? '∞' : s.profitFactor.toFixed(2)).padStart(5)}  ` +
        `${s.sharpe.toFixed(2).padStart(5)}  ${s.maxDrawdown.toFixed(1)}%`
      );
    }
  } else {
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
  }
  if (results.length) {
    const buyHold = BT.run(candles, {}).stats.buyHold;
    console.log(`\nBuy & Hold 對照：${pct(buyHold)}`);
  }
}

main().catch(e => { console.error('ERR', e.message); process.exit(1); });
