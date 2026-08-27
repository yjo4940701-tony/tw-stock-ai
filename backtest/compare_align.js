#!/usr/bin/env node
/**
 * compare_align.js — 對照「多頭排列」vs「站上三根」進場，誰績效好
 * 用 custom 策略表達兩種進場條件，其餘（純均線、只做多、出場=條件破壞）完全相同。
 *   A 多頭排列：MA20>MA60 且 MA60>MA240
 *   B 站上三根：price>MA20 且 price>MA60 且 price>MA240（= 現行三國 MAIN_LONG）
 *   C 排列+站上：A 的兩條 + price>MA20
 * 用法：node compare_align.js [--years 3]
 */
const BT = require('./engine');
const { getCandles } = require('./fetch_tw');

const P = { price: { type: 'price' }, ma20: { type: 'ma', period: 20 }, ma60: { type: 'ma', period: 60 }, ma240: { type: 'ma', period: 240 } };
const CONFIGS = {
  'A 多頭排列(20>60>240)': [{ op: '>', left: P.ma20, right: P.ma60 }, { op: '>', left: P.ma60, right: P.ma240 }],
  'B 站上三根(價>20,60,240)': [{ op: '>', left: P.price, right: P.ma20 }, { op: '>', left: P.price, right: P.ma60 }, { op: '>', left: P.price, right: P.ma240 }],
  'C 排列+站上': [{ op: '>', left: P.ma20, right: P.ma60 }, { op: '>', left: P.ma60, right: P.ma240 }, { op: '>', left: P.price, right: P.ma20 }],
};

// 代表性一籃子（跨產業，避開有分割假跳空的 ETF）
const BASKET = ['2330', '2317', '2454', '2308', '2382', '2412', '2882', '2891', '1301', '1216', '2002', '2603', '2609', '3711'];

function pct(x) { return (x >= 0 ? '+' : '') + x.toFixed(2) + '%'; }

async function main() {
  const years = process.argv.includes('--years') ? +process.argv[process.argv.indexOf('--years') + 1] : 3;
  const results = {};
  Object.keys(CONFIGS).forEach(k => results[k] = []);
  const perStock = [];

  for (const id of BASKET) {
    let candles;
    try { candles = await getCandles(id, { years }); }
    catch (e) { console.error(`  ${id} 抓取失敗：${e.message}`); continue; }
    if (!candles || candles.length < 260) { console.error(`  ${id} 資料不足(${candles ? candles.length : 0} 根)`); continue; }
    const row = { id, bh: null };
    for (const [name, longGroup] of Object.entries(CONFIGS)) {
      const res = BT.run(candles, {
        strategy: 'custom', direction: 'long', useAtrRisk: false,
        customLong: longGroup, customShort: [],
      });
      const s = res.stats;
      results[name].push(s);
      row[name] = s;
      row.bh = s.buyHold;
    }
    perStock.push(row);
    console.error(`  ${id} 完成（${candles.length} 根）`);
  }

  // 彙總
  const agg = {};
  for (const name of Object.keys(CONFIGS)) {
    const arr = results[name];
    const avg = f => arr.reduce((a, s) => a + f(s), 0) / arr.length;
    const wins = arr.filter(s => s.totalReturn > s.buyHold).length;
    agg[name] = {
      n: arr.length,
      avgRet: avg(s => s.totalReturn),
      medRet: [...arr.map(s => s.totalReturn)].sort((a, b) => a - b)[Math.floor(arr.length / 2)],
      avgTrades: avg(s => s.numTrades),
      avgWinRate: avg(s => s.winRate),
      avgSharpe: avg(s => s.sharpe),
      avgMaxDD: avg(s => s.maxDrawdown),
      avgPF: avg(s => (s.profitFactor === Infinity ? 0 : s.profitFactor)),
      beatBH: wins,
    };
  }
  const avgBH = perStock.reduce((a, r) => a + r.bh, 0) / perStock.length;

  console.log('\n================ 每檔總報酬對照 ================');
  console.log('代號    B&H        A排列      B站上      C排列+站上');
  for (const r of perStock) {
    console.log(
      `${r.id.padEnd(7)} ${pct(r.bh).padStart(8)}  ` +
      Object.keys(CONFIGS).map(k => (pct(r[k].totalReturn) + `(${r[k].numTrades})`).padStart(11)).join(' ')
    );
  }

  console.log('\n================ 彙總（' + years + '年日K，' + perStock.length + ' 檔）================');
  console.log(`Buy&Hold 平均總報酬：${pct(avgBH)}\n`);
  const cols = ['avgRet', 'medRet', 'avgTrades', 'avgWinRate', 'avgSharpe', 'avgMaxDD', 'avgPF', 'beatBH'];
  const hdr = ['策略', '平均報酬', '中位報酬', '平均筆數', '平均勝率', '平均Sharpe', '平均回撤', '平均PF', '贏B&H檔數'];
  console.log(hdr.map((h, i) => h.padEnd(i === 0 ? 24 : 10)).join(''));
  for (const [name, a] of Object.entries(agg)) {
    console.log(
      name.padEnd(24) +
      pct(a.avgRet).padEnd(10) + pct(a.medRet).padEnd(10) +
      a.avgTrades.toFixed(1).padEnd(10) + (a.avgWinRate.toFixed(1) + '%').padEnd(10) +
      a.avgSharpe.toFixed(2).padEnd(10) + a.avgMaxDD.toFixed(1).padEnd(10) +
      a.avgPF.toFixed(2).padEnd(10) + `${a.beatBH}/${a.n}`
    );
  }
}
main().catch(e => { console.error('ERR', e.stack); process.exit(1); });
