/**
 * 台股進階回測共用引擎 engine.js
 * - 純 JavaScript、零外部依賴
 * - UMD：Node (module.exports) 與 瀏覽器 (window.BTEngine) 共用
 * - 策略：三刀流（RSI + EMA 交叉 + MACD 柱狀轉向，三重確認，只做多）｜三國（240/60/20MA，可做多/做空/兩者）
 * - 風控：ATR 動態停損停利 + 追蹤止盈（多空方向鏡射）
 * - 成本：台股現股真實費率（買賣手續費 0.1425% + 賣出證交稅 0.3%）；crypto 呼叫端另傳 taker費率、無稅
 * - 不開槓桿：多單/空單部位上限皆 = 可用現金（無保證金放大、無強平模擬、無永續資金費率）
 * - direction 預設 'long'（向下相容，不傳這個參數的舊呼叫行為完全不變）
 *
 * 輸入 candles: [{time, open, high, low, close, volume?}]（time 為 "YYYY-MM-DD" 字串）
 * 輸出 result: { params, trades[], equity[], stats{} }
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.BTEngine = factory();
  }
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  // ---------- 指標計算 ----------

  // EMA：回傳與輸入等長陣列，前期用 SMA 暖機
  function ema(values, period) {
    const out = new Array(values.length).fill(null);
    if (values.length < period) return out;
    const k = 2 / (period + 1);
    let sma = 0;
    for (let i = 0; i < period; i++) sma += values[i];
    sma /= period;
    out[period - 1] = sma;
    for (let i = period; i < values.length; i++) {
      out[i] = values[i] * k + out[i - 1] * (1 - k);
    }
    return out;
  }

  // RSI（Wilder 平滑）
  function rsi(closes, period) {
    const out = new Array(closes.length).fill(null);
    if (closes.length <= period) return out;
    let gain = 0, loss = 0;
    for (let i = 1; i <= period; i++) {
      const d = closes[i] - closes[i - 1];
      if (d >= 0) gain += d; else loss -= d;
    }
    let avgGain = gain / period, avgLoss = loss / period;
    out[period] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
    for (let i = period + 1; i < closes.length; i++) {
      const d = closes[i] - closes[i - 1];
      const g = d > 0 ? d : 0, l = d < 0 ? -d : 0;
      avgGain = (avgGain * (period - 1) + g) / period;
      avgLoss = (avgLoss * (period - 1) + l) / period;
      out[i] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
    }
    return out;
  }

  // MACD 柱狀圖（hist = macd - signal）
  function macdHist(closes, fast, slow, signal) {
    const emaFast = ema(closes, fast);
    const emaSlow = ema(closes, slow);
    const macdLine = closes.map((_, i) =>
      (emaFast[i] != null && emaSlow[i] != null) ? emaFast[i] - emaSlow[i] : null);
    // signal 線：對 macdLine 有效段做 EMA
    const firstValid = macdLine.findIndex(v => v != null);
    const sigOut = new Array(closes.length).fill(null);
    if (firstValid >= 0) {
      const seg = macdLine.slice(firstValid).map(v => v == null ? 0 : v);
      const sig = ema(seg, signal);
      for (let i = 0; i < sig.length; i++) {
        if (sig[i] != null) sigOut[firstValid + i] = sig[i];
      }
    }
    return closes.map((_, i) =>
      (macdLine[i] != null && sigOut[i] != null) ? macdLine[i] - sigOut[i] : null);
  }

  // ATR（Wilder 平滑）
  function atr(highs, lows, closes, period) {
    const out = new Array(closes.length).fill(null);
    if (closes.length <= period) return out;
    const tr = new Array(closes.length).fill(null);
    tr[0] = highs[0] - lows[0];
    for (let i = 1; i < closes.length; i++) {
      tr[i] = Math.max(
        highs[i] - lows[i],
        Math.abs(highs[i] - closes[i - 1]),
        Math.abs(lows[i] - closes[i - 1])
      );
    }
    let sum = 0;
    for (let i = 1; i <= period; i++) sum += tr[i];
    out[period] = sum / period;
    for (let i = period + 1; i < closes.length; i++) {
      out[i] = (out[i - 1] * (period - 1) + tr[i]) / period;
    }
    return out;
  }

  // SMA：簡單移動平均（三國策略用 240/60/20MA）
  function sma(values, period) {
    const out = new Array(values.length).fill(null);
    let sum = 0;
    for (let i = 0; i < values.length; i++) {
      sum += values[i];
      if (i >= period) sum -= values[i - period];
      if (i >= period - 1) out[i] = sum / period;
    }
    return out;
  }

  // ---------- 預設參數 ----------
  const DEFAULTS = {
    strategy: 'threeBlade', // 'threeBlade'（三刀流）| 'threeKingdoms'（三國 240/60/20MA）
    // --- 三刀流（threeBlade）---
    rsiPeriod: 14, rsiLow: 50, rsiHigh: 65,  // rsiLow = 回檔門檻（多頭中 RSI 回落到此之下再回升視為買點）
    emaFast: 9, emaSlow: 21,
    macdFast: 12, macdSlow: 26, macdSignal: 9,
    confirmsNeeded: 3,  // 三刀流=3、兩刀（弱信號）=2
    setupLookback: 5,   // RSI 須在近 N 根內曾低於 rsiLow
    // --- 三國（threeKingdoms）---
    lbPeriod: 240,      // 劉備：方向（牛熊分水嶺）
    gyPeriod: 60,       // 關羽：進出場節奏
    zfPeriod: 20,       // 張飛：收兵/風控
    slopeBars: 3,       // 張飛斜率計算根數（保留，原版收兵出場停用）
    allowBounce: true,      // 是否做熊市反彈（跌破240MA但站上60MA → 短多）
    allowCorrection: true,  // 是否對稱做牛市修正空單（站上240MA但跌破60MA → 短空，僅 direction 含 short 時生效）
    // --- 方向（三國適用；三刀流只做多，shortEntrySignal 恆 false）---
    direction: 'long',  // 'long' | 'short' | 'both'
    // --- 風控（兩策略共用，ATR 動態停損停利 + 追蹤止盈；做空時停損停利方向鏡射）---
    atrPeriod: 14,
    slMult: 2.0,        // 停損 = 進場價 - slMult * ATR
    tpMult: 3.0,        // 停利 = 進場價 + tpMult * ATR
    trailMult: 2.5,     // 追蹤止盈 = 最高價 - trailMult * ATR
    useTrailing: true,
    useAtrRisk: true,   // 是否啟用 ATR 停損停利層（三國預設改 false 走純均線，見 run()）
    // --- 共用 ---
    capital: 1000000,   // 本金
    feeRate: 0.001425,  // 手續費（買賣各一次）
    taxRate: 0.003,     // 證交稅（賣出）
    fractional: false   // 是否允許小數單位（台股整股=false；crypto=true，可買 0.001 顆）
  };

  // 合併使用者參數與預設（含三國純均線預設）
  function resolveParams(userParams) {
    const p = Object.assign({}, DEFAULTS, userParams || {});
    // 三國預設走純均線（不啟用 ATR 風控層），除非使用者明確指定 useAtrRisk
    if (p.strategy === 'threeKingdoms' &&
        (userParams == null || userParams.useAtrRisk === undefined)) {
      p.useAtrRisk = false;
    }
    return p;
  }

  // 建立各策略的進場/出場/狀態訊號（只做多，台股現股不開槓桿）；run() 與 lastSignal() 共用
  // 回傳 entrySignal(i)=新進場(rising edge)、exitSignal(i)=出場、activeSignal(i)=目前處於做多條件、warmup
  function buildSignals(closes, p) {
    if (p.strategy === 'threeKingdoms') {
      // 三國：劉備240方向 / 關羽60進出 / 張飛20收兵
      const lb = sma(closes, p.lbPeriod);
      const gy = sma(closes, p.gyPeriod);
      const zf = sma(closes, p.zfPeriod);
      function tkSig(i) {
        const pr = closes[i], L = lb[i], G = gy[i], Z = zf[i];
        if (L == null || G == null || Z == null) return 'HOLD';
        const bull = pr > L, bear = pr < L, aboveGy = pr > G, belowGy = pr < G;
        if (bull && aboveGy && pr > Z) return 'MAIN_LONG';        // 全軍做多
        if (bear && belowGy && pr < Z) return 'MAIN_SHORT';       // 全軍做空
        if (bear && aboveGy) return 'BOUNCE_LONG';                // 熊市反彈（短多）
        if (bull && belowGy) return 'CORRECTION_SHORT';           // 牛市修正
        return 'HOLD';
      }
      const openLong = i => {
        const s = tkSig(i);
        return s === 'MAIN_LONG' || (p.allowBounce && s === 'BOUNCE_LONG');
      };
      const closeLong = i => {
        const s = tkSig(i);
        return s === 'MAIN_SHORT' || s === 'CORRECTION_SHORT';    // 轉空 / 牛市修正 → 收兵
      };
      const openShort = i => {
        const s = tkSig(i);
        return s === 'MAIN_SHORT' || (p.allowCorrection && s === 'CORRECTION_SHORT');  // 全軍做空，可對稱納入牛市修正
      };
      const closeShort = i => {
        const s = tkSig(i);
        return s === 'MAIN_LONG' || s === 'BOUNCE_LONG';           // 轉多 / 熊市反彈 → 回補（對稱於 closeLong）
      };
      return {
        entrySignal: i => openLong(i) && !openLong(i - 1),         // rising edge
        exitSignal: i => closeLong(i),
        activeSignal: i => openLong(i),                            // 目前處於做多區
        shortEntrySignal: i => openShort(i) && !openShort(i - 1),  // 做空 rising edge
        shortActiveSignal: i => openShort(i),                      // 目前處於做空區
        exitShortSignal: i => closeShort(i),                       // 空單出場（回補）
        warmup: Math.max(p.lbPeriod + 1, p.atrPeriod) + 1
      };
    }
    // 三刀流：EMA交叉 + RSI回檔回升 + MACD柱轉向（三重確認）
    const rsiArr = rsi(closes, p.rsiPeriod);
    const emaF = ema(closes, p.emaFast);
    const emaS = ema(closes, p.emaSlow);
    const hist = macdHist(closes, p.macdFast, p.macdSlow, p.macdSignal);
    function score(i) {
      if (i < 1) return 0;
      if (emaF[i] == null || emaS[i] == null || rsiArr[i] == null ||
          rsiArr[i - 1] == null || hist[i] == null || hist[i - 1] == null) return 0;
      const emaBull = emaF[i] > emaS[i] ? 1 : 0;
      let dipped = false;
      for (let j = Math.max(1, i - p.setupLookback); j <= i; j++) {
        if (rsiArr[j] != null && rsiArr[j] < p.rsiLow) { dipped = true; break; }
      }
      const rsiConf = (dipped && rsiArr[i] > rsiArr[i - 1]) ? 1 : 0;
      const macdConf = hist[i] > hist[i - 1] ? 1 : 0;
      return emaBull + rsiConf + macdConf;
    }
    return {
      entrySignal: i => score(i) >= p.confirmsNeeded && score(i - 1) < p.confirmsNeeded,
      exitSignal: i => emaF[i] != null && emaS[i] != null && emaF[i] < emaS[i],
      activeSignal: i => score(i) >= p.confirmsNeeded,             // 目前達確認門檻
      shortEntrySignal: i => false,                               // 三刀流做空不在本次範圍
      shortActiveSignal: i => false,
      exitShortSignal: i => false,
      warmup: Math.max(p.emaSlow, p.rsiPeriod, p.atrPeriod, p.macdSlow + p.macdSignal) + 1
    };
  }

  // 最新一根的訊號狀態（給訊號掃描用）
  function lastSignal(candles, userParams) {
    const p = resolveParams(userParams);
    const closes = candles.map(c => c.close);
    const sig = buildSignals(closes, p);
    const i = closes.length - 1;
    if (i < sig.warmup) return { strategy: p.strategy, ready: false, fresh: false, active: false, freshShort: false, activeShort: false, price: closes[i] };
    return {
      strategy: p.strategy, ready: true,
      fresh: sig.entrySignal(i),     // 最新一根剛出現買進訊號
      active: sig.activeSignal(i),   // 最新一根處於做多條件
      freshShort: sig.shortEntrySignal ? sig.shortEntrySignal(i) : false,  // 最新一根剛出現做空訊號
      activeShort: sig.shortActiveSignal ? sig.shortActiveSignal(i) : false, // 最新一根處於做空條件
      price: closes[i], time: candles[i].time
    };
  }

  // ---------- 回測主體 ----------
  function run(candles, userParams) {
    const p = resolveParams(userParams);
    const n = candles.length;
    const closes = candles.map(c => c.close);
    const highs = candles.map(c => c.high);
    const lows = candles.map(c => c.low);

    const atrArr = atr(highs, lows, closes, p.atrPeriod);

    // 進出場訊號（run 與 lastSignal 共用同一套）
    const sig = buildSignals(closes, p);
    const entrySignal = sig.entrySignal, exitSignal = sig.exitSignal, warmup = sig.warmup;
    const shortEntrySignal = sig.shortEntrySignal || (() => false);
    const exitShortSignal = sig.exitShortSignal || (() => false);
    const allowLong = p.direction !== 'short';
    const allowShort = p.direction !== 'long';

    const trades = [];
    const equity = []; // [{time, equity}]
    let cash = p.capital;
    let pos = null; // {dir:'long'|'short', shares, entryPrice, entryTime, entryATR, trailHigh/trailLow, cost/entryValue}

    for (let i = 0; i < n; i++) {
      const c = candles[i];
      if (i >= warmup) {
        if (!pos) {
          if (allowLong && entrySignal(i)) {
            const price = c.close;
            // 以股計價：台股整股 floor、crypto 允許小數單位
            const raw = cash / (price * (1 + p.feeRate));
            const shares = p.fractional ? raw : Math.floor(raw);
            if (shares > 0) {
              const cost = price * shares * (1 + p.feeRate);
              cash -= cost;
              pos = {
                dir: 'long', shares, entryPrice: price, entryTime: c.time,
                entryATR: atrArr[i] || 0, trailHigh: price, cost
              };
            }
          } else if (allowShort && shortEntrySignal(i)) {
            // 做空建倉＝賣出（不開槓桿，部位額度比照做多用等額現金）；比照現股賣出課稅
            const price = c.close;
            const raw = cash / (price * (1 + p.feeRate));
            const shares = p.fractional ? raw : Math.floor(raw);
            if (shares > 0) {
              const entryValue = price * shares * (1 - p.feeRate - p.taxRate);
              cash += entryValue;
              pos = {
                dir: 'short', shares, entryPrice: price, entryTime: c.time,
                entryATR: atrArr[i] || 0, trailLow: price, entryValue
              };
            }
          }
        } else if (pos.dir === 'long') {
          // 持倉中：先看 ATR 風控層（若啟用），再看策略出場訊號
          if (c.close > pos.trailHigh) pos.trailHigh = c.close;
          let exit = null;
          if (p.useAtrRisk && pos.entryATR > 0 && atrArr[i] != null) {
            const slPrice = pos.entryPrice - p.slMult * pos.entryATR;
            const tpPrice = pos.entryPrice + p.tpMult * pos.entryATR;
            const trailPrice = p.useTrailing ? pos.trailHigh - p.trailMult * atrArr[i] : -Infinity;
            if (c.close <= slPrice) exit = 'SL';
            else if (c.close >= tpPrice) exit = 'TP';
            else if (p.useTrailing && c.close <= trailPrice && trailPrice > slPrice) exit = 'TRAIL';
          }
          if (!exit && exitSignal(i)) exit = 'SIGNAL';
          if (exit) closePosition(c.close, c.time, exit);
        } else {
          // 做空持倉：ATR 風控方向鏡射（漲破停損、跌破停利、追蹤停損由最低價往上抬）
          if (c.close < pos.trailLow) pos.trailLow = c.close;
          let exit = null;
          if (p.useAtrRisk && pos.entryATR > 0 && atrArr[i] != null) {
            const slPrice = pos.entryPrice + p.slMult * pos.entryATR;
            const tpPrice = pos.entryPrice - p.tpMult * pos.entryATR;
            const trailPrice = p.useTrailing ? pos.trailLow + p.trailMult * atrArr[i] : Infinity;
            if (c.close >= slPrice) exit = 'SL';
            else if (c.close <= tpPrice) exit = 'TP';
            else if (p.useTrailing && c.close >= trailPrice && trailPrice < slPrice) exit = 'TRAIL';
          }
          if (!exit && exitShortSignal(i)) exit = 'SIGNAL';
          if (exit) closePosition(c.close, c.time, exit);
        }
      }
      // 逐根結算權益（mark-to-market；空單為負債，市值反向計）
      const mv = pos ? (pos.dir === 'short' ? -pos.shares * c.close : pos.shares * c.close) : 0;
      equity.push({ time: c.time, equity: cash + mv });
    }
    // 收尾平倉
    if (pos) closePosition(candles[n - 1].close, candles[n - 1].time, 'EOD');

    function closePosition(price, time, reason) {
      let pnl;
      if (pos.dir === 'short') {
        const cost = price * pos.shares * (1 + p.feeRate); // 回補＝買進，不課證交稅
        cash -= cost;
        pnl = pos.entryValue - cost;
        trades.push({
          dir: 'short', entryTime: pos.entryTime, exitTime: time,
          entryPrice: pos.entryPrice, exitPrice: price,
          shares: pos.shares, pnl, retPct: pnl / (pos.entryPrice * pos.shares) * 100, reason
        });
      } else {
        const proceeds = price * pos.shares * (1 - p.feeRate - p.taxRate);
        cash += proceeds;
        pnl = proceeds - pos.cost;
        trades.push({
          dir: 'long', entryTime: pos.entryTime, exitTime: time,
          entryPrice: pos.entryPrice, exitPrice: price,
          shares: pos.shares, pnl, retPct: pnl / pos.cost * 100, reason
        });
      }
      pos = null;
    }

    return { params: p, trades, equity, stats: computeStats(trades, equity, candles, p) };
  }

  // ---------- 績效報表 ----------
  function computeStats(trades, equity, candles, p) {
    const finalEq = equity.length ? equity[equity.length - 1].equity : p.capital;
    const totalReturn = (finalEq / p.capital - 1) * 100;

    const wins = trades.filter(t => t.pnl > 0);
    const losses = trades.filter(t => t.pnl <= 0);
    const grossWin = wins.reduce((s, t) => s + t.pnl, 0);
    const grossLoss = Math.abs(losses.reduce((s, t) => s + t.pnl, 0));
    const winRate = trades.length ? wins.length / trades.length * 100 : 0;
    const profitFactor = grossLoss === 0 ? (grossWin > 0 ? Infinity : 0) : grossWin / grossLoss;
    const avgWin = wins.length ? grossWin / wins.length : 0;
    const avgLoss = losses.length ? -grossLoss / losses.length : 0;

    // 最大回撤（以權益曲線計）
    let peak = -Infinity, maxDD = 0;
    for (const e of equity) {
      if (e.equity > peak) peak = e.equity;
      const dd = (peak - e.equity) / peak;
      if (dd > maxDD) maxDD = dd;
    }

    // Sharpe（日報酬，年化 252）
    const rets = [];
    for (let i = 1; i < equity.length; i++) {
      if (equity[i - 1].equity > 0) rets.push(equity[i].equity / equity[i - 1].equity - 1);
    }
    const mean = rets.length ? rets.reduce((a, b) => a + b, 0) / rets.length : 0;
    const variance = rets.length ? rets.reduce((a, b) => a + (b - mean) ** 2, 0) / rets.length : 0;
    const std = Math.sqrt(variance);
    const sharpe = std === 0 ? 0 : mean / std * Math.sqrt(252);

    // 連勝/連敗
    let curW = 0, curL = 0, maxW = 0, maxL = 0;
    for (const t of trades) {
      if (t.pnl > 0) { curW++; curL = 0; if (curW > maxW) maxW = curW; }
      else { curL++; curW = 0; if (curL > maxL) maxL = curL; }
    }

    // 出場原因統計
    const reasons = {};
    for (const t of trades) reasons[t.reason] = (reasons[t.reason] || 0) + 1;

    // Buy & Hold 對照（同本金、首根買進尾根賣出，含成本）
    let buyHold = 0;
    if (candles.length > 1) {
      const p0 = candles[0].close, pN = candles[candles.length - 1].close;
      const rawBH = p.capital / (p0 * (1 + p.feeRate));
      const shares = p.fractional ? rawBH : Math.floor(rawBH);
      if (shares > 0) {
        const cost = p0 * shares * (1 + p.feeRate);
        const proceeds = pN * shares * (1 - p.feeRate - p.taxRate);
        buyHold = (p.capital - cost + proceeds) / p.capital * 100 - 100;
      }
    }

    return {
      totalReturn, finalEquity: finalEq, numTrades: trades.length,
      winRate, profitFactor, avgWin, avgLoss,
      maxDrawdown: maxDD * 100, sharpe,
      maxConsecWins: maxW, maxConsecLosses: maxL,
      exitReasons: reasons, buyHold,
      bars: candles.length,
      period: candles.length ? `${candles[0].time} ~ ${candles[candles.length - 1].time}` : ''
    };
  }

  return { run, lastSignal, DEFAULTS, ema, rsi, macdHist, atr, sma };
});
