#!/usr/bin/env python3
"""抓美國總經指標（FRED 免 key CSV 端點）→ data/macro.json

每筆存：最新值、資料日期、前值、年增率(月頻指數)、來源、抓取時間。
失敗的序列不寫假值，標 error 並保留其餘。
"""
import csv, io, json, os, subprocess, sys, time
from datetime import datetime, timedelta, timezone

OUT = os.path.join(os.path.dirname(__file__), '..', 'data', 'macro.json')
URL = 'https://fred.stlouisfed.org/graph/fredgraph.csv?id='

# id: (名稱, 頻率, 是否算年增率)
SERIES = {
    'CPIAUCSL':  ('美國 CPI', 'M', True),
    'CPILFESL':  ('美國核心 CPI', 'M', True),
    'PCEPILFE':  ('美國核心 PCE', 'M', True),
    'PAYEMS':    ('美國非農就業(千人)', 'M', False),
    'UNRATE':    ('美國失業率%', 'M', False),
    'DFEDTARU':  ('Fed 利率目標上限%', 'D', False),
    'DGS10':     ('美債10Y殖利率%', 'D', False),
    'T10Y2Y':    ('10Y-2Y 利差', 'D', False),
    'DTWEXBGS':  ('美元指數(廣義)', 'D', False),
    'VIXCLS':    ('VIX', 'D', False),
    'DCOILWTICO': ('WTI 原油', 'D', False),
    'SP500':     ('S&P 500', 'D', False),
}


def fetch(sid):
    # FRED 會斷開 Python urllib 連線（實測 curl 正常），改走 curl
    for i in range(3):
        try:
            p = subprocess.run(['curl', '-sf', '-m', '60', URL + sid],
                               capture_output=True, timeout=90)
            if p.returncode != 0:
                raise RuntimeError(f'curl exit {p.returncode}')
            rows = list(csv.reader(io.StringIO(p.stdout.decode())))[1:]
            break
        except Exception:
            if i == 2:
                raise
            time.sleep(3)
    return [(d, float(v)) for d, v in rows if v not in ('', '.')]


def main():
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    out = {'fetched': now, 'source': 'FRED (fredgraph.csv)', 'series': {}}
    for sid, (name, freq, yoy) in SERIES.items():
        try:
            pts = fetch(sid)
            d, v = pts[-1]
            item = {'name': name, 'freq': freq, 'date': d, 'value': v,
                    'prev_date': pts[-2][0], 'prev': pts[-2][1]}
            if freq == 'D':  # 約 90 天前的值，供判斷趨勢方向
                cut = (datetime.fromisoformat(d) - timedelta(days=90)).date().isoformat()
                old = [p for p in pts if p[0] <= cut]
                if old:
                    item['ago90_date'], item['ago90'] = old[-1]
            if yoy and len(pts) > 13:
                item['yoy'] = round((v / pts[-13][1] - 1) * 100, 2)
                item['yoy_prev'] = round((pts[-2][1] / pts[-14][1] - 1) * 100, 2)
            out['series'][sid] = item
        except Exception as e:  # 不補假值
            out['series'][sid] = {'name': name, 'error': str(e)}
            print(f'FAIL {sid}: {e}', file=sys.stderr)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    ok = sum('error' not in s for s in out['series'].values())
    print(f'macro.json: {ok}/{len(SERIES)} ok')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
