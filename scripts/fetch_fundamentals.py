"""
fetch_fundamentals.py
每週由 GitHub Actions 執行，逐檔抓取財報關鍵指標存成 data/fundamentals.json

策略：
  - TaiwanStockFinancialStatements 必須帶 data_id，一次只能查一檔
  - 每次請求後 sleep 6.5 秒（安全在 600 req/hr 以內）
  - 有 resume 機制：已抓的股票自動跳過，中斷後下次繼續
  - 每 50 檔存一次進度，防止全部遺失
  - 目標：台股主板 4-5 位數字代號，約 1500 檔，首次約 2.5 小時
"""

import os, json, time, requests
from datetime import datetime, timedelta
from collections import defaultdict

TOKEN    = os.environ.get("FINMIND_TOKEN", "")
BASE     = "https://api.finmindtrade.com/api/v4/data"
HEADERS  = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}
SLEEP    = 6.5   # 秒，安全在 600 req/hr 以下
OUT_PATH = "data/fundamentals.json"

# 開始日期：近 6 季（約 18 個月），足夠算 TTM EPS
START = (datetime.now() - timedelta(days=548)).strftime("%Y-%m-%d")

EPS_KEYS = {"EPS", "每股盈餘"}
ROE_KEYS = {"ROE", "股東權益報酬率", "ReturnOnEquity"}
GM_KEYS  = {"GrossMargin", "毛利率", "GrossProfitMargin"}
DR_KEYS  = {"DebtRatio", "負債比率", "LiabilitiesToAssets"}

def find_val(d, keys):
    for k in keys:
        if k in d:
            return float(d[k])
    return None

def pct(v):
    if v is None: return None
    return round(v * 100 if abs(v) <= 1.5 else v, 1)

def save_result(r):
    os.makedirs("data", exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump({"updated": datetime.now().strftime("%Y-%m-%d"), "stocks": r},
                  f, ensure_ascii=False, separators=(",", ":"))

# ── 1. 取得股票清單 ─────────────────────────────────
print("=== 取得股票清單 ===", flush=True)
r = requests.get(BASE, params={"dataset": "TaiwanStockInfo"},
                 headers=HEADERS, timeout=60)
r.raise_for_status()
all_stocks = r.json().get("data", [])
time.sleep(SLEEP)

# 只保留 4-5 位數字代號（主板股票，排除含字母的權證/轉換債）
stock_ids = sorted(set(
    s["stock_id"] for s in all_stocks
    if s.get("stock_id", "").isdigit() and len(s["stock_id"]) in (4, 5)
))
print(f"目標股票數：{len(stock_ids)}", flush=True)

# ── 2. 載入已有資料（resume） ────────────────────────
existing = {}
if os.path.exists(OUT_PATH):
    try:
        with open(OUT_PATH, encoding="utf-8") as f:
            existing = json.load(f).get("stocks", {})
        print(f"Resume：已有 {len(existing)} 檔，跳過", flush=True)
    except Exception:
        pass

result   = dict(existing)
new_count = 0
err_count = 0

# ── 3. 逐檔抓取 ─────────────────────────────────────
for idx, sid in enumerate(stock_ids):
    if sid in result:
        continue  # 已有資料，跳過

    try:
        resp = requests.get(BASE, params={
            "dataset":    "TaiwanStockFinancialStatements",
            "data_id":    sid,
            "start_date": START
        }, headers=HEADERS, timeout=30)

        if resp.status_code == 400:
            time.sleep(SLEEP); continue
        resp.raise_for_status()

        rows = resp.json().get("data", [])

        if not rows:
            result[sid] = {}  # 標記為「無財報」（ETF 等）
            time.sleep(SLEEP); continue

        # 整理成 {date: {type: value}}
        by_date = defaultdict(dict)
        for row in rows:
            by_date[row["date"]][row.get("type", "")] = row.get("value", 0)

        sorted_dates = sorted(by_date.keys(), reverse=True)

        # TTM EPS = 最近 4 季加總
        eps_vals = []
        for d in sorted_dates[:4]:
            v = find_val(by_date[d], EPS_KEYS)
            if v is not None:
                eps_vals.append(v)
        eps_ttm = round(sum(eps_vals), 2) if eps_vals else None

        if eps_ttm is None:
            result[sid] = {}
            time.sleep(SLEEP); continue

        latest = by_date[sorted_dates[0]] if sorted_dates else {}
        entry  = {"eps": eps_ttm}

        roe = pct(find_val(latest, ROE_KEYS))
        gm  = pct(find_val(latest, GM_KEYS))
        dr  = pct(find_val(latest, DR_KEYS))
        if roe is not None: entry["roe"] = roe
        if gm  is not None: entry["gm"]  = gm
        if dr  is not None: entry["dr"]  = dr

        result[sid] = entry
        new_count += 1

    except Exception as e:
        print(f"  [{sid}] 錯誤：{e}", flush=True)
        err_count += 1

    time.sleep(SLEEP)

    # 每 50 檔存一次
    if (idx + 1) % 50 == 0:
        save_result(result)
        valid = sum(1 for v in result.values() if v.get("eps") is not None)
        print(f"  進度：{idx+1}/{len(stock_ids)}，有效財報 {valid} 檔", flush=True)

# ── 4. 最終存檔 ──────────────────────────────────────
save_result(result)
valid_final = sum(1 for v in result.values() if v.get("eps") is not None)
print(f"\n=== 完成 ===")
print(f"總處理：{len(result)} 檔，有效財報：{valid_final} 檔，錯誤：{err_count} 次")
size_kb = os.path.getsize(OUT_PATH) / 1024
print(f"儲存至 {OUT_PATH}（{size_kb:.0f} KB）")
