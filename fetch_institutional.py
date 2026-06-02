"""
每日抓取全市場三大法人買賣超資料（TWSE + TPEX 官方 API，免費無需驗證）
輸出：data/institutional.json
"""
import requests
import json
import os
import time
from datetime import datetime, timedelta

OUTPUT = 'data/institutional.json'

def fetch_twse(date_str):
    """抓上市股票當日三大法人（TWSE T86）"""
    d = date_str.replace('-', '')   # YYYYMMDD
    url = f'https://www.twse.com.tw/rwd/zh/fund/T86?date={d}&selectType=ALL&response=json'
    try:
        r = requests.get(url, timeout=20, headers={'User-Agent': 'Mozilla/5.0'})
        j = r.json()
        if j.get('stat') == 'OK' and j.get('data'):
            return j['data']
        # 假日或無資料
        return []
    except Exception as e:
        print(f'  [{date_str}] TWSE 失敗: {e}')
        return []

def fetch_tpex(date_str):
    """抓上櫃股票當日三大法人（TPEX）"""
    dt = datetime.strptime(date_str, '%Y-%m-%d')
    roc = dt.year - 1911
    d_str = f'{roc}/{dt.month:02d}/{dt.day:02d}'
    url = f'https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php?l=zh-tw&d={d_str}&se=EW&s=0,asc,0&_={int(time.time()*1000)}'
    try:
        r = requests.get(url, timeout=20, headers={'User-Agent': 'Mozilla/5.0'})
        j = r.json()
        # TPEX 回應格式：tables[0]['data']
        tables = j.get('tables', [])
        if tables and tables[0].get('data'):
            return tables[0]['data']
        return []
    except Exception as e:
        print(f'  [{date_str}] TPEX 失敗: {e}')
        return []

def parse_num(s):
    """把帶逗號的數字字串轉整數，再除以1000換算為張"""
    try:
        return round(int(str(s).replace(',', '').replace(' ', '') or '0') / 1000)
    except:
        return 0

def already_updated_today():
    """今日有效資料已存在就跳過（防止三個 cron 重複跑）"""
    if not os.path.exists(OUTPUT):
        return False
    try:
        with open(OUTPUT, 'r', encoding='utf-8') as f:
            d = json.load(f)
        today_str = datetime.now().strftime('%Y-%m-%d')
        if d.get('updated') != today_str:
            return False
        dates = d.get('dates', [])
        stocks = d.get('stocks', {})
        if dates and dates[0] == today_str and stocks:
            sample = list(stocks.values())[:20]
            total = sum(abs(v) for row in sample for v in row[0])
            return total > 0   # 有非零資料才算有效
    except:
        pass
    return False

def main():
    if already_updated_today():
        print('今日有效資料已存在，跳過。')
        return
    print('開始抓取三大法人資料（TWSE + TPEX）...')

    # 往回 45 個日曆天，確保取到 30 個交易日
    today = datetime.now()
    dates_to_try = [
        (today - timedelta(days=i)).strftime('%Y-%m-%d')
        for i in range(45)
    ]

    # { stock_id: { date: [外資淨, 投信淨, 自營淨] } }
    stocks      = {}
    valid_dates = []

    for date_str in dates_to_try:
        if len(valid_dates) >= 30:
            break

        # ── 上市（TWSE） ──
        twse_rows = fetch_twse(date_str)
        # ── 上櫃（TPEX） ──
        tpex_rows = fetch_tpex(date_str)

        if not twse_rows and not tpex_rows:
            print(f'  {date_str}: 無資料（假日）')
            time.sleep(0.5)
            continue

        # 若全市場加總都是 0，TWSE 資料尚未發布，跳過此日
        total_abs = sum(abs(parse_num(r[4])) + abs(parse_num(r[10])) + abs(parse_num(r[11]))
                        for r in twse_rows if len(r) >= 12)
        if total_abs == 0 and len(twse_rows) > 100:
            print(f'  {date_str}: TWSE 資料尚未發布（全為 0），跳過')
            time.sleep(0.5)
            continue

        valid_dates.append(date_str)
        count = 0

        # TWSE 欄位：[代號, 名稱, 外陸資買進, 外陸資賣出, 外陸資買賣超, 投信買進, 投信賣出, 投信買賣超, 自營買賣超合計, ...]
        for row in twse_rows:
            if len(row) < 9:
                continue
            sid = str(row[0]).strip()
            if not sid or len(sid) < 4 or len(sid) > 7:
                continue
            if len(row) < 12:
                continue
            foreign = parse_num(row[4])    # 外陸資買賣超（不含外資自營商）
            trust   = parse_num(row[10])   # 投信買賣超
            dealer  = parse_num(row[11])   # 自營商買賣超合計
            if sid not in stocks:
                stocks[sid] = {}
            stocks[sid][date_str] = [foreign, trust, dealer]
            count += 1

        # TPEX 欄位：[代號, 名稱, ..., 外資買賣超[4], ..., 投信買賣超[13], ..., 自營買賣超合計[22], ...]
        for row in tpex_rows:
            if len(row) < 23:
                continue
            sid = str(row[0]).strip()
            if not sid or len(sid) < 4 or len(sid) > 7:
                continue
            foreign = parse_num(row[4])
            trust   = parse_num(row[13])
            dealer  = parse_num(row[22])
            if sid not in stocks:
                stocks[sid] = {}
            stocks[sid][date_str] = [foreign, trust, dealer]
            count += 1

        print(f'  {date_str}: {count} 檔')
        time.sleep(1.5)   # 避免打太快

    if not valid_dates:
        print('錯誤：沒有抓到任何資料，中止。')
        return

    # 組成精簡格式：{ updated, dates: [...], stocks: { sid: [[外資,投信,自營], ...] } }
    output = {
        'updated': today.strftime('%Y-%m-%d'),
        'dates':   valid_dates,
        'stocks':  {}
    }

    for sid, date_map in stocks.items():
        arr = []
        for date_str in valid_dates:
            arr.append(date_map.get(date_str, [0, 0, 0]))
        output['stocks'][sid] = arr

    os.makedirs('data', exist_ok=True)
    with open(OUTPUT, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, separators=(',', ':'))

    size_kb = os.path.getsize(OUTPUT) / 1024
    print(f'完成！{len(stocks)} 檔 × {len(valid_dates)} 交易日，檔案 {size_kb:.0f} KB')

if __name__ == '__main__':
    main()
