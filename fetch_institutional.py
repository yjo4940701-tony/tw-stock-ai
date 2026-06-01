"""
每日抓取全市場三大法人買賣超資料
輸出：data/institutional.json
"""
import requests
import json
import os
import time
from datetime import datetime, timedelta

TOKEN  = os.environ.get('FINMIND_TOKEN', '')
OUTPUT = 'data/institutional.json'

INST_MAP = {
    '外資': '外資',
    '外資及陸資': '外資',
    '外資及陸資(不含外資自營商)': '外資',
    '外資自營商': '外資',   # 外資自營商也歸入外資合計
    '投信': '投信',
    '自營商': '自營商',
    '自營商(自行買賣)': '自營商',
    '自營商(避險)': '自營商',
}

def fetch_day(date_str):
    """抓單日全市場三大法人資料"""
    params = {
        'dataset':    'TaiwanStockInstitutionalInvestorsBuySell',
        'start_date': date_str,
        'end_date':   date_str,
    }
    if TOKEN:
        params['token'] = TOKEN

    try:
        r = requests.get(
            'https://api.finmindtrade.com/api/v4/data',
            params=params, timeout=30
        )
        d = r.json()
        if d.get('status') == 200 and d.get('data'):
            return d['data']
        print(f'  [{date_str}] FinMind 回應: {d.get("msg", d.get("detail", "無資料"))}')
        return []
    except Exception as e:
        print(f'  [{date_str}] 失敗: {e}')
        return []

def main():
    print(f'開始抓取三大法人資料 (token: {"有" if TOKEN else "無"})')

    # 往回 45 個日曆天，確保拿到 30 個交易日
    today = datetime.now()
    dates_to_try = [
        (today - timedelta(days=i)).strftime('%Y-%m-%d')
        for i in range(45)
    ]

    # { stock_id: { date: { '外資': X, '投信': X, '自營商': X } } }
    stocks     = {}
    valid_dates = []

    for date_str in dates_to_try:
        if len(valid_dates) >= 30:
            break

        rows = fetch_day(date_str)
        if not rows:
            continue

        valid_dates.append(date_str)
        print(f'  {date_str}: {len(rows)} 筆')

        for row in rows:
            sid  = str(row.get('stock_id', ''))
            name = row.get('name', '')
            cat  = INST_MAP.get(name)
            if not sid or not cat:
                continue

            # FinMind 單位為「股」，除以 1000 換算為「張」
            net = round(
                (row.get('buy_minus_sell') or
                 (row.get('buy', 0) - row.get('sell', 0))) / 1000
            )

            if sid not in stocks:
                stocks[sid] = {}
            if date_str not in stocks[sid]:
                stocks[sid][date_str] = {'外資': 0, '投信': 0, '自營商': 0}
            stocks[sid][date_str][cat] += net

        time.sleep(2)   # 避免 rate limit

    if not valid_dates:
        print('錯誤：沒有抓到任何資料，中止。')
        return

    # 組成精簡格式：{ dates: [...], stocks: { sid: [[外資,投信,自營商], ...] } }
    output = {
        'updated': today.strftime('%Y-%m-%d'),
        'dates':   valid_dates,   # 最多 30 個交易日，index 0 = 最新
        'stocks':  {}
    }

    for sid, date_map in stocks.items():
        arr = []
        for date_str in valid_dates:
            d = date_map.get(date_str, {})
            arr.append([d.get('外資', 0), d.get('投信', 0), d.get('自營商', 0)])
        output['stocks'][sid] = arr

    os.makedirs('data', exist_ok=True)
    with open(OUTPUT, 'w', encoding='utf-8') as f:
        # 不縮排，最小化檔案大小
        json.dump(output, f, ensure_ascii=False, separators=(',', ':'))

    size_kb = os.path.getsize(OUTPUT) / 1024
    print(f'完成！{len(stocks)} 檔 × {len(valid_dates)} 交易日，'
          f'檔案大小 {size_kb:.0f} KB')

if __name__ == '__main__':
    main()
