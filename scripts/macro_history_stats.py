#!/usr/bin/env python3
"""總經指標與那斯達克指數的歷史統計關聯 → data/macro_history_stats.json

這是「過去發生過什麼」的描述性統計，不是預測、不是投資建議、不保證未來
會重演。樣本數(n)都很小（幾十年只有個位數到十幾次事件），務必連同 n 一起看，
不要只看平均數。所有數字都來自 FRED 公開資料，不經 AI 生成或潤飾。

用那斯達克指數（NASDAQCOM，FRED 資料回溯到 1971 年）當市場代理指標，理由：
台股電子/半導體權重高，跟那斯達克的連動一般認為比標普500更貼近；FRED 的
SP500 序列只回溯到 2016 年，樣本太短做不了跨景氣循環統計。這不是說那斯達克
可以代表台股，只是找不到更貼近且夠長的免費歷史資料，此為明確假設。

三個分析：
1. 殖利率倒掛（10Y-2Y轉負）後，那斯達克未來 6/12/18 個月報酬
2. CPI年增率所在分位數，未來 3/6/12 個月報酬
3. Fed利率半年變動方向（升/降/持平），未來 6/12 個月報酬
"""
import csv, io, json, os, subprocess, sys
from datetime import date, datetime, timedelta, timezone

D = os.path.join(os.path.dirname(__file__), '..', 'data')
URL = 'https://fred.stlouisfed.org/graph/fredgraph.csv?id='


def fetch_full(sid):
    for i in range(3):
        try:
            p = subprocess.run(['curl', '-sf', '-m', '60', URL + sid], capture_output=True, timeout=90)
            if p.returncode != 0:
                raise RuntimeError(f'curl exit {p.returncode}')
            rows = list(csv.reader(io.StringIO(p.stdout.decode())))[1:]
            return [(d, float(v)) for d, v in rows if v not in ('', '.')]
        except Exception:
            if i == 2:
                raise


def price_on_or_after(series, target_date):
    """series 是 (date_str, value) 已按日期排序；回傳 target_date 當天或之後最接近的一筆"""
    for d, v in series:
        if d >= target_date:
            return d, v
    return None, None


def add_days(d_str, n):
    return (date.fromisoformat(d_str) + timedelta(days=n)).isoformat()


def add_months(d_str, n):
    d = date.fromisoformat(d_str)
    m0 = d.month - 1 + n
    y = d.year + m0 // 12
    m = m0 % 12 + 1
    day = min(d.day, 28)
    return date(y, m, day).isoformat()


def fwd_return(series, start_date, start_val, days_ahead):
    d2, v2 = price_on_or_after(series, add_days(start_date, days_ahead))
    if v2 is None:
        return None
    return round((v2 / start_val - 1) * 100, 2)


def stats(returns):
    vals = [r for r in returns if r is not None]
    n = len(vals)
    if n == 0:
        return {'n': 0, 'avg': None, 'median': None, 'win_rate': None}
    vals_sorted = sorted(vals)
    med = vals_sorted[n // 2] if n % 2 else (vals_sorted[n // 2 - 1] + vals_sorted[n // 2]) / 2
    return {'n': n, 'avg': round(sum(vals) / n, 2), 'median': round(med, 2),
            'win_rate': round(sum(1 for v in vals if v > 0) / n * 100, 1)}


def analysis_yield_inversion(t10y2y, ndx):
    """T10Y2Y 由 >=0 轉 <0 視為一次倒掛事件起點；180天內的重複轉負只算同一事件（避免同一輪循環重複計數）"""
    events = []
    prev = None
    last_event_date = None
    for d, v in t10y2y:
        if prev is not None and prev >= 0 and v < 0:
            if last_event_date is None or (date.fromisoformat(d) - date.fromisoformat(last_event_date)).days > 180:
                events.append(d)
                last_event_date = d
        prev = v
    rows = []
    for ev in events:
        d0, v0 = price_on_or_after(ndx, ev)
        if v0 is None:
            continue
        rows.append({'event_date': ev, 'ndx_start_date': d0, 'ndx_start': v0,
                     'ret_6m': fwd_return(ndx, d0, v0, 182), 'ret_12m': fwd_return(ndx, d0, v0, 365),
                     'ret_18m': fwd_return(ndx, d0, v0, 548)})
    return {'events': rows,
            'summary': {h: stats([r[f'ret_{h}'] for r in rows]) for h in ('6m', '12m', '18m')},
            'note': '事件=10Y-2Y利差由正轉負的起點，180天內視為同一輪不重複計。1971年以來那斯達克資料下的樣本。'}


def cpi_yoy_series(cpi):
    out = []
    for i in range(12, len(cpi)):
        d, v = cpi[i]
        prev = cpi[i - 12][1]
        if prev:
            out.append((d, round((v / prev - 1) * 100, 2)))
    return out


def analysis_cpi_quartile(cpi_yoy, ndx):
    vals = sorted(v for _, v in cpi_yoy)
    n = len(vals)
    q = [vals[int(n * p)] for p in (0.25, 0.5, 0.75)]

    def bucket(v):
        if v <= q[0]:
            return 'Q1(最低)'
        if v <= q[1]:
            return 'Q2'
        if v <= q[2]:
            return 'Q3'
        return 'Q4(最高)'
    groups = {}
    for d, v in cpi_yoy:
        d0, v0 = price_on_or_after(ndx, d)
        if v0 is None:
            continue
        b = bucket(v)
        groups.setdefault(b, []).append({'3m': fwd_return(ndx, d0, v0, 91), '6m': fwd_return(ndx, d0, v0, 182), '12m': fwd_return(ndx, d0, v0, 365)})
    summary = {}
    for b, rows in groups.items():
        summary[b] = {h: stats([r[h] for r in rows]) for h in ('3m', '6m', '12m')}
    return {'quartile_breakpoints': [round(x, 2) for x in q], 'summary': summary,
            'note': 'CPI年增率(CPIAUCSL)全歷史四分位分組，每月一筆觀察（月與月之間預測窗重疊，非獨立樣本，只供參考分布不做嚴謹統計推論）'}


def analysis_fed_trend(fedfunds, ndx):
    groups = {'升息(半年+0.5pp以上)': [], '降息(半年-0.5pp以上)': [], '持平': []}
    for i in range(6, len(fedfunds)):
        d, v = fedfunds[i]
        chg = v - fedfunds[i - 6][1]
        g = '升息(半年+0.5pp以上)' if chg >= 0.5 else ('降息(半年-0.5pp以上)' if chg <= -0.5 else '持平')
        d0, v0 = price_on_or_after(ndx, d)
        if v0 is None:
            continue
        groups[g].append({'6m': fwd_return(ndx, d0, v0, 182), '12m': fwd_return(ndx, d0, v0, 365)})
    summary = {g: {h: stats([r[h] for r in rows]) for h in ('6m', '12m')} for g, rows in groups.items()}
    return {'summary': summary, 'note': 'FEDFUNDS(有效聯邦資金利率)每月觀察，同上非獨立樣本，僅供參考分布'}


def analysis_current_reading(t10y2y, cpi_yoy, fedfunds, cpi_q, cpi_summary, fed_summary):
    """把「現在」的讀數對到剛算出的歷史分類，回傳歷史上同類別後來平均的樣子。
    這不是預測——只是說「現在的數字歷史上落在哪一類、那一類過去平均長怎樣」，
    要不要當作偏多/偏空的參考，由你自己判斷，且務必看 n（樣本數）夠不夠。"""
    t_date, t_val = t10y2y[-1]
    c_date, c_val = cpi_yoy[-1]
    f_date, f_val = fedfunds[-1]
    f_chg = f_val - fedfunds[-7][1] if len(fedfunds) > 6 else None

    def cpi_bucket(v):
        if v <= cpi_q[0]:
            return 'Q1(最低)'
        if v <= cpi_q[1]:
            return 'Q2'
        if v <= cpi_q[2]:
            return 'Q3'
        return 'Q4(最高)'

    fed_state = None
    if f_chg is not None:
        fed_state = '升息(半年+0.5pp以上)' if f_chg >= 0.5 else ('降息(半年-0.5pp以上)' if f_chg <= -0.5 else '持平')

    c_bucket = cpi_bucket(c_val)
    return {
        'yield_curve': {'date': t_date, 'value': t_val, 'inverted_now': t_val < 0},
        'cpi_yoy': {'date': c_date, 'value': c_val, 'bucket': c_bucket, 'history_of_this_bucket': cpi_summary.get(c_bucket)},
        'fed_6m_change': {'date': f_date, 'value_pp': round(f_chg, 2) if f_chg is not None else None,
                          'state': fed_state, 'history_of_this_state': fed_summary.get(fed_state) if fed_state else None},
        'how_to_read': '以上是「現在的數字」對照「歷史上同類情況後來平均的報酬分布」，不是對現在的預測。'
                       '樣本數(n)小、彼此不獨立、涵蓋數十年不同環境，僅供參考，不構成投資建議。'
    }


def main():
    t10y2y = fetch_full('T10Y2Y')
    ndx = fetch_full('NASDAQCOM')
    cpi = fetch_full('CPIAUCSL')
    fedfunds = fetch_full('FEDFUNDS')
    cpi_yoy = cpi_yoy_series(cpi)

    cpi_analysis = analysis_cpi_quartile(cpi_yoy, ndx)
    fed_analysis = analysis_fed_trend(fedfunds, ndx)

    out = {
        'generated': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'disclaimer': '描述性歷史統計，非預測、非投資建議。樣本數(n)都很小，過去發生過不代表未來會重演。'
                       '用那斯達克指數(NASDAQCOM)當市場代理，不是台股本身的歷史（見腳本開頭說明）。',
        'data_range': {'t10y2y': [t10y2y[0][0], t10y2y[-1][0]], 'nasdaq': [ndx[0][0], ndx[-1][0]],
                       'cpi': [cpi[0][0], cpi[-1][0]], 'fedfunds': [fedfunds[0][0], fedfunds[-1][0]]},
        'yield_curve_inversion': analysis_yield_inversion(t10y2y, ndx),
        'cpi_yoy_quartile': cpi_analysis,
        'fed_rate_trend': fed_analysis,
        'current_reading': analysis_current_reading(t10y2y, cpi_yoy, fedfunds,
                                                     cpi_analysis['quartile_breakpoints'], cpi_analysis['summary'], fed_analysis['summary']),
    }
    json.dump(out, open(os.path.join(D, 'macro_history_stats.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

    yi = out['yield_curve_inversion']
    print(f"殖利率倒掛事件：{len(yi['events'])} 次（{out['data_range']['t10y2y'][0]}~今）")
    for ev in yi['events']:
        print(f"  {ev['event_date']}  6m {ev['ret_6m']}%  12m {ev['ret_12m']}%  18m {ev['ret_18m']}%")
    print('  彙總:', yi['summary'])
    print('\nCPI分位數:', out['cpi_yoy_quartile']['quartile_breakpoints'])
    for b, s in out['cpi_yoy_quartile']['summary'].items():
        print(f"  {b}: {s}")
    print('\nFed趨勢:')
    for g, s in out['fed_rate_trend']['summary'].items():
        print(f"  {g}: {s}")
    cr = out['current_reading']
    print('\n現在的讀數對照歷史分類:')
    print(f"  10Y-2Y {cr['yield_curve']['date']} = {cr['yield_curve']['value']}（{'倒掛中' if cr['yield_curve']['inverted_now'] else '未倒掛'}）")
    print(f"  CPI年增 {cr['cpi_yoy']['date']} = {cr['cpi_yoy']['value']}% → {cr['cpi_yoy']['bucket']}，歷史此分位: {cr['cpi_yoy']['history_of_this_bucket']}")
    print(f"  Fed半年變動 {cr['fed_6m_change']['date']} = {cr['fed_6m_change']['value_pp']}pp → {cr['fed_6m_change']['state']}，歷史此狀態: {cr['fed_6m_change']['history_of_this_state']}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
