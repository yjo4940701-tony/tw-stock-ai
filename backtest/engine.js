/**
 * 台股進階回測共用引擎 engine.js
 * - 純 JavaScript、零外部依賴
 * - UMD：Node (module.exports) 與 瀏覽器 (window.BTEngine) 共用
 * - 策略：三刀流（RSI + EMA 交叉 + MACD 柱狀轉向，三重確認）
 * - 風控：ATR 動態停損停利 + 追蹤止盈
 * - 成本：台股現股真實費率（買賣手續費 0.1425% + 賣出證交稅 0.3%）
 * - 現股不開槓桿、只做多（long-only），部位上限 = 本金
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

  // ---------- 預設參數 ----------
  const DEFAULTS = {
    rsiPeriod: 14, rsiLow: 50, rsiHigh: 65,  // rsiLow = 回檔門檻（多頭中 RSI 回落到此之下再回升視為買點）
    emaFast: 9, emaSlow: 21,
    macdFast: 12, macdSlow: 26, macdSignal: 9,
    atrPeriod: 14,
    slMult: 2.0,        // 停損 = 進場價 - slMult * ATR
    tpMult: 3.0,        // 停利 = 進場價 + tpMult * ATR
    trailMult: 2.5,     // 追蹤止盈 = 最高價 - trailMult * ATR
    useTrailing: true,
    confirmsNeeded: 3,  // 三刀流=3、兩刀（弱信號）=2
    setupLookback: 5,   // RSI 須在近 N 根內曾低於 rsiLow
    capital: 1000000,   // 本金
    feeRate: 0.001425,  // 手續費（買賣各一次）
    taxRate: 0.003      // 證交稅（賣出）
  };

  // ---------- 回測主體 ----------
  function run(candles, userParams) {
    const p = Object.assign({}, DEFAULTS, userParams || {});
    const n = candles.length;
    const closes = candles.map(c => c.close);
    const highs = candles.map(c => c.high);
    const lows = candles.map(c => c.low);

    const rsiArr = rsi(closes, p.rsiPeriod);
    const emaF = ema(closes, p.emaFast);
    const emaS = ema(closes, p.emaSlow);
    const hist = macdHist(closes, p.macdFast, p.macdSlow, p.macdSignal);
    const atrArr = atr(highs, lows, closes, p.atrPeriod);

    // 進場信號（rising edge）
    function score(i) {
      if (i < 1) return 0;
      if (emaF[i] == null || emaS[i] == null || rsiArr[i] == null ||
          rsiArr[i - 1] == null || hist[i] == null || hist[i - 1] == null) return 0;
      const emaBull = emaF[i] > emaS[i] ? 1 : 0;
      // RSI：近 setupLookback 根曾跌破 rsiLow 且當前回升
      let dipped = false;
      for (let j = Math.max(1, i - p.setupLookback); j <= i; j++) {
        if (rsiArr[j] != null && rsiArr[j] < p.rsiLow) { dipped = true; break; }
      }
      const rsiConf = (dipped && rsiArr[i] > rsiArr[i - 1]) ? 1 : 0;
      const macdConf = hist[i] > hist[i - 1] ? 1 : 0;
      return emaBull + rsiConf + macdConf;
    }

    const warmup = Math.max(p.emaSlow, p.rsiPeriod, p.atrPeriod, p.macdSlow + p.macdSignal) + 1;

    const trades = [];
    const equity = []; // [{time, equity}]
    let cash = p.capital;
    let pos = null; // {shares, entryPrice, entryTime, entryATR, trailHigh, cost}

    for (let i = 0; i < n; i++) {
      const c = candles[i];
      if (i >= warmup && atrArr[i] != null) {
        if (!pos) {
          // 進場：rising edge + 達確認門檻 + ATR 有效
          if (score(i) >= p.confirmsNeeded && score(i - 1) < p.confirmsNeeded) {
            const price = c.close;
            // 以股計價（含零股）：任何股價都能回測，跨檔報酬率比較才公平
            const shares = Math.floor(cash / (price * (1 + p.feeRate)));
            if (shares >= 1) {
              const cost = price * shares * (1 + p.feeRate);
              cash -= cost;
              pos = {
                shares, entryPrice: price, entryTime: c.time,
                entryATR: atrArr[i], trailHigh: price, cost
              };
            }
          }
        } else {
          // 持倉中：更新追蹤高點、判斷出場
          if (c.close > pos.trailHigh) pos.trailHigh = c.close;
          const slPrice = pos.entryPrice - p.slMult * pos.entryATR;
          const tpPrice = pos.entryPrice + p.tpMult * pos.entryATR;
          const trailPrice = p.useTrailing ? pos.trailHigh - p.trailMult * atrArr[i] : -Infinity;
          let exit = null;
          if (c.close <= slPrice) exit = 'SL';
          else if (c.close >= tpPrice) exit = 'TP';
          else if (p.useTrailing && c.close <= trailPrice && trailPrice > slPrice) exit = 'TRAIL';
          else if (emaF[i] != null && emaS[i] != null && emaF[i] < emaS[i]) exit = 'SIGNAL';
          if (exit) closePosition(c.close, c.time, exit);
        }
      }
      // 逐根結算權益（mark-to-market）
      const mv = pos ? pos.shares * c.close : 0;
      equity.push({ time: c.time, equity: cash + mv });
    }
    // 收尾平倉
    if (pos) closePosition(candles[n - 1].close, candles[n - 1].time, 'EOD');

    function closePosition(price, time, reason) {
      const proceeds = price * pos.shares * (1 - p.feeRate - p.taxRate);
      cash += proceeds;
      const pnl = proceeds - pos.cost;
      trades.push({
        entryTime: pos.entryTime, exitTime: time,
        entryPrice: pos.entryPrice, exitPrice: price,
        shares: pos.shares, pnl, retPct: pnl / pos.cost * 100, reason
      });
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
      const shares = Math.floor(p.capital / (p0 * (1 + p.feeRate)));
      if (shares >= 1) {
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

  return { run, DEFAULTS, ema, rsi, macdHist, atr };
});
