"""
每日抓取全市場三大法人買賣超資料（TWSE + TPEX 官方 API，免費無需驗證）
輸出：data/institutional.json

增量合併設計（2026-06-11）：
- 現有檔案裡健康的日期直接沿用，每天只補抓缺少的日期（通常 1-2 天）
- TWSE 會間歇性封鎖 GitHub Actions IP，失敗重試 3 次；連續失敗該日跳過，
  留給下一個 cron 或隔天補抓，絕不用空資料覆蓋歷史

自選股保證機制（2026-06-11）：
- T86/TPEX 都是「一次請求回整個市場某一天」，無法逐檔抓，只能逐日抓
- 寫檔前針對使用者自選股（讀公開 Gist）做保證檢查：任何一天若自選股
  在 TWSE 全為 0（代表那天上市資料沒抓成功），對該日加強重試（5 次）；
  仍失敗就把該日整個剔除，寧可少一天也不讓自選股顯示錯誤的 0
"""
import requests
import json
import os
import time
from datetime import datetime, timedelta

OUTPUT = 'data/institutional.json'
WATCHLIST_GIST = '0b9966cb6fc32b5aeffe4ad7bdc07836'   # 跨裝置同步用的公開 Gist

def fetch_watchlist():
    """讀公開 Gist 取得使用者所有自選股代號（跨群組合併）。失敗回空集合。"""
    try:
        r = requests.get(f'https://api.github.com/gists/{WATCHLIST_GIST}', timeout=20)
        files = r.json().get('files', {})
        f = files.get('tw-stock-settings.json') or next(iter(files.values()), None)
        if not f:
            return set()
        cfg = json.loads(f.get('content') or '{}')
        groups = (cfg.get('tw_groups') or {}).get('list', [])
        wl = set()
        for g in groups:
            for t in g.get('tickers', []):
                wl.add(str(t).strip())
        print(f'自選股清單：{len(wl)} 檔 {sorted(wl)}')
        return wl
    except Exception as e:
        print(f'讀取自選股清單失敗（不影響全市場抓取）: {e}')
        return set()

def notify_tg(message):
    """資料異常時推 Telegram。沒設 TG Secret 就靜默跳過（本地測試不會發）。"""
    token = os.environ.get('TG_BOT_TOKEN', '')
    chat  = os.environ.get('TG_CHAT_ID', '')
    if not token or not chat:
        print(f'（未設 TG Secret，略過通知）{message}')
        return
    try:
        r = requests.post(
            f'https://api.telegram.org/bot{token}/sendMessage',
            json={'chat_id': chat, 'text': message, 'parse_mode': 'HTML'},
            timeout=10
        )
        if r.status_code == 200:
            print('已推送 TG 異常通知')
        else:
            print(f'TG 通知失敗: {r.text}')
    except Exception as e:
        print(f'TG 通知失敗: {e}')

def fetch_twse(date_str, retries=3):
    """抓上市股票當日三大法人（TWSE T86）
    回傳：list = 成功（空 list 為假日）；None = 連續失敗（被擋）"""
    d = date_str.replace('-', '')   # YYYYMMDD
    url = f'https://www.twse.com.tw/rwd/zh/fund/T86?date={d}&selectType=ALL&response=json'
    for i in range(retries):
        try:
            r = requests.get(url, timeout=20, headers={'User-Agent': 'Mozilla/5.0'})
            j = r.json()
            if j.get('stat') == 'OK' and j.get('data'):
                return j['data']
            return []   # stat 非 OK = 假日或無資料，不必重試
        except Exception as e:
            print(f'  [{date_str}] TWSE 第{i+1}次失敗: {e}')
            if i < retries - 1:
                time.sleep(5)
    return None

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

def load_existing():
    """讀現有檔案 → (stocks_map, healthy_dates)
    stocks_map: { sid: { date: [外資,投信,自營] } }
    健康日期 = 該日非零檔數 > 1500（只剩 TPEX 的壞日約 900 檔，含 TWSE 會破萬）"""
    if not os.path.exists(OUTPUT):
        return {}, set()
    try:
        with open(OUTPUT, 'r', encoding='utf-8') as f:
            d = json.load(f)
        dates = d.get('dates', [])
        stocks_map = {}
        nonzero = [0] * len(dates)
        for sid, rows in d.get('stocks', {}).items():
            m = {}
            for i, dt in enumerate(dates):
                if i < len(rows):
                    m[dt] = rows[i]
                    if any(rows[i]):
                        nonzero[i] += 1
            stocks_map[sid] = m
        healthy = set(dt for i, dt in enumerate(dates) if nonzero[i] > 1500)
        print(f'現有檔案：{len(dates)} 日，其中健康 {len(healthy)} 日')
        return stocks_map, healthy
    except Exception as e:
        print(f'讀取現有檔案失敗: {e}')
        return {}, set()

def already_updated_today():
    """今日有效資料已存在就跳過（防止三個 cron 重複跑）
    用大型上市股驗證，避免 TWSE 被擋、只剩 TPEX 資料時誤判為有效"""
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
            total = 0
            for sid in ['2330', '2317', '2454', '2881', '2603']:
                rows = stocks.get(sid)
                if rows and rows[0]:
                    total += sum(abs(v) for v in rows[0])
            return total > 0   # 大型股當日全為 0 = TWSE 沒抓到，不算有效
    except:
        pass
    return False

def fetch_date_into(date_str, stocks, twse_retries=3):
    """抓某一天 TWSE + TPEX 並合併進 stocks。
    回傳狀態：'ok' 成功 / 'holiday' 假日無資料 / 'pending' 尚未發布 / 'blocked' TWSE 被擋"""
    twse_rows = fetch_twse(date_str, retries=twse_retries)
    if twse_rows is None:
        return 'blocked'

    tpex_rows = fetch_tpex(date_str)

    if not twse_rows and not tpex_rows:
        return 'holiday'

    # 若全市場加總都是 0，TWSE 資料尚未發布
    total_abs = sum(abs(parse_num(r[4])) + abs(parse_num(r[10])) + abs(parse_num(r[11]))
                    for r in twse_rows if len(r) >= 12)
    if total_abs == 0 and len(twse_rows) > 100:
        return 'pending'

    count = 0
    # TWSE 欄位：[代號, 名稱, 外陸資買進, 外陸資賣出, 外陸資買賣超, 投信買進, 投信賣出, 投信買賣超, 自營買賣超合計, ...]
    for row in twse_rows:
        if len(row) < 12:
            continue
        sid = str(row[0]).strip()
        if not sid or len(sid) < 4 or len(sid) > 7:
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
    return 'ok'

def main():
    if already_updated_today():
        print('今日有效資料已存在，跳過。')
        return
    print('開始抓取三大法人資料（TWSE + TPEX）...')

    # 先讀自選股清單，稍後用來做「保證有資料」的優先驗證
    watchlist = fetch_watchlist()

    stocks, healthy = load_existing()

    # 往回 45 個日曆天，確保取到 30 個交易日
    today = datetime.now()
    dates_to_try = [
        (today - timedelta(days=i)).strftime('%Y-%m-%d')
        for i in range(45)
    ]

    today_str    = today.strftime('%Y-%m-%d')
    today_status = 'ok' if today_str in healthy else 'unknown'

    valid_dates = []

    for date_str in dates_to_try:
        if len(valid_dates) >= 30:
            break

        # 現有資料健康的日期直接沿用，不重抓
        if date_str in healthy:
            valid_dates.append(date_str)
            continue

        status = fetch_date_into(date_str, stocks)
        if date_str == today_str:
            today_status = status
        if status == 'blocked':
            # TWSE 被擋：寧缺勿錯，跳過此日，留給下個 cron / 明天補抓
            print(f'  {date_str}: TWSE 連續失敗，跳過此日')
            time.sleep(1.5)
            continue
        if status == 'holiday':
            print(f'  {date_str}: 無資料（假日）')
            time.sleep(0.5)
            continue
        if status == 'pending':
            print(f'  {date_str}: TWSE 資料尚未發布（全為 0），跳過')
            time.sleep(0.5)
            continue

        valid_dates.append(date_str)
        time.sleep(1.5)   # 避免打太快

    if not valid_dates:
        print('錯誤：沒有抓到任何資料，中止。')
        notify_tg('🔴 <b>台股三大法人資料抓取失敗</b>\n\n今天完全沒抓到任何交易日資料，TWSE / TPEX API 可能異常或 GitHub Actions IP 被封鎖，請檢查。')
        return

    # ── 自選股保證檢查：自選股在某日 TWSE 全為 0 = 該日上市資料沒抓成功 ──
    # 對這些日期加強重試（5 次）；仍失敗就剔除該日，不讓自選股顯示錯誤的 0
    if watchlist:
        retried = set()
        for date_str in list(valid_dates):
            if date_str in healthy:
                continue  # 沿用的舊資料已驗證過健康
            present = [stocks.get(s, {}).get(date_str) for s in watchlist]
            present = [v for v in present if v is not None]
            # 自選股在該日全部 [0,0,0] → 視為該日 TWSE 沒成功
            if present and all(v == [0, 0, 0] for v in present):
                print(f'  ⚠️ 自選股在 {date_str} 全為 0，加強重試...')
                status = fetch_date_into(date_str, stocks, twse_retries=5)
                retried.add(date_str)
                if status != 'ok':
                    # 仍失敗 → 剔除該日，寧可少一天也不顯示錯誤資料
                    print(f'  ✗ {date_str} 重試後仍失敗，剔除此日')
                    valid_dates.remove(date_str)
                    if date_str == today_str:
                        today_status = 'dropped'
                else:
                    print(f'  ✓ {date_str} 補抓成功')
                    if date_str == today_str:
                        today_status = 'ok'
                time.sleep(1.5)
        if not retried:
            print(f'自選股保證檢查：全部 {len(valid_dates)} 日皆有資料 ✓')

    # 組成精簡格式：{ updated, dates: [...], stocks: { sid: [[外資,投信,自營], ...] } }
    # 30 日內全為 0 的代號（下市、無資料的權證）直接剔除，避免檔案膨脹
    output = {
        'updated': today.strftime('%Y-%m-%d'),
        'dates':   valid_dates,
        'stocks':  {}
    }

    for sid, date_map in stocks.items():
        arr = [date_map.get(d, [0, 0, 0]) for d in valid_dates]
        if any(any(row) for row in arr):
            output['stocks'][sid] = arr

    os.makedirs('data', exist_ok=True)
    with open(OUTPUT, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, separators=(',', ':'))

    size_kb = os.path.getsize(OUTPUT) / 1024
    print(f'完成！{len(output["stocks"])} 檔 × {len(valid_dates)} 交易日，檔案 {size_kb:.0f} KB')

    # ── 資料異常 → TG 通知 ──
    # 今日是交易日（非假日）但資料沒進來（被擋 / 尚未發布 / 自選股驗證剔除）
    # 只在「最後一班」cron（台灣 17:00 = UTC 09 時）才發，避免 16:00/16:30 還在重試就先報警洗版。
    # 手動觸發（workflow_dispatch）不在此時段 → 不發，留給排程把關。
    if today_status in ('blocked', 'pending', 'dropped'):
        is_final_run = datetime.utcnow().hour == 9
        if is_final_run:
            reason = {
                'blocked': 'TWSE 連續被擋（GitHub Actions IP 可能被封鎖）',
                'pending': 'TWSE 今日資料尚未發布',
                'dropped': '自選股今日資料驗證失敗已剔除',
            }[today_status]
            notify_tg(
                f'⚠️ <b>台股三大法人：今日資料未更新</b>\n\n'
                f'原因：{reason}\n'
                f'目前資料顯示至 <b>{valid_dates[0]}</b>（{today_str} 尚未進來）\n\n'
                f'明天開盤後的排程會自動補抓，儀表板也已標示延遲。'
            )
        else:
            print(f'今日資料未更新（{today_status}），非最後一班 cron，暫不發 TG（等 17:00 把關）')

if __name__ == '__main__':
    main()
