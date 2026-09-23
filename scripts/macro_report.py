#!/usr/bin/env python3
"""產生總經報告：完整 HTML（之後加密）＋ Telegram 短摘要（純文字）。

用法：macro_report.py <輸出目錄> [--slot am|pm]
輸出：<dir>/report.html（明文，只放暫存區，不可 commit）、<dir>/tg.txt
所有數字全來自資料檔，不由 AI 生成；AI 只負責把「已經算好的事實」改寫成白話，
prompt 明講不可預測漲跌、不可給買賣建議、不可捏造數字。無 AI_API_KEY 時整段略過，
報告其餘部分照常（AI 只是加值，不可拖垮主流程，同 weekly_report.py 的原則）。
不含任何持倉/自選股資訊。
"""
import html, json, os, sys, time
from datetime import datetime, timedelta, timezone

try:
    import requests
except ImportError:
    requests = None

D = os.path.join(os.path.dirname(__file__), '..', 'data')
jl = lambda n: json.load(open(os.path.join(D, n), encoding='utf-8'))
e = html.escape
ARROW = {'up': '▲上升', 'down': '▼下降', 'flat': '－持平', 'mixed': '◆矛盾', 'n/a': '—'}


def fmt(v, suf=''):
    return '—' if v is None else f'{v:+.2f}{suf}' if suf == '%' else f'{v}{suf}'


def ai_summary(data_text):
    """把已算好的事實丟給 AI 改寫成白話，禁止預測漲跌/給買賣建議/捏造數字。
    Key 從環境變數 AI_API_KEY（備用 AI_API_KEY2）讀，與 weekly_report.py 同一套邏輯。
    無 Key 或失敗 → 回 None，報告照常出。"""
    key = os.environ.get('AI_API_KEY', '').strip()
    key2 = os.environ.get('AI_API_KEY2', '').strip()
    if not key or requests is None or not data_text.strip():
        print('未設 AI_API_KEY 或缺 requests，略過 AI 白話摘要')
        return None

    prompt = (
        '你是總經框架整理助理。以下是已經算好的事實數據（階段判斷、記分卡、ETF動能、'
        '歷史統計對照），請用繁體中文寫「白話摘要」，3-4句、200字內。\n'
        '規則（違反任一條就是錯的輸出）：\n'
        '1. 只能改寫我提供的數字和結論，不能新增、估算或猜測任何我沒給的數字。\n'
        '2. 不能預測接下來會漲會跌，不能說「建議買/賣」「應該加碼/減碼」，不能給目標價。\n'
        '3. 歷史統計是「過去發生過什麼」，要維持「歷史上」「過去」這種語氣，不能講成對未來的斷言。\n'
        '4. 直接給摘要內容，不要前綴（不要寫「以下是摘要」之類）。\n\n'
        + data_text
    )
    gemini_models = ['gemini-2.5-flash-lite', 'gemini-2.5-flash', 'gemini-2.0-flash', 'gemini-1.5-flash']

    def call(k):
        is_g = not k.startswith('gsk_')
        if is_g:
            last = None
            for model in gemini_models:
                u = (f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key=' + k)
                r = requests.post(u, json={'contents': [{'parts': [{'text': prompt}]}],
                                  'generationConfig': {'temperature': 0.4, 'maxOutputTokens': 400}}, timeout=30)
                if r.status_code in (404, 429, 500, 503):
                    last = f'{model}→HTTP {r.status_code}'
                    continue
                r.raise_for_status()
                return r.json()['candidates'][0]['content']['parts'][0]['text']
            raise RuntimeError(f'所有 Gemini 模型皆不可用（{last}）')
        r = requests.post('https://api.groq.com/openai/v1/chat/completions',
                          headers={'Authorization': 'Bearer ' + k},
                          json={'model': 'openai/gpt-oss-120b',
                                'messages': [{'role': 'user', 'content': prompt}],
                                'temperature': 0.4, 'max_tokens': 400}, timeout=30)
        r.raise_for_status()
        return r.json()['choices'][0]['message']['content']

    for k, label in [(key, '主要'), (key2, '備用')]:
        if not k:
            continue
        for attempt in range(1, 4):
            try:
                txt = call(k).strip().replace('\n', ' ')
                print(f'AI 白話摘要（{label} Key，第 {attempt} 次）完成')
                return txt
            except Exception as ex:
                print(f'AI 白話摘要（{label} Key，第 {attempt} 次）失敗: {ex}')
                if attempt < 3:
                    time.sleep(3)
    return None


def main():
    out_dir = sys.argv[1]
    slot = 'pm' if '--slot' in sys.argv and sys.argv[sys.argv.index('--slot') + 1] == 'pm' else 'am'
    os.makedirs(out_dir, exist_ok=True)
    reg, tg_, us, tw = jl('macro_regime.json'), jl('macro_targets.json'), jl('macro.json')['series'], jl('macro_tw.json')
    hist = None
    try:
        hist = jl('macro_history_stats.json')
    except FileNotFoundError:
        print('macro_history_stats.json 不存在，略過歷史統計區塊')
    tpe = datetime.now(timezone(timedelta(hours=8)))
    stamp = tpe.strftime('%Y-%m-%d %H:%M')

    # ---------- HTML ----------
    h = [f'<h2>總經循環報告 <small>{e(stamp)}（台北）{"早報" if slot=="am" else "晚報"}</small></h2>',
         '<p class="mr-disc">框架推演與公開資料整理，非預測、非投資建議。門檻為假設值。</p>']

    ai_text = None
    if requests is not None:
        parts = [f"階段：{reg['regime']}"]
        parts += [f"{r['dim']} {r['dir']} {r['fact']}" for r in reg['scorecard']]
        if reg['triggers']:
            parts.append('觸發：' + '；'.join(reg['triggers']))
        for g, v in tg_['groups'].items():
            if v['etfs']:
                top3 = '、'.join(f"{x['id']}({x['name']}){fmt(x.get('ret_1m'),'%')}" for x in v['etfs'][:3])
                parts.append(f'{g} 候選前3：{top3}')
        if hist:
            cr = hist['current_reading']
            parts.append(f"10Y-2Y利差 {cr['yield_curve']['value']}（{'倒掛中' if cr['yield_curve']['inverted_now'] else '未倒掛'}）")
            parts.append(f"CPI年增分位 {cr['cpi_yoy']['bucket']}，歷史此分位12個月後平均 {cr['cpi_yoy']['history_of_this_bucket']['12m']}")
            if cr['fed_6m_change']['state']:
                parts.append(f"Fed半年變動狀態 {cr['fed_6m_change']['state']}，歷史此狀態12個月後平均 {cr['fed_6m_change']['history_of_this_state']['12m']}")
        ai_text = ai_summary('\n'.join(parts))
    if ai_text:
        h.append(f'<div class="mr-warn"><b>🤖 AI 白話摘要</b><p>{e(ai_text)}</p><p class="mr-disc">AI 僅改寫已算好的事實，不預測漲跌、不給買賣建議，數字皆來自下方各節。</p></div>')

    h.append('<h3>階段判斷</h3><ul>' + ''.join(f'<li><b>{e(k)}</b>：{e(v)}</li>' for k, v in reg['regime'].items()) + '</ul>')
    if reg['triggers']:
        h.append('<div class="mr-warn"><b>需重新檢視（§9 觸發）</b><ul>' + ''.join(f'<li>{e(t)}</li>' for t in reg['triggers']) + '</ul></div>')
    if reg['warnings']:
        h.append('<div class="mr-warn"><b>資料警示</b><ul>' + ''.join(f'<li>{e(t)}</li>' for t in reg['warnings']) + '</ul></div>')
    h.append('<h3>記分卡</h3><table><tr><th>維度</th><th>方向</th><th>事實</th><th>備註</th></tr>' + ''.join(
        f"<tr><td>{e(r['dim'])}</td><td>{ARROW.get(r['dir'], e(r['dir']))}</td><td>{e(r['fact'])}</td><td>{e(r['note'])}</td></tr>" for r in reg['scorecard']) + '</table>')

    h.append('<h3>美國指標</h3><table><tr><th>指標</th><th>最新</th><th>年增</th><th>資料日期</th></tr>')
    for sid, s in us.items():
        if 'error' in s:
            h.append(f"<tr><td>{e(s['name'])}</td><td colspan=3>抓取失敗</td></tr>")
        else:
            h.append(f"<tr><td>{e(s['name'])}</td><td>{e(str(s['value']))}</td><td>{fmt(s.get('yoy'), '%') if 'yoy' in s else '—'}</td><td>{e(s['date'])}</td></tr>")
    h.append('</table><h3>台灣指標</h3><table><tr><th>指標</th><th>最新</th><th>前值</th><th>日期</th><th>來源</th></tr>')
    for grp in ('auto', 'manual'):
        for k, v in tw[grp].items():
            h.append(f"<tr><td>{e(v['name'])}</td><td>{e(str(v['value']))}</td><td>{e(str(v.get('prev','—')))}</td><td>{e(v['date'])}</td><td>{e(v['source'][:40])}{'（手動）' if grp=='manual' else ''}</td></tr>")
    h.append('</table>')

    h.append(f"<h3>階段對應 ETF（全市場動態選股，非固定清單）</h3><p class='mr-disc'>候選共 {tg_['universe_count']} 檔全市場 ETF；價格至 {e(tg_['price_data_date'])}、法人至 {e(tg_['inst_data_date'])}</p>")
    for g, v in tg_['groups'].items():
        h.append(f"<h4>{e(g)}：{e(v['sectors'])}（候選池 {v['candidate_pool_size']} 檔，取近1月動能前 {len(v['etfs'])} 名）</h4><table><tr><th>ETF</th><th>名稱</th><th>類別</th><th>1月%</th><th>3月%</th><th>距MA60%</th><th>法人5日(張)</th><th>提醒</th></tr>")
        for x in v['etfs']:
            h.append(f"<tr><td>{e(x['id'])}</td><td>{e(x['name'])}</td><td>{e(x['category'])}</td><td>{fmt(x.get('ret_1m'))}</td><td>{fmt(x.get('ret_3m'))}</td><td>{fmt(x.get('vs_ma60_pct'))}</td><td>{x.get('inst_5d_total','—')}</td><td>{e(x['note'])}</td></tr>")
        h.append('</table>')
    h.append('<h4>限制</h4><ul>' + ''.join(f'<li>{e(c)}</li>' for c in tg_['caveats']) + '</ul>')

    if hist:
        h.append('<h3>歷史統計對照（描述性統計，非預測）</h3>')
        h.append(f"<p class='mr-disc'>{e(hist['disclaimer'])}</p>")
        cr = hist['current_reading']
        h.append('<h4>現在的讀數 → 對照歷史上同類情況後來的樣子</h4><table><tr><th>指標</th><th>現在</th><th>歷史分類</th><th>3月後</th><th>6月後</th><th>12月後</th><th>樣本數</th></tr>')
        cb, cbh = cr['cpi_yoy']['bucket'], cr['cpi_yoy']['history_of_this_bucket']
        h.append(f"<tr><td>CPI年增分位</td><td>{fmt(cr['cpi_yoy']['value'],'%')}</td><td>{e(cb)}</td>"
                  f"<td>{fmt(cbh['3m'].get('avg'),'%')}(勝率{cbh['3m'].get('win_rate')}%)</td>"
                  f"<td>{fmt(cbh['6m'].get('avg'),'%')}(勝率{cbh['6m'].get('win_rate')}%)</td>"
                  f"<td>{fmt(cbh['12m'].get('avg'),'%')}(勝率{cbh['12m'].get('win_rate')}%)</td><td>{cbh['3m'].get('n')}</td></tr>")
        if cr['fed_6m_change']['state']:
            fs, fsh = cr['fed_6m_change']['state'], cr['fed_6m_change']['history_of_this_state']
            h.append(f"<tr><td>Fed半年變動</td><td>{fmt(cr['fed_6m_change']['value_pp'])}pp</td><td>{e(fs)}</td>"
                      f"<td>—</td><td>{fmt(fsh['6m'].get('avg'),'%')}(勝率{fsh['6m'].get('win_rate')}%)</td>"
                      f"<td>{fmt(fsh['12m'].get('avg'),'%')}(勝率{fsh['12m'].get('win_rate')}%)</td><td>{fsh['6m'].get('n')}</td></tr>")
        h.append(f"<tr><td>10Y-2Y殖利率利差</td><td>{fmt(cr['yield_curve']['value'])}</td><td>{'倒掛中' if cr['yield_curve']['inverted_now'] else '未倒掛'}</td><td colspan=4>{'目前倒掛，見下方倒掛事件表' if cr['yield_curve']['inverted_now'] else '目前未倒掛，倒掛統計不適用'}</td><td>—</td></tr>")
        h.append('</table>')
        yi = hist['yield_curve_inversion']
        h.append(f"<h4>殖利率倒掛事件全歷史（{e(hist['data_range']['t10y2y'][0])}~今，共 {len(yi['events'])} 次）</h4><table><tr><th>倒掛起點</th><th>6月後</th><th>12月後</th><th>18月後</th></tr>")
        for ev in yi['events']:
            h.append(f"<tr><td>{e(ev['event_date'])}</td><td>{fmt(ev.get('ret_6m'),'%')}</td><td>{fmt(ev.get('ret_12m'),'%')}</td><td>{fmt(ev.get('ret_18m'),'%')}</td></tr>")
        h.append(f"</table><p class='mr-disc'>彙總：6月後平均{fmt(yi['summary']['6m'].get('avg'),'%')}(n={yi['summary']['6m'].get('n')})、"
                  f"12月後平均{fmt(yi['summary']['12m'].get('avg'),'%')}(n={yi['summary']['12m'].get('n')})、"
                  f"18月後平均{fmt(yi['summary']['18m'].get('avg'),'%')}(n={yi['summary']['18m'].get('n')})。{e(yi['note'])}</p>")
        h.append(f"<p class='mr-disc'>{e(cr['how_to_read'])}｜市場代理為那斯達克指數(NASDAQCOM)，不是台股本身的歷史，見腳本說明。</p>")

    open(os.path.join(out_dir, 'report.html'), 'w', encoding='utf-8').write('\n'.join(h))

    # ---------- Telegram 短摘要 ----------
    L = [f"🌐 總經{'早' if slot=='am' else '晚'}報 {stamp}"]
    L += [f'{k}：{v}' for k, v in reg['regime'].items()]
    key = {r['dim']: r for r in reg['scorecard']}
    for d in ('台灣成長', '台灣通膨', '美國通膨', '大宗商品(WTI)'):
        if d in key:
            L.append(f"• {d} {ARROW.get(key[d]['dir'],'')} {key[d]['fact'][:48]}")
    for t in reg['triggers']:
        L.append(f'⚠ {t}')
    for w in reg['warnings'][:3]:
        L.append(f'🕒 {w}')
    top = []
    for g, v in tg_['groups'].items():
        if v['etfs'] and v['etfs'][0].get('ret_1m') is not None:
            top.append(f"{g.split('·')[1]}:{v['etfs'][0]['id']}({v['etfs'][0]['ret_1m']:+.1f}%/月)")
    if top:
        L.append('動能領先 ' + '、'.join(dict.fromkeys(top)))
    L.append('完整報告需密碼（儀表板→🌐總經）。框架推演，非投資建議。')
    open(os.path.join(out_dir, 'tg.txt'), 'w', encoding='utf-8').write('\n'.join(L))
    print('\n'.join(L))


if __name__ == '__main__':
    main()
