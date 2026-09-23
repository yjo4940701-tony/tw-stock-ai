#!/usr/bin/env python3
"""產生總經報告：完整 HTML（之後加密）＋ Telegram 短摘要（純文字）。

用法：macro_report.py <輸出目錄> [--slot am|pm]
輸出：<dir>/report.html（明文，只放暫存區，不可 commit）、<dir>/tg.txt
所有動態字串都 html.escape；數字全來自資料檔，不由 AI 生成。
不含任何持倉/自選股資訊。
"""
import html, json, os, sys
from datetime import datetime, timedelta, timezone

D = os.path.join(os.path.dirname(__file__), '..', 'data')
jl = lambda n: json.load(open(os.path.join(D, n), encoding='utf-8'))
e = html.escape
ARROW = {'up': '▲上升', 'down': '▼下降', 'flat': '－持平', 'mixed': '◆矛盾', 'n/a': '—'}


def fmt(v, suf=''):
    return '—' if v is None else f'{v:+.2f}{suf}' if suf == '%' else f'{v}{suf}'


def main():
    out_dir = sys.argv[1]
    slot = 'pm' if '--slot' in sys.argv and sys.argv[sys.argv.index('--slot') + 1] == 'pm' else 'am'
    os.makedirs(out_dir, exist_ok=True)
    reg, tg_, us, tw = jl('macro_regime.json'), jl('macro_targets.json'), jl('macro.json')['series'], jl('macro_tw.json')
    tpe = datetime.now(timezone(timedelta(hours=8)))
    stamp = tpe.strftime('%Y-%m-%d %H:%M')

    # ---------- HTML ----------
    h = [f'<h2>總經循環報告 <small>{e(stamp)}（台北）{"早報" if slot=="am" else "晚報"}</small></h2>',
         '<p class="mr-disc">框架推演與公開資料整理，非預測、非投資建議。門檻為假設值。</p>']
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

    h.append(f"<h3>階段對應 ETF（框架清單＋現況數據）</h3><p class='mr-disc'>價格至 {e(tg_['price_data_date'])}、法人至 {e(tg_['inst_data_date'])}</p>")
    for g, v in tg_['groups'].items():
        h.append(f"<h4>{e(g)}：{e(v['sectors'])}</h4><table><tr><th>ETF</th><th>類型</th><th>1月%</th><th>3月%</th><th>距MA60%</th><th>法人5日(張)</th><th>提醒</th></tr>")
        for x in v['etfs']:
            h.append(f"<tr><td>{e(x['id'])}</td><td>{e(x['type'])}</td><td>{fmt(x.get('ret_1m'))}</td><td>{fmt(x.get('ret_3m'))}</td><td>{fmt(x.get('vs_ma60_pct'))}</td><td>{x.get('inst_5d_total','—')}</td><td>{e(x['note'])}</td></tr>")
        h.append('</table>')
    for sid, x in tg_['mixed'].items():
        h.append(f"<p><b>{e(sid)}</b>：{e(x['note'])}｜1月 {fmt(x.get('ret_1m'))}%｜3月 {fmt(x.get('ret_3m'))}%</p>")
    h.append('<h4>限制</h4><ul>' + ''.join(f'<li>{e(c)}</li>' for c in tg_['caveats']) + '</ul>')
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
