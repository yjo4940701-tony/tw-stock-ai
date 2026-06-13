"""
台股自選股週報 → Telegram（每週五收盤後）
資料來源全部免費、現成：
- 自選股清單：公開 Gist tw_groups
- 週漲跌：TradingView Scanner（Perf.W），全市場掃描後比對自選股
- 法人連買：data/institutional.json（外資 / 投信連續買超天數）
- 高 EPS：data/fundamentals.json
中文股名：解析 index.html 的 STOCK_NAMES（自動同步，找不到則用代號）
"""
import requests
import json
import os
import re
from datetime import datetime

GIST_ID   = '0b9966cb6fc32b5aeffe4ad7bdc07836'
INST_FILE = 'data/institutional.json'
FUND_FILE = 'data/fundamentals.json'
HTML_FILE = 'index.html'
TV_URL    = 'https://scanner.tradingview.com/taiwan/scan'


def get_watchlist():
    """讀公開 Gist 取自選股（跨群組合併，保留順序去重）"""
    r = requests.get(f'https://api.github.com/gists/{GIST_ID}', timeout=20)
    files = r.json().get('files', {})
    f = files.get('tw-stock-settings.json') or next(iter(files.values()), None)
    if not f:
        return []
    cfg = json.loads(f.get('content') or '{}')
    groups = (cfg.get('tw_groups') or {}).get('list', [])
    wl = []
    for g in groups:
        for t in g.get('tickers', []):
            t = str(t).strip()
            if t and t not in wl:
                wl.append(t)
    return wl


def load_names():
    """從 index.html 解析 STOCK_NAMES 中文股名對照"""
    names = {}
    try:
        with open(HTML_FILE, encoding='utf-8') as f:
            html = f.read()
        m = re.search(r'var STOCK_NAMES\s*=\s*\{(.*?)\n\};', html, re.S)
        if m:
            for sid, name in re.findall(r"'([0-9A-Za-z]{4,6})'\s*:\s*'([^']+)'", m.group(1)):
                names[sid] = name
    except Exception as e:
        print(f'解析股名失敗（改用代號）: {e}')
    return names


def get_weekly_perf(watchlist):
    """TV 全市場掃描 Perf.W（週績效 %），比對自選股"""
    body = json.dumps({
        'columns': ['close', 'change', 'Perf.W'],
        'sort': {'sortBy': 'volume', 'sortOrder': 'desc'},
        'range': [0, 12000]
    })
    try:
        # text/plain 避開 CORS preflight（Python 端其實無 CORS，但與前端一致）
        r = requests.post(TV_URL, data=body, headers={'Content-Type': 'text/plain'}, timeout=30)
        rows = r.json().get('data', [])
        m = {}
        for it in rows:
            sid = it['s'].split(':')[1]
            m[sid] = it['d'][2]   # Perf.W
        return {t: m[t] for t in watchlist if t in m and m[t] is not None}
    except Exception as e:
        print(f'TV 週績效抓取失敗（略過漲跌段）: {e}')
        return {}


def get_inst_streak(watchlist):
    """外資 / 投信連續買超天數（institutional.json，index 0 最新）"""
    try:
        with open(INST_FILE, encoding='utf-8') as f:
            stocks = json.load(f).get('stocks', {})
    except Exception as e:
        print(f'讀 institutional.json 失敗: {e}')
        return {}
    res = {}
    for t in watchlist:
        arr = stocks.get(t)
        if not arr:
            continue
        def streak(idx):
            n = 0
            for row in arr:
                if len(row) > idx and row[idx] > 0:
                    n += 1
                else:
                    break
            return n
        res[t] = {'foreign': streak(0), 'trust': streak(1)}
    return res


def get_eps(watchlist):
    """高 EPS 標的（fundamentals.json）"""
    try:
        with open(FUND_FILE, encoding='utf-8') as f:
            stocks = json.load(f).get('stocks', {})
    except Exception as e:
        print(f'讀 fundamentals.json 失敗: {e}')
        return {}
    return {t: stocks[t]['eps'] for t in watchlist
            if t in stocks and stocks[t].get('eps') is not None}


def send_telegram(token, chat_id, message):
    r = requests.post(f'https://api.telegram.org/bot{token}/sendMessage',
                      json={'chat_id': chat_id, 'text': message, 'parse_mode': 'HTML'},
                      timeout=10)
    if r.status_code != 200:
        print(f'Telegram 發送失敗: {r.text}')
    else:
        print('週報已推送 TG')


def main():
    token = os.environ.get('TG_BOT_TOKEN', '')
    chat  = os.environ.get('TG_CHAT_ID', '')

    wl = get_watchlist()
    if not wl:
        print('自選股為空，略過週報')
        return
    print(f'自選股 {len(wl)} 檔')

    names = load_names()
    def label(sid):
        return f'{names.get(sid, sid)}({sid})'

    perf   = get_weekly_perf(wl)
    streak = get_inst_streak(wl)
    eps    = get_eps(wl)

    today = datetime.now().strftime('%Y-%m-%d')
    lines = [f'📊 <b>自選股週報</b>（{today}）', f'自選股 {len(wl)} 檔', '']

    # ① 週漲跌排行
    if perf:
        ranked = sorted(perf.items(), key=lambda x: x[1], reverse=True)
        ups = [x for x in ranked if x[1] > 0][:3]
        downs = [x for x in ranked if x[1] < 0][-3:][::-1]
        if ups:
            lines.append('📈 <b>本週漲幅</b>')
            for sid, p in ups:
                lines.append(f'　{label(sid)}　+{p:.1f}%')
        if downs:
            lines.append('📉 <b>本週跌幅</b>')
            for sid, p in downs:
                lines.append(f'　{label(sid)}　{p:.1f}%')
        lines.append('')

    # ② 法人連買（外資 / 投信連買 ≥ 2 天）
    fstreak = sorted([(t, s['foreign']) for t, s in streak.items() if s['foreign'] >= 2],
                     key=lambda x: x[1], reverse=True)
    tstreak = sorted([(t, s['trust']) for t, s in streak.items() if s['trust'] >= 2],
                     key=lambda x: x[1], reverse=True)
    if fstreak or tstreak:
        lines.append('🏦 <b>法人連買</b>')
        if fstreak:
            lines.append('　外資：' + '、'.join(f'{label(t)} {n}天' for t, n in fstreak))
        if tstreak:
            lines.append('　投信：' + '、'.join(f'{label(t)} {n}天' for t, n in tstreak))
        lines.append('')

    # ③ 高 EPS 標的（前 3）
    if eps:
        top_eps = sorted(eps.items(), key=lambda x: x[1], reverse=True)[:3]
        lines.append('⭐ <b>高 EPS（TTM）</b>')
        for sid, e in top_eps:
            lines.append(f'　{label(sid)}　{e:.1f} 元')

    message = '\n'.join(lines).rstrip()

    if not token or not chat:
        print('（未設 TG Secret，僅印出內容）\n' + '-' * 40)
        # 去 HTML 標籤方便本地檢視
        print(re.sub(r'</?b>', '', message))
        return
    send_telegram(token, chat, message)


if __name__ == '__main__':
    main()
