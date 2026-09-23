#!/usr/bin/env python3
"""macro_regime.json + ETF 對照表 + 全市場日K/三大法人 → data/macro_targets.json

只做「階段對應的框架清單 + 現況驗證數據」，不給買賣建議。
「交界」階段分別列出兩個階段，不合併。
"""
import gzip, json, os, sys
from datetime import datetime, timezone

D = os.path.join(os.path.dirname(__file__), '..', 'data')
jl = lambda n: json.load(open(os.path.join(D, n), encoding='utf-8'))


def ret(c, n):
    return round((c[-1] / c[-1 - n] - 1) * 100, 2) if len(c) > n and c[-1 - n] else None


def sma(c, n):
    return sum(c[-n:]) / n if len(c) >= n else None


def main():
    reg, mp = jl('macro_regime.json'), jl('macro_etf_map.json')
    oh = json.load(gzip.open(os.path.join(D, 'ohlcv.json.gz')))
    inst = jl('institutional.json')
    dates = oh['dates']

    def metrics(sid):
        s = oh['stocks'].get(sid)
        if not s:
            return {'error': '日K無此代號'}
        c = [x for x in s['c'] if x is not None]
        m60 = sma(c, 60)
        r = {'close': c[-1], 'ret_1m': ret(c, 21), 'ret_3m': ret(c, 63),
             'vs_ma60_pct': round((c[-1] / m60 - 1) * 100, 2) if m60 else None,
             'ma20_gt_ma60': (sma(c, 20) > m60) if m60 else None}
        rows = inst['stocks'].get(sid)
        if rows:
            r['inst_5d'] = {k: sum(x[i] for x in rows[:5]) for i, k in enumerate(['外資', '投信', '自營'])}
            r['inst_5d_total'] = sum(r['inst_5d'].values())
        return r

    out = {'generated': datetime.now(timezone.utc).isoformat(timespec='seconds'),
           'price_data_date': dates[-1], 'inst_data_date': inst['dates'][0],
           'regime': reg['regime'], 'groups': {}, 'mixed': {},
           'caveats': ['價格為未還原權息價，高股息/債券 ETF 的報酬會因除息偏低',
                       '法人單位：張，取近 5 個交易日合計',
                       'ETF 代號僅為框架舉例，持股需看投信官網最新資料',
                       '此為階段對應的框架清單與現況數據，不是買賣建議']}
    for label, txt in reg['regime'].items():
        stages = [s for s in txt.replace('交界', '').split('／') if s in mp['stages']]
        for st in stages:
            cfg = mp['stages'][st]
            items = []
            for sid, typ in cfg['etfs'].items():
                items.append({'id': sid, 'type': typ, 'note': mp['notes_per_type'].get(typ, ''), **metrics(sid)})
            items.sort(key=lambda x: (x.get('ret_1m') is None, -(x.get('ret_1m') or 0)))
            out['groups'][f'{label}·{st}'] = {'sectors': cfg['sectors'], 'etfs': items}
    for sid, note in mp['mixed_etfs'].items():
        out['mixed'][sid] = {'note': note, **metrics(sid)}
    json.dump(out, open(os.path.join(D, 'macro_targets.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('價格資料', dates[-1], '法人資料', inst['dates'][0])
    for g, v in out['groups'].items():
        print(f"\n[{g}] {v['sectors']}")
        for e in v['etfs']:
            print(f"  {e['id']:<7}{e['type']:<8} 1M {e.get('ret_1m')}% 3M {e.get('ret_3m')}% 距MA60 {e.get('vs_ma60_pct')}% 法人5日 {e.get('inst_5d_total','-')}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
