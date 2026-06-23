#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全市場日K 建底/增量腳本（TWSE + TPEX 官方端點，免費免 token）

產出 data/ohlcv.json，緊湊欄位格式（共用日期軸）：
{
  "updated": "2026-06-23",
  "dates": ["2024-06-20", ..., "2026-06-23"],   # 舊→新
  "stocks": { "2330": {"o":[...],"h":[...],"l":[...],"c":[...],"v":[...]}, ... }
}
- 陣列對齊 dates 索引；某股某日無資料 → null。
- 價格 2 位小數；量存「張」(成交股數/1000，整數) 以縮小體積、對齊前端指標慣例。

用法：
  python build_ohlcv.py --backfill 730      # 首次建底：回補近 730 天（~500 交易日）
  python build_ohlcv.py                      # 增量：補齊現有檔之後到今天的交易日
"""
import os, sys, json, time, gzip, argparse, urllib.request
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
OUT  = os.path.join(HERE, '..', 'data', 'ohlcv.json.gz')   # 壓縮存檔，前端 DecompressionStream 解
KEEP_DAYS = 520          # 修剪：只留最近 ~520 交易日
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

# ── 共用 ────────────────────────────────────────────
def num(s):
    """'2,395.00'->2395.0 ; '--'/''/'X'->None"""
    if s is None: return None
    s = str(s).replace(',', '').strip()
    if s in ('', '--', '---', 'X', 'N/A', 'null'): return None
    try: return float(s)
    except ValueError: return None

def http_json(url, tries=3, sleep=2):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            raw = urllib.request.urlopen(req, timeout=40).read().decode('utf-8')
            return json.loads(raw)
        except Exception as e:
            last = e
            time.sleep(sleep * (i + 1))
    print('  [warn] http fail:', repr(last)[:80])
    return None

def field_idx(fields, *keys):
    """回傳第一個「欄位名稱含任一 key」的索引，找不到 -1"""
    for i, f in enumerate(fields):
        fs = str(f)
        if any(k in fs for k in keys): return i
    return -1

def is_stock(sid):
    """只留真股票/ETF，擋掉權證(6位 03xxxxP 等)、債券、奇怪代號。
       4 位純數字=普通股/TDR；00 開頭=ETF(含槓桿/反向 00631L/00632R)。"""
    if len(sid) == 4 and sid.isdigit(): return True
    if sid.startswith('00'): return True
    return False

# ── TWSE 上市：MI_INDEX（認歷史日，個股表 ~1366 檔）──
def fetch_twse(d):
    """d=date 物件 → {sid:[o,h,l,c,vol張]} ；非交易日回 None"""
    url = ('https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX'
           '?date=%s&type=ALLBUT0999&response=json' % d.strftime('%Y%m%d'))
    j = http_json(url)
    if not j or j.get('stat') != 'OK': return None
    for t in j.get('tables', []):
        fields = t.get('fields', []); data = t.get('data', [])
        if len(data) < 500: continue
        ic = field_idx(fields, '證券代號', '代號')
        io = field_idx(fields, '開盤價'); ih = field_idx(fields, '最高價')
        il = field_idx(fields, '最低價'); icl = field_idx(fields, '收盤價')
        iv = field_idx(fields, '成交股數')
        if min(ic, io, ih, il, icl, iv) < 0: continue
        out = {}
        for r in data:
            sid = str(r[ic]).strip()
            if not is_stock(sid): continue
            o, h, l, c = num(r[io]), num(r[ih]), num(r[il]), num(r[icl])
            v = num(r[iv])
            if c is None: continue          # 無收盤（當日無成交）→ 跳過該檔
            out[sid] = [o, h, l, c, int(v / 1000) if v is not None else None]
        return out
    return None

# ── TPEX 上櫃：新端點 dailyQuotes（認歷史日）──
def fetch_tpex(d):
    """d=date 物件 → {sid:[o,h,l,c,vol張]} ；非交易日/失敗回 None"""
    url = ('https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes'
           '?date=%s&type=EW&response=json' % d.strftime('%Y/%m/%d'))
    j = http_json(url)
    if not j: return None
    for t in j.get('tables', []):
        fields = t.get('fields', []); data = t.get('data', [])
        if len(data) < 300: continue
        ic = field_idx(fields, '代號')
        io = field_idx(fields, '開盤'); ih = field_idx(fields, '最高')
        il = field_idx(fields, '最低'); icl = field_idx(fields, '收盤')
        iv = field_idx(fields, '成交股數')
        if min(ic, io, ih, il, icl, iv) < 0: continue
        out = {}
        for r in data:
            sid = str(r[ic]).strip()
            if not is_stock(sid): continue
            o, h, l, c = num(r[io]), num(r[ih]), num(r[il]), num(r[icl])
            v = num(r[iv])
            if c is None: continue
            out[sid] = [o, h, l, c, int(v / 1000) if v is not None else None]
        return out
    return None

def fetch_day(d):
    """合併 TWSE+TPEX；TWSE 非交易日 → 整天 None（不浪費打 TPEX）"""
    tw = fetch_twse(d)
    if tw is None: return None              # 非交易日或上市抓取失敗
    tp = fetch_tpex(d) or {}
    tw.update(tp)                           # 上市 + 上櫃
    return tw

# ── 資料結構（欄位格式）增量 append ─────────────────
def load_data():
    if os.path.exists(OUT):
        with gzip.open(OUT, 'rt', encoding='utf-8') as f:
            return json.load(f)
    return {'updated': None, 'dates': [], 'stocks': {}}

def append_date(data, ds, quotes):
    """把某日 quotes={sid:[o,h,l,c,v]} 接到欄位結構尾端（ds 必須晚於現有最後一天）"""
    dates = data['dates']; stocks = data['stocks']
    if ds in dates: return False
    n = len(dates)
    dates.append(ds)
    for sid, arr in stocks.items():         # 既有股：有值補值、無值補 null
        q = quotes.get(sid)
        for k, idx in (('o', 0), ('h', 1), ('l', 2), ('c', 3), ('v', 4)):
            arr[k].append(q[idx] if q else None)
    for sid, q in quotes.items():           # 新股：前面補 n 個 null
        if sid not in stocks:
            stocks[sid] = {k: [None] * n + [q[i]] for k, i in
                           (('o', 0), ('h', 1), ('l', 2), ('c', 3), ('v', 4))}
    return True

def trim(data, keep=KEEP_DAYS):
    extra = len(data['dates']) - keep
    if extra <= 0: return
    data['dates'] = data['dates'][extra:]
    drop = []
    for sid, arr in data['stocks'].items():
        for k in arr: arr[k] = arr[k][extra:]
        if all(x is None for x in arr['c']): drop.append(sid)
    for sid in drop: del data['stocks'][sid]   # 整窗無資料的股移除

def save(data):
    data['updated'] = data['dates'][-1] if data['dates'] else None
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    # mtime=0：確定性壓縮，資料未變→位元相同→git 不會在假日 no-op 時誤 commit
    with gzip.GzipFile(OUT, 'wb', mtime=0) as gz:
        gz.write(payload)
    sz = os.path.getsize(OUT) / 1024 / 1024
    print('saved %s | dates=%d stocks=%d | %.1f MB (gz)' %
          (OUT, len(data['dates']), len(data['stocks']), sz))

# ── 主流程 ──────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--backfill', type=int, default=0,
                    help='回補近 N 天（首次建底，如 730）；省略=增量')
    ap.add_argument('--throttle', type=float, default=1.2, help='每日間隔秒')
    ap.add_argument('--force', action='store_true',
                    help='允許 backfill 覆蓋成更短的現有檔（保險預設禁止）')
    args = ap.parse_args()

    # 🔴 保險：backfill 是「從空白重建」，若會把現有完整檔覆蓋成更短 → 拒絕（除非 --force）
    # 防呆 2026-06-23 慘案：誤觸 backfill=5 把正式站 478 天 base 蓋成 3 天
    if args.backfill and not args.force and os.path.exists(OUT):
        try:
            existing = load_data()
            cur_days = len(existing.get('dates', []))
            est_new  = int(args.backfill * 5 / 7)   # ~交易日估計
            if cur_days > est_new:
                print('[拒絕] 現有檔有 %d 個交易日，backfill %d 天只會建約 %d 天 → '
                      '會覆蓋成更短的檔。' % (cur_days, args.backfill, est_new))
                print('       若真要重建，加 --force；或用更大的 --backfill。增量請省略 --backfill。')
                sys.exit(1)
        except Exception:
            pass   # 現有檔讀不出 → 視為可重建

    # --backfill 一律從空白重建（避免沿用舊檔殘留）；增量才讀現有檔
    data = {'updated': None, 'dates': [], 'stocks': {}} if args.backfill else load_data()
    have = set(data['dates'])
    last = data['dates'][-1] if data['dates'] else None

    today = date.today()
    if args.backfill:
        start = today - timedelta(days=args.backfill)
    elif last:
        start = datetime.strptime(last, '%Y-%m-%d').date() + timedelta(days=1)
    else:
        print('無現有檔且未指定 --backfill，預設回補 730 天')
        start = today - timedelta(days=730)

    # 逐日推進（跳週末；假日靠 TWSE stat 判斷）
    d = start
    added = 0
    while d <= today:
        if d.weekday() >= 5:                # 六日
            d += timedelta(days=1); continue
        ds = d.isoformat()
        if ds in have:
            d += timedelta(days=1); continue
        q = fetch_day(d)
        if q:
            if append_date(data, ds, q):
                added += 1
                print('  + %s : %d 檔' % (ds, len(q)))
        time.sleep(args.throttle)
        d += timedelta(days=1)

    print('新增 %d 個交易日' % added)
    trim(data)
    save(data)

if __name__ == '__main__':
    main()
