"""
fetch_revenue.py
每月由 GitHub Actions 執行，逐檔查詢月營收並計算 YoY/MoM。
4 個平行 job 各負責一段代號，匿名查詢不消耗 token 配額。
"""

import os, json, time, requests, sys
from datetime import datetime, timedelta, date
from collections import defaultdict

TODAY       = date.today()
BASE        = "https://api.finmindtrade.com/api/v4/data"
SLEEP       = 12.0
STOCK_RANGE = os.environ.get("STOCK_RANGE", "")
OUT_PATH    = f"data/revenue-{STOCK_RANGE}.json" if STOCK_RANGE else "data/revenue.json"
FUND_PATH   = "data/fundamentals.json"

start = (datetime.now() - timedelta(days=430)).strftime("%Y-%m-%d")

def is_fresh(entry):
    rev_d = entry.get("rev_d")
    if not rev_d:
        return False
    try:
        d = datetime.strptime(rev_d, "%Y-%m-%d").date()
        return d.year == TODAY.year and d.month == TODAY.month
    except Exception:
        return False

if not os.path.exists(FUND_PATH):
    print(f"找不到 {FUND_PATH}，略過")
    sys.exit(0)

with open(FUND_PATH, encoding="utf-8") as f:
    all_ids = sorted(json.load(f).get("stocks", {}).keys())

if STOCK_RANGE and "-" in STOCK_RANGE:
    parts = STOCK_RANGE.split("-")
    lo, hi = int(parts[0]), int(parts[1])
    stock_ids = [s for s in all_ids if s.isdigit() and lo <= int(s) <= hi]
    print(f"區段 {STOCK_RANGE}：目標 {len(stock_ids)} 檔", flush=True)
else:
    stock_ids = all_ids
    print(f"全範圍：目標 {len(stock_ids)} 檔", flush=True)

existing = {}
if os.path.exists(OUT_PATH):
    try:
        with open(OUT_PATH, encoding="utf-8") as f:
            existing = json.load(f)
        print(f"Resume：已有 {len(existing)} 檔", flush=True)
    except Exception:
        pass

result = dict(existing)

def save():
    os.makedirs("data", exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, separators=(",", ":"))

for idx, sid in enumerate(stock_ids):
    if is_fresh(result.get(sid, {})):
        continue

    try:
        resp = requests.get(BASE, params={
            "dataset":    "TaiwanStockMonthRevenue",
            "data_id":    sid,
            "start_date": start
        }, timeout=30)

        if resp.status_code in (400, 404):
            result[sid] = {"rev_d": TODAY.strftime("%Y-%m-%d")}
            time.sleep(SLEEP)
            continue
        resp.raise_for_status()

        rows = resp.json().get("data", [])
        if not rows:
            result[sid] = {"rev_d": TODAY.strftime("%Y-%m-%d")}
            time.sleep(SLEEP)
            continue

        recs = sorted([{
            "y": int(r["revenue_year"]),
            "m": int(r["revenue_month"]),
            "rev": float(r.get("revenue", 0))
        } for r in rows], key=lambda x: (x["y"], x["m"]))

        latest   = recs[-1]
        ly, lm   = latest["y"], latest["m"]
        prev_yoy = next((r for r in recs if r["y"] == ly-1 and r["m"] == lm), None)
        prev_mom = recs[-2] if len(recs) >= 2 else None

        # 單月 YoY / MoM
        yoy = round((latest["rev"] - prev_yoy["rev"]) / prev_yoy["rev"] * 100, 1) if prev_yoy and prev_yoy["rev"] > 0 else None
        mom = round((latest["rev"] - prev_mom["rev"]) / prev_mom["rev"] * 100, 1) if prev_mom and prev_mom["rev"] > 0 else None

        # 累計 YoY（1 月到最新月份，今年 vs 去年）
        cum_this = sum(r["rev"] for r in recs if r["y"] == ly   and r["m"] <= lm)
        cum_prev = sum(r["rev"] for r in recs if r["y"] == ly-1 and r["m"] <= lm)
        cum_yoy  = round((cum_this - cum_prev) / cum_prev * 100, 1) if cum_prev > 0 else None

        result[sid] = {
            "rev_yoy":     yoy,
            "rev_cum_yoy": cum_yoy,
            "rev_mom":     mom,
            "rev_ym":      f"{ly}-{lm:02d}",
            "rev_d":       TODAY.strftime("%Y-%m-%d")
        }

    except Exception as e:
        print(f"  [{sid}] 錯誤：{e}", flush=True)

    time.sleep(SLEEP)

    if (idx + 1) % 50 == 0:
        save()
        print(f"  進度：{idx+1}/{len(stock_ids)}", flush=True)

save()
valid = sum(1 for v in result.values() if v.get("rev_yoy") is not None)
print(f"\n完成：{len(result)} 檔，有 YoY 值：{valid} 檔", flush=True)
