import requests
import json
import os
from datetime import datetime, timedelta

ALERTS_FILE = 'alerts.json'

def load_alerts():
    if not os.path.exists(ALERTS_FILE):
        return []
    with open(ALERTS_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_alerts(alerts):
    with open(ALERTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(alerts, f, ensure_ascii=False, indent=2)

def get_latest_price(ticker):
    start = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    url = f'https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockPrice&data_id={ticker}&start_date={start}'
    try:
        r = requests.get(url, timeout=15)
        data = r.json().get('data', [])
        if not data:
            return None
        data.sort(key=lambda x: x['date'])
        return float(data[-1]['close'])
    except Exception as e:
        print(f'[{ticker}] 查價失敗: {e}')
        return None

def send_telegram(token, chat_id, message):
    url = f'https://api.telegram.org/bot{token}/sendMessage'
    r = requests.post(url, json={
        'chat_id': chat_id,
        'text': message,
        'parse_mode': 'HTML'
    }, timeout=10)
    if r.status_code != 200:
        print(f'Telegram 發送失敗: {r.text}')

def main():
    tg_token = os.environ.get('TG_BOT_TOKEN', '')
    tg_chat  = os.environ.get('TG_CHAT_ID', '')
    if not tg_token or not tg_chat:
        print('缺少 TG_BOT_TOKEN 或 TG_CHAT_ID')
        return

    alerts  = load_alerts()
    pending = [a for a in alerts if not a.get('triggered')]
    if not pending:
        print('沒有待檢查的提醒')
        return

    print(f'待檢查提醒數：{len(pending)}')

    # 每個 ticker 只查一次價格
    price_cache = {}
    tickers = list(set(a['ticker'] for a in pending))
    for ticker in tickers:
        price_cache[ticker] = get_latest_price(ticker)
        print(f'  {ticker} → {price_cache[ticker]}')

    changed = False
    for alert in alerts:
        if alert.get('triggered'):
            continue
        price = price_cache.get(alert['ticker'])
        if price is None:
            continue

        hit = (alert['direction'] == 'below' and price <= alert['price']) or \
              (alert['direction'] == 'above' and price >= alert['price'])

        if not hit:
            continue

        type_map = {'stop': '🛑 停損', 'target': '🎯 目標', 'add': '📥 加碼'}
        type_str = type_map.get(alert['type'], '提醒')
        dir_str  = '跌破' if alert['direction'] == 'below' else '突破'
        diff_pct = (price - alert['price']) / alert['price'] * 100

        msg = (
            f"📈 <b>台股價格提醒觸發！</b>\n\n"
            f"{type_str}｜<b>{alert['name']}（{alert['ticker']}）</b>\n"
            f"設定價：{alert['price']} 元\n"
            f"現價：<b>{price:.2f} 元</b>（{dir_str}設定價 {diff_pct:+.1f}%）\n\n"
            f"<i>設定時間：{alert.get('createdAt','')[:10]}</i>"
        )
        send_telegram(tg_token, tg_chat, msg)
        print(f'  ✅ 已發送 TG 提醒：{alert["name"]} {alert["price"]}')

        alert['triggered']      = True
        alert['triggeredAt']    = datetime.now().isoformat()
        alert['triggeredPrice'] = price
        changed = True

    if changed:
        save_alerts(alerts)
        print('alerts.json 已更新')
    else:
        print('無觸發提醒')

if __name__ == '__main__':
    main()
