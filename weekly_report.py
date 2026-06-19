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
    """中文股名對照：優先抓 FinMind TaiwanStockInfo（全市場、匿名免費），
    再以 index.html 的 STOCK_NAMES 補強 / 當 FinMind 失敗時的後備。"""
    names = {}
    # 1) index.html STOCK_NAMES 為主（人工維護、名稱乾淨，如 2327→國巨 不帶星號）
    try:
        with open(HTML_FILE, encoding='utf-8') as f:
            html = f.read()
        m = re.search(r'var STOCK_NAMES\s*=\s*\{(.*?)\n\};', html, re.S)
        if m:
            for sid, name in re.findall(r"'([0-9A-Za-z]{4,6})'\s*:\s*'([^']+)'", m.group(1)):
                names[sid] = name
    except Exception as e:
        print(f'解析 STOCK_NAMES 失敗: {e}')
    # 2) FinMind TaiwanStockInfo 補上 STOCK_NAMES 沒有的冷門股（前端 ensureStockInfo 同一 dataset，匿名 GET）
    #    FinMind 名稱偶有尾綴 *（特殊交易註記），去掉避免出現「群創*」這種雜訊
    try:
        r = requests.get('https://api.finmindtrade.com/api/v4/data',
                         params={'dataset': 'TaiwanStockInfo'}, timeout=30)
        added = 0
        for row in (r.json().get('data') or []):
            sid = str(row.get('stock_id') or '').strip()
            nm  = str(row.get('stock_name') or '').strip().rstrip('*').strip()
            if sid and nm and sid not in names:
                names[sid] = nm
                added += 1
        print(f'FinMind 補股名：+{added} 檔（總 {len(names)}）')
    except Exception as e:
        print(f'FinMind 股名載入失敗（僅用 STOCK_NAMES）: {e}')
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


def ai_summary(data_text):
    """把週報三段數據丟給 AI，回 1-2 句『本週持股重點』。
    Key 從環境變數 AI_API_KEY（備用 AI_API_KEY2）讀，自動判斷 Groq(gsk_)/Gemini(AIza)。
    無 Key 或失敗 → 回 None，週報照常出（AI 只是加值，不可拖垮主流程）。"""
    key  = os.environ.get('AI_API_KEY', '').strip()
    key2 = os.environ.get('AI_API_KEY2', '').strip()
    if not key:
        print('未設 AI_API_KEY，略過 AI 總結')
        return None
    if not data_text.strip():
        return None

    prompt = (
        '你是台股助理。以下是某使用者自選股「本週數據摘要」（漲跌排行 / 法人連買 / 高 EPS）。\n'
        '請用繁體中文寫「本週持股重點」，最多 2 句、80 字內，客觀點出值得留意的方向（誰強誰弱、'
        '哪檔有法人或基本面撐腰）。不要喊買賣、不要給目標價、不要逐檔複述數字。直接給結論句，不要前綴。\n\n'
        + data_text
    )

    def call(k):
        is_g = k.startswith('AIza')
        if is_g:
            u = ('https://generativelanguage.googleapis.com/v1beta/models/'
                 'gemini-2.5-flash-lite:generateContent?key=' + k)
            r = requests.post(u, json={'contents': [{'parts': [{'text': prompt}]}],
                              'generationConfig': {'temperature': 0.6, 'maxOutputTokens': 256}}, timeout=30)
            r.raise_for_status()
            return r.json()['candidates'][0]['content']['parts'][0]['text']
        r = requests.post('https://api.groq.com/openai/v1/chat/completions',
                          headers={'Authorization': 'Bearer ' + k},
                          json={'model': 'llama-3.3-70b-versatile',
                                'messages': [{'role': 'user', 'content': prompt}],
                                'temperature': 0.6, 'max_tokens': 256}, timeout=30)
        r.raise_for_status()
        return r.json()['choices'][0]['message']['content']

    for k, label in [(key, '主要'), (key2, '備用')]:
        if not k:
            continue
        try:
            txt = call(k).strip().replace('\n', ' ')
            print(f'AI 總結（{label} Key）完成')
            return txt
        except Exception as e:
            print(f'AI 總結（{label} Key）失敗: {e}')
    return None


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

    # ④ AI 一句話總結 → 插在最上方當開場（用前三段數據生成；無 AI_API_KEY 則略過）
    data_text = re.sub(r'</?b>', '', '\n'.join(lines[3:]).strip())
    summary = ai_summary(data_text)
    if summary:
        lines[3:3] = ['🤖 <b>本週重點</b>', f'　{summary}', '']

    message = '\n'.join(lines).rstrip()

    if not token or not chat:
        print('（未設 TG Secret，僅印出內容）\n' + '-' * 40)
        # 去 HTML 標籤方便本地檢視
        print(re.sub(r'</?b>', '', message))
        return
    send_telegram(token, chat, message)


if __name__ == '__main__':
    main()
