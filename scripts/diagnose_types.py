"""
diagnose_types.py
抓 2330 的財報資料，印出所有 type 名稱和範例值，
用來確認 FinMind 實際欄位名稱。
"""
import os, json, requests
from datetime import datetime, timedelta

TOKEN   = os.environ.get("FINMIND_TOKEN", "")
BASE    = "https://api.finmindtrade.com/api/v4/data"
HEADERS = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}
START   = (datetime.now() - timedelta(days=548)).strftime("%Y-%m-%d")

print("=== TaiwanStockFinancialStatements (2330) ===")
r = requests.get(BASE, params={
    "dataset":    "TaiwanStockFinancialStatements",
    "data_id":    "2330",
    "start_date": START
}, headers=HEADERS, timeout=30)
r.raise_for_status()
rows = r.json().get("data", [])
print(f"共 {len(rows)} 筆\n")

# 印出所有 type，並附上最新一筆的值
from collections import defaultdict
by_date = defaultdict(dict)
for row in rows:
    by_date[row["date"]][row.get("type","")] = row.get("value", "")

latest_date = sorted(by_date.keys())[-1] if by_date else None
if latest_date:
    print(f"最新季度：{latest_date}")
    for t, v in sorted(by_date[latest_date].items()):
        print(f"  type={t!r:40s}  value={v}")
