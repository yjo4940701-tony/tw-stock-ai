#!/usr/bin/env python3
"""台灣總經指標 → data/macro_tw.json

自動：TV 財經日曆（經 tw-stock-news Worker 的 tvcal 路由）取各指標最新實際值。
手動：景氣對策信號、中經院製造業 PMI（國發會/中經院網站擋程式請求，無可靠自動來源）
      讀 data/macro_tw_manual.json，必須帶 date/source，缺欄位就不採用。
"""
import json, os, subprocess, sys
from datetime import datetime, timedelta, timezone

BASE = os.path.join(os.path.dirname(__file__), '..', 'data')
WORKER = 'https://tw-stock-news.yjo4940701.workers.dev/'

# TV 標題 → (key, 中文名)
WANT = {
    'Inflation Rate YoY': ('cpi_yoy', '台灣 CPI 年增率%'),
    'Exports YoY': ('exports_yoy', '出口年增率%'),
    'Export Orders YoY': ('export_orders_yoy', '外銷訂單年增率%'),
    'Industrial Production YoY': ('ip_yoy', '工業生產年增率%'),
    'Unemployment Rate': ('unemployment', '失業率%'),
    'M2 Money Supply YoY': ('m2_yoy', 'M2 年增率%'),
    'Interest Rate Decision': ('policy_rate', '央行政策利率%'),
    'GDP Growth Rate YoY Adv': ('gdp_yoy', 'GDP 年增率%(概估)'),
}


def curl_json(url):
    p = subprocess.run(['curl', '-sf', '-m', '60', url], capture_output=True, timeout=90)
    if p.returncode:
        raise RuntimeError(f'curl exit {p.returncode}')
    return json.loads(p.stdout.decode())


def main():
    now = datetime.now(timezone.utc)
    frm = (now - timedelta(days=120)).strftime('%Y-%m-%dT00:00:00.000Z')
    to = (now + timedelta(days=1)).strftime('%Y-%m-%dT00:00:00.000Z')
    out = {'fetched': now.isoformat(timespec='seconds'), 'auto': {}, 'manual': {}}
    try:
        d = curl_json(f'{WORKER}?type=tvcal&countries=TW&from={frm}&to={to}')
        events = sorted(d.get('result', []), key=lambda e: e.get('date', ''))
        for e in events:
            t = e.get('title')
            if t in WANT and e.get('actual') is not None:
                k, name = WANT[t]
                out['auto'][k] = {'name': name, 'value': e['actual'], 'prev': e.get('previous'),
                                  'date': e['date'][:10], 'source': 'TradingView 財經日曆'}
    except Exception as ex:
        print('tvcal FAIL:', ex, file=sys.stderr)
        out['auto_error'] = str(ex)

    mp = os.path.join(BASE, 'macro_tw_manual.json')
    if os.path.exists(mp):
        m = json.load(open(mp, encoding='utf-8'))
        for k, v in m.items():
            if k.startswith('_'):
                continue
            if v.get('value') is not None and v.get('date') and v.get('source'):
                out['manual'][k] = v
            else:
                print(f'manual {k} 缺 value/date/source，略過', file=sys.stderr)

    with open(os.path.join(BASE, 'macro_tw.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"macro_tw.json: auto {len(out['auto'])}/{len(WANT)}, manual {len(out['manual'])}")
    return 0 if out['auto'] else 1


if __name__ == '__main__':
    sys.exit(main())
