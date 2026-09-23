#!/usr/bin/env python3
"""macro_regime.json + etf_universe.json（全市場分類）+ 全市場日K/三大法人
   → data/macro_targets.json

階段對應的是「候選類別」（見 macro_etf_map.json stage_categories），每類別動態
選出近 1 個月動能最強的前 N 檔（不是固定清單）。只做「現況數據」，不給買賣建議。
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
    uni = jl('etf_universe.json')
    oh = json.load(gzip.open(os.path.join(D, 'ohlcv.json.gz')))
    inst = jl('institutional.json')
    dates = oh['dates']
    min_days = mp['min_history_days']

    def metrics(sid):
        s = oh['stocks'].get(sid)
        if not s:
            return None
        c = [x for x in s['c'] if x is not None]
        if len(c) < min_days:
            return None  # 上市未滿門檻天數，資料太短不列入排名
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
           'universe_count': uni['count'], 'regime': reg['regime'], 'groups': {}, 'mixed': {},
           'caveats': ['價格為未還原權息價，高股息/債券 ETF 的報酬會因除息偏低',
                       '法人單位：張，取近 5 個交易日合計',
                       f"候選類別來自全市場 {uni['count']} 檔 ETF 用名稱關鍵字自動分類，非官方分類，可能誤判（見 etf_universe.json）",
                       '各類別內取近 1 個月報酬前 5 名，動能強不代表未來仍強，也不代表持股品質',
                       '此為階段對應的框架清單與現況數據，不是買賣建議']}

    for label, txt in reg['regime'].items():
        stages = [s for s in txt.replace('交界', '').split('／') if s in mp['stage_categories']]
        for st in stages:
            cats = mp['stage_categories'][st]
            pool = []
            for cat in cats:
                for sid in uni['by_category'].get(cat, []):
                    m = metrics(sid)
                    if m is None:
                        continue
                    note = mp['notes_per_category'].get(cat, '')
                    if sid in mp['mixed_etfs']:
                        note = (note + '；' if note else '') + mp['mixed_etfs'][sid]
                    pool.append({'id': sid, 'name': uni['etfs'][sid]['name'], 'category': cat, 'note': note, **m})
            pool.sort(key=lambda x: (x.get('ret_1m') is None, -(x.get('ret_1m') or -1e9)))
            top = pool[:mp['top_n']]
            out['groups'][f'{label}·{st}'] = {'sectors': '、'.join(cats), 'candidate_pool_size': len(pool), 'etfs': top}

    json.dump(out, open(os.path.join(D, 'macro_targets.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('價格資料', dates[-1], '法人資料', inst['dates'][0], '全市場ETF', uni['count'])
    for g, v in out['groups'].items():
        print(f"\n[{g}] 候選類別：{v['sectors']}（池 {v['candidate_pool_size']} 檔）")
        for e in v['etfs']:
            print(f"  {e['id']:<7}{e['name'][:12]:<14}{e['category']:<10} 1M {e.get('ret_1m')}% 3M {e.get('ret_3m')}% 距MA60 {e.get('vs_ma60_pct')}% 法人5日 {e.get('inst_5d_total','-')}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
