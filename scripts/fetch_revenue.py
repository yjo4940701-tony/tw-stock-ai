"""
fetch_revenue.py
在 merge job 裡執行，bulk 抓全市場月營收並計算 YoY，
更新進 data/fundamentals.json。

月營收每月 10 日前更新，每次 merge 都執行一次，
確保 fundamentals.json 裡的月營收資料是最新的。
"""

import os, json, requests, sys
from datetime import datetime, timedelta, date
from collections import defaultdict

TOKEN   = os.environ.get("FINMIND_TOKEN", "")
BASE    = "https://api.finmindtrade.com/api/v4/data"
HEADERS = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}
TODAY   = date.today()
FUND_PATH = "data/fundamentals.json"

# 抓近 14 個月，足夠算 YoY
start = (datetime.now() - timedelta(days=430)).strftime("%Y-%m-%d")

print(f"=== Fetching TaiwanStockMonthRevenue (bulk, from {start}) ===", flush=True)

r = requests.get(BASE, params={
    "dataset":    "TaiwanStockMonthRevenue",
    "start_date": start
}, headers=HEADERS, timeout=180)

if r.status_code == 400:
    print("400 error：bulk query 不支援，月營收略過")
    sys.exit(0)

r.raise_for_status()
rows = r.json().get("data", [])
print(f"共 {len(rows):,} 筆", flush=True)

if not rows:
    print("無資料，略過")
    sys.exit(0)

# ── 整理成 {stock_id: [{y, m, rev}, ...]} ────────────────
by_stock = defaultdict(list)
for row in rows:
    by_stock[row["stock_id"]].append({
        "y":   int(row["revenue_year"]),
        "m":   int(row["revenue_month"]),
        "rev": float(row.get("revenue", 0))
    })

# ── 計算各股最新月 YoY ────────────────────────────────────
rev_map = {}
for sid, recs in by_stock.items():
    recs.sort(key=lambda x: (x["y"], x["m"]))
    latest = recs[-1]
    prev = next(
        (r for r in recs if r["y"] == latest["y"]-1 and r["m"] == latest["m"]),
        None
    )
    yoy = None
    if prev and prev["rev"] > 0:
        yoy = round((latest["rev"] - prev["rev"]) / prev["rev"] * 100, 1)

    # MoM（上個月）
    mom = None
    if len(recs) >= 2:
        prev_m = recs[-2]
        if prev_m["rev"] > 0:
            mom = round((latest["rev"] - prev_m["rev"]) / prev_m["rev"] * 100, 1)

    rev_map[sid] = {
        "rev_yoy": yoy,
        "rev_mom": mom,
        "rev_ym":  f"{latest['y']}-{latest['m']:02d}",
        "rev_d":   TODAY.strftime("%Y-%m-%d")
    }

print(f"計算 YoY/MoM 完成，共 {len(rev_map):,} 檔", flush=True)

# ── 更新 fundamentals.json ────────────────────────────────
if not os.path.exists(FUND_PATH):
    print(f"找不到 {FUND_PATH}，略過")
    sys.exit(0)

with open(FUND_PATH, encoding="utf-8") as f:
    fund = json.load(f)

stocks = fund.get("stocks", {})
updated = 0
for sid, rv in rev_map.items():
    if sid in stocks:
        stocks[sid].update(rv)
    else:
        stocks[sid] = rv
    updated += 1

fund["rev_updated"] = TODAY.strftime("%Y-%m-%d")
fund["stocks"] = stocks

with open(FUND_PATH, "w", encoding="utf-8") as f:
    json.dump(fund, f, ensure_ascii=False, separators=(",", ":"))

size_kb = os.path.getsize(FUND_PATH) / 1024
print(f"更新 {updated:,} 檔，存至 {FUND_PATH}（{size_kb:.0f} KB）")
