#!/usr/bin/env node
/**
 * compare_align_crypto.js — crypto 版「多頭排列」vs「站上三根」對照
 * 同 compare_align.js 邏輯，改吃派網日K + crypto 成本模型（taker0.05%/無稅/可買小數/本金10000）
 *   A 多頭排列：MA20>MA60 且 MA60>MA240
 *   B 站上三根：price>MA20 且 price>MA60 且 price>MA240
 *   C 排列+站上
 * 用法：node compare_align_crypto.js [--tf 1D] [--bars 500]
 * ⚠ 派網單次上限 500 根；日K 500 根扣 240MA 暖機後約 260 根可交易（約 8-9 個月）。
 */
const BT = require('./engine');
const { getCryptoCandles } = require('./fetch_pionex');

// --ma a,b,c 可覆寫均線組（預設 20/60/240）；讓 240MA 對 crypto 500 根資料太長時可縮短測試
const maArg = process.argv.includes('--ma') ? process.argv[process.argv.indexOf('--ma') + 1].split(',').map(Number) : [20, 60, 240];
const [SHORT, MID, LONG] = maArg;
const P = { price: { type: 'price' }, s: { type: 'ma', period: SHORT }, m: { type: 'ma', period: MID }, l: { type: 'ma', period: LONG } };
const CONFIGS = {
  [`A 多頭排列(${SHORT}>${MID}>${LONG})`]: [{ op: '>', left: P.s, right: P.m }, { op: '>', left: P.m, right: P.l }],
  [`B 站上三根(價>${SHORT},${MID},${LONG})`]: [{ op: '>', left: P.price, right: P.s }, { op: '>', left: P.price, right: P.m }, { op: '>', left: P.price, right: P.l }],
  'C 排列+站上': [{ op: '>', left: P.s, right: P.m }, { op: '>', left: P.m, right: P.l }, { op: '>', left: P.price, right: P.s }],
};

const BASKET = ['BTC_USDT', 'ETH_USDT', 'SOL_USDT', 'BNB_USDT', 'XRP_USDT', 'DOGE_USDT', 'ADA_USDT', 'AVAX_USDT'];

function pct(x) { return (x >= 0 ? '+' : '') + x.toFixed(2) + '%'; }

async function main() {
  const tf = process.argv.includes('--tf') ? process.argv[process.argv.indexOf('--tf') + 1] : '1D';
  const bars = process.argv.includes('--bars') ? +process.argv[process.argv.indexOf('--bars') + 1] : 500;
  const results = {}; Object.keys(CONFIGS).forEach(k => results[k] = []);
  const perStock = [];

  for (const sym of BASKET) {
    let candles;
    try { candles = await getCryptoCandles(sym, tf, bars); }
    catch (e) { console.error(`  ${sym} 抓取失敗：${e.message}`); continue; }
    if (!candles || candles.length < 260) { console.error(`  ${sym} 資料不足(${candles ? candles.length : 0} 根)`); continue; }
    const row = { id: sym.replace('_USDT', ''), bh: null };
    for (const [name, longGroup] of Object.entries(CONFIGS)) {
      const res = BT.run(candles, {
        strategy: 'custom', direction: 'long', useAtrRisk: false,
        customLong: longGroup, customShort: [],
        capital: 10000, feeRate: 0.0005, taxRate: 0, fractional: true,
      });
      results[name].push(res.stats); row[name] = res.stats; row.bh = res.stats.buyHold;
    }
    perStock.push(row);
    console.error(`  ${sym} 完成（${candles.length} 根，${candles[0].time}→${candles[candles.length - 1].time}）`);
  }
  if (!perStock.length) { console.error('無資料'); return; }

  const agg = {};
  for (const name of Object.keys(CONFIGS)) {
    const arr = results[name];
    const avg = f => arr.reduce((a, s) => a + f(s), 0) / arr.length;
    agg[name] = {
      n: arr.length, avgRet: avg(s => s.totalReturn),
      medRet: [...arr.map(s => s.totalReturn)].sort((a, b) => a - b)[Math.floor(arr.length / 2)],
      avgTrades: avg(s => s.numTrades), avgWinRate: avg(s => s.winRate),
      avgSharpe: avg(s => s.sharpe), avgMaxDD: avg(s => s.maxDrawdown),
      avgPF: avg(s => (s.profitFactor === Infinity ? 0 : s.profitFactor)),
      beatBH: arr.filter(s => s.totalReturn > s.buyHold).length,
    };
  }
  const avgBH = perStock.reduce((a, r) => a + r.bh, 0) / perStock.length;

  console.log('\n================ 每幣總報酬對照 ================');
  console.log('幣種    B&H        A排列      B站上      C排列+站上');
  for (const r of perStock) {
    console.log(`${r.id.padEnd(7)} ${pct(r.bh).padStart(8)}  ` +
      Object.keys(CONFIGS).map(k => (pct(r[k].totalReturn) + `(${r[k].numTrades})`).padStart(11)).join(' '));
  }

  console.log('\n================ 彙總（' + tf + ' ' + bars + '根，' + perStock.length + ' 幣）================');
  console.log(`Buy&Hold 平均總報酬：${pct(avgBH)}\n`);
  const hdr = ['策略', '平均報酬', '中位報酬', '平均筆數', '平均勝率', '平均Sharpe', '平均回撤', '平均PF', '贏B&H'];
  console.log(hdr.map((h, i) => h.padEnd(i === 0 ? 24 : 10)).join(''));
  for (const [name, a] of Object.entries(agg)) {
    console.log(name.padEnd(24) + pct(a.avgRet).padEnd(10) + pct(a.medRet).padEnd(10) +
      a.avgTrades.toFixed(1).padEnd(10) + (a.avgWinRate.toFixed(1) + '%').padEnd(10) +
      a.avgSharpe.toFixed(2).padEnd(10) + a.avgMaxDD.toFixed(1).padEnd(10) +
      a.avgPF.toFixed(2).padEnd(10) + `${a.beatBH}/${a.n}`);
  }
}
main().catch(e => { console.error('ERR', e.stack); process.exit(1); });
