"""
fetch_fundamentals.py
每週由 GitHub Actions 執行，抓取全市場財報關鍵指標存成 data/fundamentals.json

指標：
  - eps_ttm   近 4 季 EPS 加總（TTM）
  - roe       最新季 ROE（股東權益報酬率）
  - gross_m   最新季毛利率（%）
  - op_m      最新季營業利益率（%）
  - net_m     最新季淨利率（%）
  - debt_r    最新季負債比率（%）
"""

import os, json, requests
from datetime import datetime, timedelta
from collections import defaultdict

TOKEN = os.environ.get("FINMIND_TOKEN", "")
BASE  = "https://api.finmindtrade.com/api/v4/data"
HEADERS = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}

def fetch(dataset, start_date):
    print(f"  → {dataset} from {start_date} ...", flush=True)
    r = requests.get(BASE, params={"dataset": dataset, "start_date": start_date},
                     headers=HEADERS, timeout=180)
    r.raise_for_status()
    data = r.json().get("data", [])
    print(f"     {len(data):,} rows", flush=True)
    return data

# 近 6 季（約 18 個月），足夠算 TTM EPS
start = (datetime.now() - timedelta(days=548)).strftime("%Y-%m-%d")

print("=== Fetching TaiwanStockFinancialStatements ===")
fin_rows = fetch("TaiwanStockFinancialStatements", start)

print("=== Fetching TaiwanStockBalanceSheet ===")
bs_rows  = fetch("TaiwanStockBalanceSheet", start)

# ── 印出樣本，確認欄位名稱 ──────────────────────────
if fin_rows:
    sample = fin_rows[:3]
    print("Financial sample:", json.dumps(sample, ensure_ascii=False))
if bs_rows:
    print("Balance sheet sample:", json.dumps(bs_rows[:3], ensure_ascii=False))

# ── 整理損益表 ─────────────────────────────────────
# 可能的欄位名稱（FinMind 有時用英文有時用中文 type）
EPS_TYPES      = {"EPS", "每股盈餘"}
GROSS_TYPES    = {"GrossProfit", "毛利率", "GrossMargin"}
REVENUE_TYPES  = {"Revenue", "營業收入"}
OPERATING_TYPES= {"OperatingIncome", "營業利益", "OperatingMargin", "營業利益率"}
NET_TYPES      = {"NetIncome", "淨利", "NetMargin", "淨利率"}
ROE_TYPES      = {"ROE", "股東權益報酬率"}

# key: (stock_id, date) → {type: value}
fin_map = defaultdict(dict)
for row in fin_rows:
    sid  = row.get("stock_id", "")
    date = row.get("date", "")
    typ  = row.get("type", "")
    val  = row.get("value", None)
    if sid and date and typ and val is not None:
        fin_map[(sid, date)][typ] = float(val)

# 整理負債比率
# TaiwanStockBalanceSheet: type 可能是 "TotalDebt" / "TotalAssets" 或直接 "LiabilitiesToAssets"
DEBT_RATIO_TYPES = {"LiabilitiesToAssets", "負債比率", "DebtRatio"}
TOTAL_ASSETS_T   = {"TotalAssets", "總資產"}
TOTAL_LIAB_T     = {"TotalLiabilities", "總負債"}

bs_map = defaultdict(dict)
for row in bs_rows:
    sid  = row.get("stock_id", "")
    date = row.get("date", "")
    typ  = row.get("type", "")
    val  = row.get("value", None)
    if sid and date and typ and val is not None:
        bs_map[(sid, date)][typ] = float(val)

# ── 彙整 ────────────────────────────────────────────
# 每個股票：找最近 4 季日期
from itertools import groupby

# 取得各股所有季報日期
stock_dates = defaultdict(set)
for (sid, date) in fin_map:
    stock_dates[sid].add(date)

result = {}

def find_val(d, *type_sets):
    for ts in type_sets:
        for t in ts:
            if t in d:
                return d[t]
    return None

for sid, dates in stock_dates.items():
    sorted_dates = sorted(dates, reverse=True)

    # TTM EPS = 最近 4 季 EPS 加總
    eps_vals = []
    for d in sorted_dates:
        v = find_val(fin_map[(sid, d)], EPS_TYPES)
        if v is not None:
            eps_vals.append(v)
        if len(eps_vals) == 4:
            break
    eps_ttm = round(sum(eps_vals), 2) if eps_vals else None

    # 最新季指標
    latest_fin = fin_map.get((sid, sorted_dates[0]), {}) if sorted_dates else {}
    latest_bs  = bs_map.get( (sid, sorted_dates[0]), {}) if sorted_dates else {}
    # balance sheet 可能有不同日期，找最近的
    bs_dates = [d for (s, d) in bs_map if s == sid]
    if bs_dates:
        latest_bs = bs_map[(sid, sorted(bs_dates, reverse=True)[0])]

    gross_m = find_val(latest_fin, GROSS_TYPES)
    op_m    = find_val(latest_fin, OPERATING_TYPES)
    net_m   = find_val(latest_fin, NET_TYPES)
    roe     = find_val(latest_fin, ROE_TYPES)
    debt_r  = find_val(latest_bs,  DEBT_RATIO_TYPES)

    # 如果拿到的是絕對金額而非比率，嘗試計算
    if gross_m is not None and gross_m > 100:
        rev = find_val(latest_fin, REVENUE_TYPES)
        if rev and rev != 0:
            gross_m = round(gross_m / rev * 100, 1)
        else:
            gross_m = None
    elif gross_m is not None:
        gross_m = round(gross_m, 1)

    if op_m is not None and op_m > 100:
        rev = find_val(latest_fin, REVENUE_TYPES)
        if rev and rev != 0:
            op_m = round(op_m / rev * 100, 1)
        else:
            op_m = None
    elif op_m is not None:
        op_m = round(op_m, 1)

    if net_m is not None and net_m > 100:
        rev = find_val(latest_fin, REVENUE_TYPES)
        if rev and rev != 0:
            net_m = round(net_m / rev * 100, 1)
        else:
            net_m = None
    elif net_m is not None:
        net_m = round(net_m, 1)

    if debt_r is not None and debt_r <= 1:
        debt_r = round(debt_r * 100, 1)
    elif debt_r is not None:
        debt_r = round(debt_r, 1)

    if roe is not None and roe <= 1:
        roe = round(roe * 100, 1)
    elif roe is not None:
        roe = round(roe, 1)

    # 只存有值的
    entry = {}
    if eps_ttm is not None: entry["eps"] = eps_ttm
    if roe     is not None: entry["roe"] = roe
    if gross_m is not None: entry["gm"]  = gross_m
    if op_m    is not None: entry["om"]  = op_m
    if net_m   is not None: entry["nm"]  = net_m
    if debt_r  is not None: entry["dr"]  = debt_r

    if entry:
        result[sid] = entry

print(f"\n=== 共整理 {len(result):,} 檔 ===")

# ── 存檔 ────────────────────────────────────────────
os.makedirs("data", exist_ok=True)
output = {
    "updated": datetime.now().strftime("%Y-%m-%d"),
    "stocks": result
}
with open("data/fundamentals.json", "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, separators=(",", ":"))

size_kb = os.path.getsize("data/fundamentals.json") / 1024
print(f"Saved data/fundamentals.json ({size_kb:.0f} KB)")
