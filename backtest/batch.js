#!/usr/bin/env node
/**
 * batch.js — 一次回測多檔，彙總排行（本地大量回測）
 *
 * 用法：
 *   node batch.js --ids 2330,2317,2454,2308 [--years 2]
 *   node batch.js --file ids.txt              （每行一個代號）
 *   node batch.js --gist                       （讀公開 Gist 自選股清單）
 *   node batch.js --ids 2330,2317 --by sharpe --csv out.csv
 *   node batch.js --ids 2330,2317 --optimize --fast   （每檔各自最佳化後取最佳）
 *
 * 參數同 bt.js 可覆寫策略（--sl/--tp/--trail/--confirms/--rsiLow）
 * --by   排序：totalReturn（預設）| sharpe | profitFactor | winRate
 * --csv  輸出 CSV 檔
 * FinMind 限額：首次抓取每檔間隔 0.4s；已快取者免打。
 */
const fs = require('fs');
const path = require('path');
const BT = require('./engine');
const { getCandles } = require('./fetch_tw');

const GIST_API = 'https://api.github.com/gists/0b9966cb6fc32b5aeffe4ad7bdc07836';
const GIST_SETTINGS_FILE = 'tw-stock-settings.json';

function parseArgs(argv) {
  const a = {};
  for (let i = 2; i < argv.length; i++) {
    const k = argv[i];
    if (k === '--gist') { a.gist = true; continue; }
    if (k === '--optimize') { a.optimize = true; continue; }
    if (k === '--fast') { a.fast = true; continue; }
    if (k.startsWith('--')) a[k.slice(2)] = argv[++i];
  }
  return a;
}

function buildParams(a) {
  const p = {};
  if (a.sl != null) p.slMult = +a.sl;
  if (a.tp != null) p.tpMult = +a.tp;
  if (a.trail != null) p.trailMult = +a.trail;
  if (a.confirms != null) p.confirmsNeeded = +a.confirms;
  if (a.rsiLow != null) p.rsiLow = +a.rsiLow;
  if (a.capital != null) p.capital = +a.capital;
  return p;
}

async function getIds(a) {
  if (a.ids) return a.ids.split(',').map(s => s.trim()).filter(Boolean);
  if (a.file) return fs.readFileSync(a.file, 'utf8').split(/\r?\n/).map(s => s.trim()).filter(Boolean);
  if (a.gist) {
    const r = await fetch(GIST_API);
    const j = await r.json();
    const settings = JSON.parse(j.files[GIST_SETTINGS_FILE].content);
    // tw_groups 結構：{ active, list:[{name, tickers:[...]}] }；攤平所有群組去重
    const set = new Set();
    const collect = v => {
      if (Array.isArray(v)) v.forEach(collect);
      else if (v && typeof v === 'object') Object.values(v).forEach(collect);
      else if (typeof v === 'string' && /^\d{4,6}[A-Z]?$/.test(v)) set.add(v);
    };
    collect(settings.tw_groups);
    return [...set];
  }
  return null;
}

// 粗網格（與 optimize.js 對齊，給 --optimize 用）
const GRID_FAST = { slMult: [2, 3], tpMult: [3, 5], trailMult: [2.5, 3.5], confirmsNeeded: [2, 3], rsiLow: [50] };
const GRID_FULL = { slMult: [1.5, 2, 2.5, 3], tpMult: [2, 3, 4, 5], trailMult: [2, 2.5, 3, 3.5], confirmsNeeded: [2, 3], rsiLow: [45, 50, 55] };
function* combos(grid) {
  const keys = Object.keys(grid), idx = keys.map(() => 0);
  while (true) {
    const p = {}; keys.forEach((k, i) => p[k] = grid[k][idx[i]]); yield p;
    let pos = keys.length - 1;
    while (pos >= 0) { idx[pos]++; if (idx[pos] < grid[keys[pos]].length) break; idx[pos] = 0; pos--; }
    if (pos < 0) break;
  }
}

function bestParams(candles, by, fast) {
  let best = null;
  for (const p of combos(fast ? GRID_FAST : GRID_FULL)) {
    const s = BT.run(candles, p).stats;
    if (s.numTrades < 5) continue;
    const v = s[by] === Infinity ? 1e9 : s[by];
    if (!best || v > best.v) best = { v, p, s };
  }
  return best;
}

function pct(x) { return (x >= 0 ? '+' : '') + x.toFixed(2) + '%'; }
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function main() {
  const a = parseArgs(process.argv);
  const ids = await getIds(a);
  if (!ids || !ids.length) {
    console.error('用法：node batch.js --ids 2330,2317 | --file ids.txt | --gist'); process.exit(1);
  }
  const years = a.years ? +a.years : 2;
  const by = a.by || 'totalReturn';
  const params = buildParams(a);

  console.log(`回測 ${ids.length} 檔（${years} 年，依 ${by} 排序${a.optimize ? '，每檔各自最佳化' : ''}）...\n`);
  const rows = [];
  for (const id of ids) {
    try {
      const hadCache = fs.existsSync(path.join(__dirname, 'cache', `${id}.json`));
      const candles = await getCandles(id, { years });
      if (!hadCache) await sleep(400); // 首抓節流
      if (candles.length < 60) { console.error(`  ${id} 資料不足，略過`); continue; }
      let s, usedP;
      if (a.optimize) {
        const b = bestParams(candles, by, a.fast);
        if (!b) { console.error(`  ${id} 無有效組合，略過`); continue; }
        s = b.s; usedP = b.p;
      } else {
        const res = BT.run(candles, params); s = res.stats; usedP = res.params;
      }
      rows.push({ id, s, usedP });
      process.stdout.write('.');
    } catch (e) { console.error(`  ${id} ERR ${e.message}`); }
  }
  console.log('\n');

  rows.sort((x, y) => {
    const vx = x.s[by] === Infinity ? 1e9 : x.s[by];
    const vy = y.s[by] === Infinity ? 1e9 : y.s[by];
    return vy - vx;
  });

  console.log('代號    總報酬   交易 勝率   PF    Sharpe MaxDD   B&H' + (a.optimize ? '     最佳參數(SL/TP/Trail/確認/rsi)' : ''));
  for (const { id, s, usedP } of rows) {
    let line =
      `${id.padEnd(6)}  ${pct(s.totalReturn).padStart(8)}  ${String(s.numTrades).padStart(3)}  ` +
      `${s.winRate.toFixed(0).padStart(3)}%  ${(s.profitFactor === Infinity ? '∞' : s.profitFactor.toFixed(2)).padStart(5)}  ` +
      `${s.sharpe.toFixed(2).padStart(5)}  ${s.maxDrawdown.toFixed(1).padStart(5)}%  ${pct(s.buyHold).padStart(8)}`;
    if (a.optimize) line += `   ${usedP.slMult}/${usedP.tpMult}/${usedP.trailMult}/${usedP.confirmsNeeded}/${usedP.rsiLow}`;
    console.log(line);
  }

  if (rows.length) {
    const avg = k => rows.reduce((sum, r) => sum + (r.s[k] === Infinity ? 0 : r.s[k]), 0) / rows.length;
    const beatBH = rows.filter(r => r.s.totalReturn > r.s.buyHold).length;
    console.log(`\n彙總：${rows.length} 檔　平均總報酬 ${pct(avg('totalReturn'))}　平均 Sharpe ${avg('sharpe').toFixed(2)}　平均勝率 ${avg('winRate').toFixed(0)}%　贏過 B&H ${beatBH}/${rows.length} 檔`);
  }

  if (a.csv) {
    const header = 'id,totalReturn,numTrades,winRate,profitFactor,sharpe,maxDrawdown,buyHold' +
      (a.optimize ? ',slMult,tpMult,trailMult,confirms,rsiLow' : '') + '\n';
    const body = rows.map(({ id, s, usedP }) => {
      let r = [id, s.totalReturn.toFixed(2), s.numTrades, s.winRate.toFixed(1),
        (s.profitFactor === Infinity ? 999 : s.profitFactor.toFixed(2)),
        s.sharpe.toFixed(2), s.maxDrawdown.toFixed(2), s.buyHold.toFixed(2)];
      if (a.optimize) r = r.concat([usedP.slMult, usedP.tpMult, usedP.trailMult, usedP.confirmsNeeded, usedP.rsiLow]);
      return r.join(',');
    }).join('\n');
    fs.writeFileSync(a.csv, header + body);
    console.log(`已輸出 CSV：${a.csv}`);
  }
}

main().catch(e => { console.error('ERR', e.message); process.exit(1); });
