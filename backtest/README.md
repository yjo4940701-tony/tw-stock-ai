# 台股進階回測引擎（backtest/）

共用核心引擎 + Node 命令列工具，本地一次回測大量台股。**兩種策略可切換**：

| 策略 | `--strategy` | 均線 | 邏輯 | 出場 |
|:--|:--|:--|:--|:--|
| **三刀流** threeBlade（預設）| `blade` | EMA 9/21 | RSI 回檔回升 + EMA 交叉 + MACD 柱轉向（三重確認）| ATR 停損停利 + 追蹤止盈 |
| **三國** threeKingdoms | `3k` | SMA 240/60/20 | 劉備240定多空 + 關羽60抓進出 + 張飛20收兵 | 均線轉空訊號（可選加 ATR 保護層）|

> 兩策略移植自 autobots 教學套件（`threeBlade.js` / `strategy_three_kingdoms.py`），改為台股日K、只做多、以股計價。
> 對應計劃書：`100_Todo/plans/2026-06-09-台股進階回測引擎.md`

### 三國策略細節（純均線趨勢，只做多）
- **劉備 240MA**＝多空分水嶺：股價 > 240MA 為牛市
- **關羽 60MA**＝進出節奏；**張飛 20MA**＝收兵
- 進場：**全軍做多**（站上 240+60+20）或 **熊市反彈**（跌破 240 但站上 60，`allowBounce`，可關）
- 出場：轉空（跌破三線）或牛市修正（站上 240 但跌破 60）
- 風控：原版純均線，台股版可加 `--atr-risk` 開 ATR 停損保護層（空頭股建議開）
- ⚠ 需 **240MA 暖機**，至少用 2-3 年資料，否則前段全在暖機沒交易

---

## 檔案

| 檔案 | 用途 |
|:--|:--|
| `engine.js` | 共用回測核心（純 JS、零依賴、UMD：Node + 瀏覽器共用）。內建 RSI/EMA/MACD/ATR 指標 |
| `fetch_tw.js` | FinMind 日K 抓取 + `cache/` 當日 JSON 快取（省 API 限額） |
| `bt.js` | 單股回測 CLI |
| `optimize.js` | 單股參數網格最佳化 |
| `batch.js` | 一次回測多檔，彙總排行（本地大量回測） |

---

## 用法

```bash
# 單股回測（含交易明細）
node bt.js --id 2330 --years 2 --trades

# 單股參數最佳化（細網格 384 組，依總報酬排序）
node optimize.js --id 2330 --by totalReturn --top 10
node optimize.js --id 2330 --fast              # 粗網格（快）
node optimize.js --id 2330 --by sharpe         # 依 Sharpe 排序

# 三國策略（240/60/20MA 趨勢）
node bt.js --id 2330 --strategy 3k --years 2 --trades
node bt.js --id 2330 --strategy 3k --atr-risk           # 加 ATR 停損保護層
node bt.js --id 2330 --strategy 3k --lb 200 --gy 50 --zf 20   # 自訂均線
node batch.js --gist --strategy 3k --years 3 --by sharpe      # 三國掃自選股

# 批次回測多檔
node batch.js --ids 2330,2317,2454 --years 2 --by sharpe
node batch.js --gist                            # 讀 GitHub Gist 自選股清單
node batch.js --file ids.txt                    # 每行一個代號
node batch.js --gist --optimize --fast          # 每檔各自最佳化後取最佳
node batch.js --gist --csv out.csv              # 輸出 CSV
```

### 策略參數（可覆寫引擎預設）

| 旗標 | 說明 | 預設 |
|:--|:--|:--|
| `--sl` | ATR 停損倍數（停損 = 進場價 − sl×ATR） | 2.0 |
| `--tp` | ATR 停利倍數 | 3.0 |
| `--trail` | 追蹤止盈倍數（= 最高價 − trail×ATR） | 2.5 |
| `--confirms` | 三刀流確認數（3=三刀、2=兩刀弱信號） | 3 |
| `--rsiLow` | 回檔門檻（RSI 回落此值之下再回升視為買點） | 50 |
| `--capital` | 本金 | 1,000,000 |
| `--no-trail` | 關閉追蹤止盈 | — |

### 績效指標
總報酬、最終權益、交易次數、勝率、盈虧比 PF、平均獲利/虧損、最大回撤、Sharpe（年化 252）、最長連勝/連敗、出場原因、Buy&Hold 對照。

---

## ⚠️ 三個必讀限制

1. **FinMind `TaiwanStockPrice` 是未還原價**：分割／除權息會造成假跳空。
   例：0050 在 2025 做過分割，回測顯示 B&H −43%、回撤 77% 全是假象。
   **結論**：有大額配息或做過分割的標的（ETF、金融、傳產），回測結果不可信。
   還原價需 FinMind 付費 dataset（`TaiwanStockPriceAdj`），暫不接。

2. **`--optimize` 是樣本內最佳化（in-sample）**：在同一段資料找最佳參數會高估實際表現，
   這是 overfitting。真要信，需 Out-of-Sample / Walk-forward 驗證（之後另做）。

3. **出場用當根收盤近似**：停損/停利/追蹤都以收盤價判斷觸發、收盤價成交，
   不模擬盤中觸價或跳空穿價（台股漲跌停 + 缺口下，盤中觸價回測易過度樂觀）。

---

## 設計要點

- **以股計價（含零股）**：不限整張，任何股價都能回測，跨檔比 % 報酬才公平
  （否則高價股如 2454 聯發科一張 140 萬 > 本金 100 萬會被靜默排除）。
- **台股真實成本**：買賣手續費各 0.1425% + 賣出證交稅 0.3%，現股不開槓桿、只做多。
- **共用引擎**：`engine.js` 用 UMD 寫法，網頁分頁（待補）與此處 Node CLI 共用同一套算法，杜絕邏輯漂移。
- **快取**：`cache/<id>.json` 當日有效，重跑最佳化/批次不重複打 FinMind（限額 300 次/小時）。

## 待補（不在本次範圍）

- 網頁端「進階回測」分頁（計劃書 Step 4）+ 網頁/CLI 一致性驗證（Step 5）
- 還原價資料源、Out-of-Sample 驗證、分K 回測
