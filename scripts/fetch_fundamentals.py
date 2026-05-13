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
from datetime import datetime, timedelta, date
from collections import defaultdict

TODAY = date.today()
REFRESH_DAYS = 80   # 超過 80 天就重抓（季報每 90 天更新）

def is_fresh(entry):
    """回傳 True 代表可以跳過（不需重抓）"""
    if not entry:
        return False
    d_str = entry.get("d")
    if not d_str:
        return False
    try:
        days = (TODAY - datetime.strptime(d_str, "%Y-%m-%d").date()).days
        if entry.get("gm") is not None:
            return days < REFRESH_DAYS     # 有財報：80 天內跳過
        else:
            return days < 30               # 無財報（ETF 等）：30 天內跳過，之後重試
    except Exception:
        return False

TOKEN      = os.environ.get("FINMIND_TOKEN", "")
BASE       = "https://api.finmindtrade.com/api/v4/data"
SLEEP      = 12.0  # 秒，安全在 300 req/hr 匿名上限以內
STOCK_RANGE = os.environ.get("STOCK_RANGE", "")   # e.g. "2000-2999"
# 輸出檔：有 range 時存成分段檔，最後由 merge job 合併
OUT_PATH   = f"data/fundamentals-{STOCK_RANGE}.json" if STOCK_RANGE else "data/fundamentals.json"

# 開始日期：近 6 季（約 18 個月），足夠算 TTM EPS
START = (datetime.now() - timedelta(days=548)).strftime("%Y-%m-%d")

# FinMind TaiwanStockFinancialStatements 實際欄位名稱（從診斷腳本確認）
EPS_KEY     = "EPS"
REVENUE_KEY = "Revenue"
GROSS_KEY   = "GrossProfit"
OP_KEY      = "OperatingIncome"
NET_KEY     = "IncomeAfterTaxes"
EQUITY_KEY  = "EquityAttributableToOwnersOfParent"

def save_result(r):
    os.makedirs("data", exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump({"updated": datetime.now().strftime("%Y-%m-%d"), "stocks": r},
                  f, ensure_ascii=False, separators=(",", ":"))

# ── 1. 取得股票清單 ─────────────────────────────────
print("=== 取得股票清單 ===", flush=True)
r = requests.get(BASE, params={"dataset": "TaiwanStockInfo"}, timeout=60)
r.raise_for_status()
all_stocks = r.json().get("data", [])
time.sleep(SLEEP)

# 只保留 4-5 位數字代號（主板股票，排除含字母的權證/轉換債）
all_ids = sorted(set(
    s["stock_id"] for s in all_stocks
    if s.get("stock_id", "").isdigit() and len(s["stock_id"]) in (4, 5)
))

# 若有 STOCK_RANGE 則只處理該段（格式：start-end，依代號數字篩選）
if STOCK_RANGE and "-" in STOCK_RANGE:
    parts = STOCK_RANGE.split("-")
    lo, hi = int(parts[0]), int(parts[1])
    stock_ids = [s for s in all_ids if lo <= int(s) <= hi]
    print(f"區段 {STOCK_RANGE}：目標 {len(stock_ids)} 檔", flush=True)
else:
    stock_ids = all_ids
    print(f"全範圍：目標 {len(stock_ids)} 檔", flush=True)

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
    if is_fresh(result.get(sid)):
        continue  # 資料還新鮮（< 80 天），跳過

    try:
        resp = requests.get(BASE, params={
            "dataset":    "TaiwanStockFinancialStatements",
            "data_id":    sid,
            "start_date": START
        }, timeout=30)

        if resp.status_code == 400:
            time.sleep(SLEEP); continue
        resp.raise_for_status()

        rows = resp.json().get("data", [])

        if not rows:
            result[sid] = {"d": TODAY.strftime("%Y-%m-%d")}  # 無財報，記日期避免重複抓
            time.sleep(SLEEP); continue

        # 整理成 {date: {type: value}}
        by_date = defaultdict(dict)
        for row in rows:
            by_date[row["date"]][row.get("type", "")] = row.get("value", 0)

        sorted_dates = sorted(by_date.keys(), reverse=True)

        # TTM EPS = 最近 4 季加總
        eps_vals = []
        for d in sorted_dates[:4]:
            v = by_date[d].get(EPS_KEY)
            if v is not None:
                eps_vals.append(float(v))

        if not eps_vals:
            result[sid] = {}
            time.sleep(SLEEP); continue

        entry = {"eps": round(sum(eps_vals), 2)}

        # 最新單季
        latest_date = sorted_dates[0]
        c = by_date[latest_date]

        # 去年同季（找相同月日的前一年資料）
        ld = datetime.strptime(latest_date, "%Y-%m-%d")
        prev_date = f"{ld.year-1}-{ld.month:02d}-{ld.day:02d}"
        p = by_date.get(prev_date, {})

        def safe_ratio(num, den):
            try:
                n, d2 = float(num), float(den)
                if d2 == 0: return None
                v = n / d2 * 100
                return round(v, 1) if -500 < v < 500 else None
            except Exception:
                return None

        revenue = c.get(REVENUE_KEY)
        if revenue:
            gm = safe_ratio(c.get(GROSS_KEY), revenue)
            om = safe_ratio(c.get(OP_KEY),    revenue)
            nm = safe_ratio(c.get(NET_KEY),   revenue)
            if gm is not None: entry["gm"] = gm
            if om is not None: entry["om"] = om
            if nm is not None: entry["nm"] = nm

            # 去年同季毛利率/營業利益率/淨利率 → 算年增（百分點）
            p_rev = p.get(REVENUE_KEY)
            if p_rev:
                gm_p = safe_ratio(p.get(GROSS_KEY), p_rev)
                om_p = safe_ratio(p.get(OP_KEY),    p_rev)
                nm_p = safe_ratio(p.get(NET_KEY),   p_rev)
                if gm is not None and gm_p is not None:
                    entry["gm_yoy"] = round(gm - gm_p, 1)
                if om is not None and om_p is not None:
                    entry["om_yoy"] = round(om - om_p, 1)
                if nm is not None and nm_p is not None:
                    entry["nm_yoy"] = round(nm - nm_p, 1)

        entry["d"] = TODAY.strftime("%Y-%m-%d")
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
