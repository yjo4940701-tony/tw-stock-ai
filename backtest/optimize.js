#!/usr/bin/env node
/**
 * optimize.js — 參數網格最佳化（單股 / 幣，三刀流 / 三國）
 *
 * 用法：
 *   node optimize.js --id 2330 [--years 2] [--top 10] [--by totalReturn] [--fast]
 *   node optimize.js --id 2330 --strategy 3k --years 3   # 三國（掃 240/60/20MA + ATR保護）
 *   node optimize.js --crypto BTC_USDT [--tf 1D] [--bars 600]   # 幣（派網公開行情）
 *
 *   # 樣本外驗證（Walk-forward / Out-of-Sample）— 避免「參數調得漂亮、換段就失靈」：
 *   node optimize.js --id 2330 --years 3 --wf [--split 0.7]
 *   node optimize.js --crypto BTC_USDT --tf 4H --bars 1000 --wf
 *
 * --strategy 3k  三國策略（預設三刀流）
 * --by  排序指標：totalReturn（預設）| sharpe | profitFactor | winRate
 * --fast 粗網格（快）；不加則細網格（慢，組合數較多）
 * --minTrades N  過濾交易數太少的組合（預設 5，避免樣本太小的假象）
 * --wf   開啟樣本外驗證：前段(訓練)挑參數 → 後段(沒看過)實測，並列前/後段績效
 * --split R  訓練段比例（預設 0.7 = 前 70% 訓練、後 30% 驗證）
 *
 * 資料只抓一次、反覆套用（避免重複打 FinMind / 派網）。
 */
const BT = require('./engine');

function parseArgs(argv) {
  const a = {};
  for (let i = 2; i < argv.length; i++) {
    const k = argv[i];
    if (k === '--fast') { a.fast = true; continue; }
    if (k === '--wf') { a.wf = true; continue; }
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
  const isCrypto = !!a.crypto;
  if (!a.id && !isCrypto) {
    console.error('用法：node optimize.js --id 2330 [--fast] [--wf]　或　--crypto BTC_USDT [--tf 1D] [--wf]');
    process.exit(1);
  }
  const years = a.years ? +a.years : 2;
  const top = a.top ? +a.top : 10;
  const by = a.by || 'totalReturn';
  const minTrades = a.minTrades != null ? +a.minTrades : 5;

  const sv = String(a.strategy || '').toLowerCase();
  const is3k = (sv === '3k' || sv === 'kingdoms' || sv === 'threekingdoms' || sv === '三國');

  // ── 取資料（台股 FinMind / 幣 派網），只抓一次 ──
  let candles, label, tf;
  if (isCrypto) {
    const { getCryptoCandles } = require('./fetch_pionex');
    tf = a.tf || '1D';
    const bars = Math.min(500, a.bars ? +a.bars : 500); // 派網單次上限 500 根
    candles = await getCryptoCandles(a.crypto, tf, bars);
    label = `${a.crypto} ${tf}`;
  } else {
    const { getCandles } = require('./fetch_tw');
    candles = await getCandles(a.id, { years });
    label = String(a.id);
  }
  if (candles.length < 60) { console.error(`資料不足（${candles.length} 根）`); process.exit(1); }

  // 幣的成本模型（台股用 engine 預設）
  const baseParams = isCrypto ? { feeRate: 0.0005, taxRate: 0, fractional: true, capital: 10000 } : {};
  function mergeParams(combo) {
    return Object.assign({}, baseParams, is3k ? { strategy: 'threeKingdoms' } : {}, combo);
  }
  // 引擎暖機根數（要跟 engine.js warmup 對齊，OOS 取 lead-in 用）
  function warmupOf(combo) {
    return is3k ? ((combo.lbPeriod || 240) + 2) : 36; // 3刀: max(emaSlow21,rsi14,atr14,macdSlow26+signal9)+1=36
  }

  const grid = is3k ? (a.fast ? GRID_3K_FAST : GRID_3K_FULL) : (a.fast ? GRID_FAST : GRID_FULL);
  const stratName = is3k ? '三國 240/60/20MA' : '三刀流';

  // 在指定 candle 段上跑整張網格，回傳依 by 排序、過濾 minTrades 後的結果
  function optimizeOn(cs) {
    const out = [];
    let count = 0;
    for (const combo of combos(grid)) {
      const res = BT.run(cs, mergeParams(combo));
      count++;
      const s = res.stats;
      if (s.numTrades < minTrades) continue;
      const sortVal = s[by] === Infinity ? 1e9 : s[by];
      out.push({ p: combo, s, sortVal });
    }
    out.sort((x, y) => y.sortVal - x.sortVal);
    return { results: out, count };
  }

  function comboCells(p) {
    return is3k
      ? `${String(p.lbPeriod).padStart(3)}  ${String(p.gyPeriod).padStart(3)}  ${String(p.zfPeriod).padStart(3)}  ` +
        `${p.allowBounce ? '是' : '否'}   ${p.useAtrRisk ? '開' : '關'} `
      : `${String(p.slMult).padStart(3)}  ${String(p.tpMult).padStart(3)}  ${String(p.trailMult).padStart(4)}  ` +
        `${p.confirmsNeeded}刀  ${String(p.rsiLow).padStart(4)} `;
  }
  const comboHdr = is3k ? '劉備 關羽 張飛 反彈 ATR保護' : 'SL   TP   Trail 確認 rsiLow';

  // ══════════════ 樣本外驗證（Walk-forward） ══════════════
  if (a.wf) {
    const split = a.split != null ? +a.split : 0.7;
    if (!(split > 0.3 && split < 0.9)) { console.error('--split 需介於 0.3~0.9'); process.exit(1); }
    const n = candles.length;
    const splitIdx = Math.floor(n * split);
    const isCs = candles.slice(0, splitIdx);          // 前段（訓練）
    const oosCs = candles.slice(splitIdx);            // 後段（驗證，純窗口）

    // 前段最低門檻：3k 需 lbPeriod 暖機 + 一些交易空間
    const isMinNeed = is3k ? 280 : 80;
    if (isCs.length < isMinNeed) {
      console.error(`前段僅 ${isCs.length} 根，不足以最佳化（需 ≥ ${isMinNeed}）。請拉長期間：台股 --years 3、幣 --bars 更大。`);
      process.exit(1);
    }
    if (oosCs.length < 30) {
      console.error(`後段僅 ${oosCs.length} 根，樣本太小。請拉長期間或調低 --split。`);
      process.exit(1);
    }

    const { results: isResults, count } = optimizeOn(isCs);
    if (!isResults.length) {
      console.error(`前段沒有任何組合通過 minTrades<${minTrades}，無法驗證。試 --minTrades 3 或拉長期間。`);
      process.exit(1);
    }

    // 取前段前 N 名，各自拿到「沒看過的後段」實測
    const showN = Math.min(top, isResults.length);
    const rows = [];
    let oosWarmInsufficient = false;
    for (let i = 0; i < showN; i++) {
      const { p, s: isS } = isResults[i];
      // 後段測試：往前借 warmup 根當暖機（指標熱身，交易從後段起點才開始）
      const w = warmupOf(p);
      const oosStart = splitIdx - w;
      if (oosStart < 0) oosWarmInsufficient = true;
      const oosEvalCs = candles.slice(Math.max(0, oosStart));
      const oosS = BT.run(oosEvalCs, mergeParams(p)).stats;
      rows.push({ p, isS, oosS });
    }

    // 後段 Buy & Hold 對照（純後段窗口）
    const oosBH = (oosCs[oosCs.length - 1].close / oosCs[0].close - 1) * 100;
    const isBH = (isCs[isCs.length - 1].close / isCs[0].close - 1) * 100;

    console.log(`\n===== ${label} 樣本外驗證（Walk-forward）｜${stratName}（${a.fast ? '粗' : '細'}網格 ${count} 組，依 ${by} 挑） =====`);
    console.log(`資料 ${n} 根　切點 ${(split * 100).toFixed(0)}%：前段(訓練) ${candles[0].time} ~ ${candles[splitIdx - 1].time}（${isCs.length} 根）｜後段(驗證) ${candles[splitIdx].time} ~ ${candles[n - 1].time}（${oosCs.length} 根）`);
    console.log(`前段挑出前 ${showN} 名，各自在「沒看過的後段」實測：\n`);

    console.log(`排名  ${comboHdr} │  前段報酬  後段報酬   落差 │ 前Sharpe 後Sharpe │ 後段交易 後段MaxDD`);
    for (let i = 0; i < rows.length; i++) {
      const { p, isS, oosS } = rows[i];
      const gap = oosS.totalReturn - isS.totalReturn;
      console.log(
        `${String(i + 1).padStart(3)}  ${comboCells(p)} │ ` +
        `${pct(isS.totalReturn).padStart(8)}  ${pct(oosS.totalReturn).padStart(8)}  ${pct(gap).padStart(7)} │ ` +
        `${isS.sharpe.toFixed(2).padStart(6)}  ${oosS.sharpe.toFixed(2).padStart(6)} │ ` +
        `${String(oosS.numTrades).padStart(6)}  ${oosS.maxDrawdown.toFixed(1).padStart(6)}%`
      );
    }

    console.log(`\n前段 Buy&Hold：${pct(isBH)}　｜　後段 Buy&Hold：${pct(oosBH)}`);
    if (oosWarmInsufficient) {
      console.log(`⚠ 部分組合前段不足以提供完整暖機（lead-in），其後段結果可能偏樂觀，建議拉長期間。`);
    }
    console.log(
      `\n📖 怎麼看：\n` +
      `・「前段」是拿來挑參數的舊歷史；「後段」是這組參數「沒看過」的新時間，才是真考驗。\n` +
      `・後段報酬與前段差不多（或更好）、落差小 → 參數較穩健、比較可信。\n` +
      `・後段報酬大幅縮水或由正轉負（落差很大的負值）→ 多半是前段剛好調得漂亮的僥倖（過度配適），別採用。\n` +
      `・整排看：若多數參數後段都崩 → 這策略/週期在這檔本來就不穩，換策略或週期。\n` +
      `・對照後段 Buy&Hold：策略後段贏不過單純買進持有，代表這套主動操作在後段沒加值。`
    );
    return;
  }

  // ══════════════ 一般最佳化（原行為） ══════════════
  if (is3k && candles.length < 270) {
    console.error(`三國需 240MA 暖機，資料僅 ${candles.length} 根，建議 --years 3 或 --bars 更大`); process.exit(1);
  }
  const { results, count } = optimizeOn(candles);

  console.log(`\n===== ${label} 參數最佳化｜${stratName}（${a.fast ? '粗' : '細'}網格 ${count} 組，依 ${by} 排序）=====`);
  console.log(`期間 ${candles[0].time} ~ ${candles[candles.length - 1].time}　過濾 minTrades<${minTrades}　符合 ${results.length} 組\n`);

  console.log(`排名  ${comboHdr} │ 總報酬   交易 勝率   PF    Sharpe MaxDD`);
  for (let i = 0; i < Math.min(top, results.length); i++) {
    const { p, s } = results[i];
    console.log(
      `${String(i + 1).padStart(3)}  ${comboCells(p)} │ ` +
      `${pct(s.totalReturn).padStart(8)}  ${String(s.numTrades).padStart(3)}  ` +
      `${s.winRate.toFixed(0).padStart(3)}%  ${(s.profitFactor === Infinity ? '∞' : s.profitFactor.toFixed(2)).padStart(5)}  ` +
      `${s.sharpe.toFixed(2).padStart(5)}  ${s.maxDrawdown.toFixed(1)}%`
    );
  }
  if (results.length) {
    const buyHold = BT.run(candles, mergeParams(is3k ? { lbPeriod: 240, gyPeriod: 60, zfPeriod: 20 } : {})).stats.buyHold;
    console.log(`\nBuy & Hold 對照：${pct(buyHold)}`);
  }
}

main().catch(e => { console.error('ERR', e.message); process.exit(1); });
