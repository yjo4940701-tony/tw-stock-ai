#!/usr/bin/env python3
"""台灣總經指標 → data/macro_tw.json

自動：TV 財經日曆（經 tw-stock-news Worker 的 tvcal 路由）取各指標最新實際值。
自動：景氣對策信號（國發會政府開放資料 zip）、中經院 PMI（中央社搜尋標題解析）。
備援：自動失敗時讀 data/macro_tw_manual.json（必須帶 date/source）。
"""
import csv, io, json, os, re, subprocess, sys, zipfile
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


def curl_bytes(url):
    p = subprocess.run(['curl', '-sfL', '-m', '60', '-A', 'Mozilla/5.0', url], capture_output=True, timeout=90)
    if p.returncode:
        raise RuntimeError(f'curl exit {p.returncode}')
    return p.stdout


def fetch_ndc_signal():
    """國發會「景氣指標及燈號」政府開放資料（data.gov.tw 6099）→ 最新一期綜合分數。
    下載網址從 data.gov.tw API 動態取得，國發會換檔名也不會壞。"""
    meta = json.loads(curl_bytes('https://data.gov.tw/api/v2/rest/dataset/6099').decode())
    url = meta['result']['distribution'][0]['resourceDownloadUrl']
    z = zipfile.ZipFile(io.BytesIO(curl_bytes(url)))
    for info in z.infolist():
        txt = z.read(info).decode('utf-8-sig', 'replace')
        if '景氣對策信號綜合分數' not in txt.split('\n', 1)[0]:
            continue
        rows = [r for r in csv.DictReader(io.StringIO(txt)) if (r.get('景氣對策信號綜合分數') or '').strip()]
        last, prev = rows[-1], rows[-2]
        d = last['Date'].strip()
        return {'name': f"景氣對策信號綜合判斷分數({int(d[4:])}月)", 'value': int(float(last['景氣對策信號綜合分數'])),
                'prev': int(float(prev['景氣對策信號綜合分數'])), 'date': f'{d[:4]}-{d[4:]}',
                'light': last.get('景氣對策信號'), 'source': '國發會 政府開放資料（自動）', 'auto': True}
    raise RuntimeError('zip 內找不到景氣對策信號欄位')


def fetch_cna_pmi():
    """中經院 PMI：中經院網站擋程式，改從中央社搜尋結果標題解析「N月PMI xx.x%」。"""
    from urllib.parse import quote
    def search(q):
        return curl_bytes('https://www.cna.com.tw/search/hysearchws.aspx?q=' + quote(q)).decode('utf-8', 'replace')
    found = parse_pmi(search('中經院 PMI'))
    if not found:
        raise RuntimeError('中央社搜尋結果找不到 PMI 標題')
    # 同一個月可能有多篇，取最早發布的（通常是中經院發布當天的那篇）
    found.sort(key=lambda x: (x['date'], x['published']))
    latest_date = max(x['date'] for x in found)
    last = next(x for x in found if x['date'] == latest_date)
    prev = next((x['value'] for x in reversed(found) if x['date'] < latest_date), None)
    if prev is None:  # 搜尋結果只剩本月 → 另搜上個月
        pm = int(latest_date[5:]) - 1 or 12
        prev = next((x['value'] for x in parse_pmi(search(f'{pm}月PMI 中經院')) if x['date'] < latest_date), None)
    return {'name': f"中經院製造業 PMI({int(last['date'][5:])}月)", 'value': last['value'], 'prev': prev,
            'date': last['date'], 'published': last['published'],
            'source': f"中經院，中央社標題解析（自動）{last['url']}", 'auto': True}


def parse_pmi(html):
    found = []
    for m in re.finditer(r'href="(/news/\w+/\d+\.aspx)"(?:(?!href=).)*?<h2>([^<]*?(\d{1,2})月(?:製造業)?PMI\s*([\d.]+)%[^<]*)</h2>\s*<time[^>]*datetime="(\d{4})-(\d{2})-(\d{2})', html, re.S):
        href, title, mon, val, py, pm, pd = m.groups()
        mon, py = int(mon), int(py)
        yr = py - 1 if mon > int(pm) else py
        found.append({'date': f'{yr}-{mon:02d}', 'value': float(val), 'published': f'{py}-{pm}-{pd}',
                      'url': 'https://www.cna.com.tw' + href})
    return found


def main():
    now = datetime.now(timezone.utc)
    # 抓 400 天：除了取最新值，也保留 CPI 年增率歷史，供 macro_regime 算「連續幾個月高於 2%」
    frm = (now - timedelta(days=400)).strftime('%Y-%m-%dT00:00:00.000Z')
    to = (now + timedelta(days=1)).strftime('%Y-%m-%dT00:00:00.000Z')
    out = {'fetched': now.isoformat(timespec='seconds'), 'auto': {}, 'manual': {}, 'history': {}}
    try:
        d = curl_json(f'{WORKER}?type=tvcal&countries=TW&from={frm}&to={to}')
        events = sorted(d.get('result', []), key=lambda e: e.get('date', ''))
        for e in events:
            t = e.get('title')
            if t in WANT and e.get('actual') is not None:
                k, name = WANT[t]
                out['auto'][k] = {'name': name, 'value': e['actual'], 'prev': e.get('previous'),
                                  'date': e['date'][:10], 'source': 'TradingView 財經日曆'}
                if k == 'cpi_yoy':
                    out['history'].setdefault(k, []).append([e['date'][:10], e['actual']])
    except Exception as ex:
        print('tvcal FAIL:', ex, file=sys.stderr)
        out['auto_error'] = str(ex)

    # 市場事件觀察：加權指數、費半（TV Scanner，延遲報價快照）。只供「事件」提醒，不參與階段判斷
    MARKET = {'TWSE:IX0001': '加權指數', 'NASDAQ:SOX': '費城半導體'}
    out['market'] = {}
    for sym, name in MARKET.items():
        try:
            m = curl_json(f'https://scanner.tradingview.com/symbol?symbol={sym}'
                          '&fields=close,change,High.1M,Perf.W&no_404=true')
            if m and m.get('close') and m.get('High.1M'):
                out['market'][sym] = {'name': name, 'close': m['close'], 'high_1m': m['High.1M'],
                                      'chg_1d_pct': m.get('change'), 'perf_w_pct': m.get('Perf.W'),
                                      'source': 'TradingView Scanner（延遲報價快照）'}
        except Exception as ex:
            print(f'market {sym} FAIL:', ex, file=sys.stderr)

    # 先讀手動檔當備援，再用自動來源覆蓋（自動抓到且期別不舊於手動才採用）
    mp = os.path.join(BASE, 'macro_tw_manual.json')
    m = {}
    if os.path.exists(mp):
        m = json.load(open(mp, encoding='utf-8'))
        for k, v in m.items():
            if k.startswith('_'):
                continue
            if v.get('value') is not None and v.get('date') and v.get('source'):
                out['manual'][k] = dict(v, auto=False)
            else:
                print(f'manual {k} 缺 value/date/source，略過', file=sys.stderr)
    for k, fn in (('business_signal_score', fetch_ndc_signal), ('pmi_manufacturing', fetch_cna_pmi)):
        try:
            v = fn()
            old = out['manual'].get(k)
            if v and (not old or v['date'] >= old['date']):
                if v.get('prev') is None and old:  # 前值抓不到 → 用備援檔推
                    v['prev'] = old.get('prev') if old['date'] == v['date'] else old['value']
                out['manual'][k] = v
                if not old or v['date'] > old['date']:  # 新一期 → 寫回備援檔，下個月當前值
                    m[k] = {kk: v[kk] for kk in ('name', 'value', 'prev', 'date', 'published', 'source') if v.get(kk) is not None}
                    json.dump(m, open(mp, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
        except Exception as ex:
            print(f'{k} 自動抓取 FAIL（沿用手動檔）:', ex, file=sys.stderr)

    with open(os.path.join(BASE, 'macro_tw.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"macro_tw.json: auto {len(out['auto'])}/{len(WANT)}, manual {len(out['manual'])}")
    return 0 if out['auto'] else 1


if __name__ == '__main__':
    sys.exit(main())
