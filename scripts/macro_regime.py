#!/usr/bin/env python3
"""讀 macro.json / macro_tw.json → 記分卡 → 美林階段 → data/macro_regime.json

判斷規則對應 macro-cycle-taiwan-etf 文件 §2/§6/§9。
原則：事實與判斷分開；缺資料標「無資料」不猜；門檻在 macro_thresholds.json（我的假設）。
台灣與美國分開判斷（台股看台灣，美國是全球背景）。
"""
import json, os, sys
from datetime import date, datetime, timezone

D = os.path.join(os.path.dirname(__file__), '..', 'data')
load = lambda n: json.load(open(os.path.join(D, n), encoding='utf-8'))

QUAD = {('up', 'up'): '過熱期', ('up', 'down'): '復甦期',
        ('down', 'down'): '衰退/寬鬆期', ('down', 'up'): '滯脹期'}


def age_days(s):
    s = s[:10] if len(s) >= 10 else s + '-28'  # 'YYYY-MM' 取月底附近，偏保守
    return (date.today() - date.fromisoformat(s)).days


def row(dim, fact, direction, phase, note=''):
    return {'dim': dim, 'fact': fact, 'dir': direction, 'phase': phase, 'note': note}


def infl_dir(yoy, prev, T):
    dlt = yoy - prev
    if dlt >= T['inflation']['rising_delta_pp']:
        return 'up'
    if dlt <= T['inflation']['falling_delta_pp']:
        return 'down'
    return 'flat'


def main():
    T, us, tw = load('macro_thresholds.json'), load('macro.json')['series'], load('macro_tw.json')
    a, m = tw['auto'], tw['manual']
    rows, warns, triggers = [], [], []

    # ---- 台灣成長 ----
    g = T['tw_growth']
    votes, facts = [], []
    if 'business_signal_score' in m:
        s = m['business_signal_score']['value']
        votes.append(s >= g['signal_hot']); facts.append(f'燈號{s}分')
    if 'pmi_manufacturing' in m:
        p = m['pmi_manufacturing']['value']
        votes.append(p >= g['pmi_strong']); facts.append(f'PMI {p}')
        if p < g['pmi_expand']:
            triggers.append('PMI 跌破 50（成長維度可能轉為下滑）')
    if 'export_orders_yoy' in a:
        v = a['export_orders_yoy']['value']
        votes.append(v >= g['export_orders_strong_yoy']); facts.append(f'外銷訂單{v}%')
    if 'ip_yoy' in a:
        v = a['ip_yoy']['value']
        votes.append(v >= g['ip_strong_yoy']); facts.append(f'工業生產{v}%')
    tw_g = 'up' if votes and sum(votes) * 2 >= len(votes) else 'down'
    rows.append(row('台灣成長', '、'.join(facts) or '無資料', tw_g,
                    '過熱/復甦（高於趨勢）' if tw_g == 'up' else '衰退/滯脹（低於趨勢）'))
    if 'business_signal_score' in m:
        s = m['business_signal_score']
        if s.get('prev') is not None and s['value'] < s['prev']:
            triggers.append('景氣燈號分數較前月下降')

    # ---- 台灣通膨 ----
    tw_i = None
    if 'cpi_yoy' in a:
        c = a['cpi_yoy']
        d = infl_dir(c['value'], c['prev'], T)
        hi = c['value'] > T['inflation']['target']
        # 水準與方向並列：兩者一致才定案，矛盾就標 mixed（不強行二選一）
        if d == 'flat':
            tw_i = 'up' if hi else 'down'
        elif (d == 'up') == hi:
            tw_i = d
        else:
            tw_i = 'mixed'
        rows.append(row('台灣通膨', f"CPI {c['value']}%（前 {c['prev']}%，{c['date']}）；水準{'高於' if hi else '低於'}{T['inflation']['target']}%目標、方向{ {'up':'上升','down':'回落','flat':'持平'}[d] }",
                        tw_i, {'up': '過熱/滯脹', 'down': '復甦/衰退', 'mixed': '水準與方向矛盾，介於兩階段'}[tw_i],
                        '單月 CPI 波動大，不強行二選一' if tw_i == 'mixed' else ''))
        if d == 'down' and c['value'] - c['prev'] <= -0.3:
            triggers.append('台灣 CPI 明顯回落')

    # ---- 台灣政策利率 ----
    if 'policy_rate' in a:
        r = a['policy_rate']
        chg = r['value'] - (r['prev'] if r['prev'] is not None else r['value'])
        rows.append(row('台灣政策利率', f"{r['value']}%（前 {r['prev']}%）", 'up' if chg > 0 else 'flat',
                        '過熱（升息）' if chg > 0 else '未進入緊縮', '文件 §6 標為與過熱不符項'))
        if chg != 0:
            triggers.append('台灣央行利率變動')

    # ---- 美國成長 / 通膨 / 利率 / 商品 ----
    ug = T['us_growth']
    u, p = us.get('UNRATE'), us.get('PAYEMS')
    us_g = None
    if u and 'error' not in u and p and 'error' not in p:
        d_u = u['value'] - u['prev']
        nfp = p['value'] - p['prev']
        weak = d_u >= ug['unemployment_rising_pp'] or nfp < ug['payrolls_weak_thousand']
        us_g = 'down' if weak else 'up'
        rows.append(row('美國成長', f"失業率{u['value']}%（前{u['prev']}%）、非農月增{nfp:.0f}千人", us_g,
                        '成長轉弱' if weak else '成長維持', '只用就業兩項，無 ISM/GDP，代表性有限'))
    us_i = None
    parts = []
    for sid, nm in (('CPIAUCSL', 'CPI'), ('PCEPILFE', '核心PCE')):
        s = us.get(sid)
        if s and 'yoy' in s:
            parts.append((nm, s['yoy'], s['yoy_prev'], infl_dir(s['yoy'], s['yoy_prev'], T)))
    if parts:
        ups = sum(1 for x in parts if x[3] == 'up')
        downs = sum(1 for x in parts if x[3] == 'down')
        us_i = 'up' if ups >= downs and (ups > 0 or all(x[1] > T['inflation']['target'] for x in parts)) else 'down'
        rows.append(row('美國通膨', '、'.join(f'{n} {y}%（前{pv}%）' for n, y, pv, _ in parts), us_i,
                        '通膨上行' if us_i == 'up' else '通膨回落'))
    ff = us.get('DFEDTARU')
    if ff and 'ago90' in ff:
        mv = ff['value'] - ff['ago90']
        rows.append(row('全球利率(Fed)', f"目標上限 {ff['value']}%，90 天前 {ff['ago90']}%", 'up' if mv >= T['global_rates']['fed_move_pp_90d'] else ('down' if mv <= -T['global_rates']['fed_move_pp_90d'] else 'flat'),
                        '過熱（升息）' if mv > 0 else '未升息'))
    oil = us.get('DCOILWTICO')
    if oil and 'ago90' in oil:
        pc = (oil['value'] / oil['ago90'] - 1) * 100
        rows.append(row('大宗商品(WTI)', f"{oil['value']}（90 天前 {oil['ago90']}，{pc:+.1f}%）",
                        'up' if pc >= T['commodities']['oil_up_pct_90d'] else ('down' if pc <= T['commodities']['oil_down_pct_90d'] else 'flat'),
                        '過熱/滯脹特徵' if pc > 0 else ''))
    rows.append(row('供給面', '無自動資料（文件僅有 PMI 細項的新聞轉述）', 'n/a', '未納入判斷'))

    # ---- 階段 ----
    def regime(g, i):
        if g is None or i is None:
            return '資料不足'
        if i == 'mixed':
            return f"{QUAD[(g, 'up')]}／{QUAD[(g, 'down')]}交界"
        return QUAD[(g, i)]
    reg = {'台灣': regime(tw_g, tw_i), '美國': regime(us_g, us_i)}

    # ---- 資料新鮮度 ----
    # 手動項目：估計「下一筆新資料」的公布日，今天已過還沒更新就提醒（比單純算天數準）
    # 現有資料是「X月的活動」，公布於 X+1 月（如7月資料於8月27日公布）。
    # 下一筆是「X+1月的活動」，故公布於 X+2 月：先 +1 月拿到下一期期別，再 +1 月拿到公布月。
    def add_months(d, n):
        m0 = d.month - 1 + n
        return date(d.year + m0 // 12, m0 % 12 + 1, 1)
    NEXT_PUBLISH_DAY = {'business_signal_score': 27, 'pmi_manufacturing': 3}
    for k, v in m.items():
        cur_month = date.fromisoformat((v['date'] if len(v['date']) == 7 else v['date'][:7]) + '-01')
        next_period = add_months(cur_month, 1)   # 下一期資料的期別（如 8月）
        publish_month = add_months(cur_month, 2)  # 下一期資料的公布月（如 9月）
        due_day = NEXT_PUBLISH_DAY.get(k, 27)
        due = date(publish_month.year, publish_month.month, min(due_day, 28))
        if date.today() >= due:
            warns.append(f"手動資料應已有新一期：{v['name']}（現有 {v['date']}，預估公布日 {due.isoformat()} 已過，請更新 data/macro_tw_manual.json）")
        elif age_days(v.get('published') or v['date']) > T['stale_days']['manual']:
            warns.append(f"手動資料過期：{v['name']}（{v['date']}，公布 {v.get('published','?')}）")
    for k, v in a.items():
        if age_days(v['date']) > T["stale_days"]["monthly"]:
            warns.append(f"自動資料偏舊：{v['name']}（{v['date']}）")
    for sid, s in us.items():
        if 'date' in s and age_days(s['date']) > (T['stale_days']['daily'] if s['freq'] == 'D' else T['stale_days']['monthly']):
            warns.append(f"美國資料偏舊：{s['name']}（{s['date']}）")
        if 'error' in s:
            warns.append(f"抓取失敗：{s['name']}")

    out = {'generated': datetime.now(timezone.utc).isoformat(timespec='seconds'),
           'regime': reg, 'scorecard': rows, 'triggers': triggers, 'warnings': warns,
           'growth_dir': {'tw': tw_g, 'us': us_g}, 'inflation_dir': {'tw': tw_i, 'us': us_i},
           'disclaimer': '框架推演，非預測或投資建議；門檻為假設值，見 macro_thresholds.json'}
    json.dump(out, open(os.path.join(D, 'macro_regime.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('階段:', reg)
    for r in rows:
        print(f" {r['dim']:<12}{r['dir']:<5}{r['fact']}")
    print('觸發:', triggers or '無'); print('警示:', warns or '無')


if __name__ == '__main__':
    sys.exit(main())
