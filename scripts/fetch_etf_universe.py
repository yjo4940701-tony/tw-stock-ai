#!/usr/bin/env python3
"""抓全市場 ETF 清單（TWSE + TPEx）→ 用名稱關鍵字規則分類 → data/etf_universe.json

分類規則寫死在 RULES，依關鍵字命中順序判斷（越前面優先權越高），全部是我
（Claude）用 ETF 中文名稱的慣用命名法歸類，不是官方分類，可能有誤判/漏判，
每檔都保留原始名稱，使用前應自行核對。目的是給 macro_targets 更大的候選池，
不是投資建議或完整盡職調查。
"""
import json, os, subprocess, sys
from datetime import datetime, timezone

D = os.path.join(os.path.dirname(__file__), '..', 'data')

RULES = [
    ('槓桿反向', ['正2', '反1', '正1']),
    ('債券', ['債', 'B', 'DEBT']),  # 名稱含「債」；代號含B在後面用後綴規則另判
    ('原油', ['原油', '石油']),
    ('黃金', ['黃金']),
    ('大宗商品', ['商品', '農產', '棕櫚']),
    ('貨幣市場', ['貨幣市場', '債券ETF']),
    ('高股息', ['高股息', '高息', '優息', '存股', '股利精選', '填息', '收息']),
    ('金融', ['金融', 'MSCI金融']),
    ('半導體', ['半導體', '晶片', 'IC設計', '晶圓']),
    ('主題科技(電動車/AI等)', ['電動車', '未來車', '供應鏈', '元宇宙', '太空', '衛星', '數位支付', '基因免疫', '網路資安', '電池', '儲能', '潔淨能源']),
    ('科技(台股)', ['科技', '電子', 'AI', '資訊', '5G', '創新']),
    ('生技', ['生技', '醫療']),
    ('中小型', ['中小', '中型']),
    ('主動式', ['主動']),
    ('市值型(大盤)', ['台灣50', '台50', '富櫃50', '台灣', '加權', '上証', '上證', '滬深', '摩台', '寶滬深', '深100',
                     '龍頭', '動能50', '精彩50', 'ESG永續', '智慧50', '低碳50', '優選30', 'ESG 30', 'ESG30']),
    ('國際/區域型', ['美國', '日本', '中國', '歐洲', '印度', '越南', '全球', '世界', 'S&P', '標普', '道瓊', '日經', '恒生',
                    '中証', '中証500', 'A股', '新興市場', 'KOSPI']),
    ('綠能/ESG', ['ESG', '綠能', '綠色電力', '淨零']),
    ('產業其他', ['資源', '基建', '不動產', 'REIT', '運輸', '公司治理', '平衡']),
]


# 名稱同時含「地區/海外市場字眼」與「科技類字眼」→ 海外科技（不管台股，跟台股總經循環關聯低）
# 例：元大全球AI、華南永昌NASDAQxT、國泰臺韓科技、復華中國5G
REGION_WORDS = ['美國', '全球', '中國', '日本', '北美', '臺韓', '韓國',
                '歐洲', '印度', '越南', '道瓊', '標普', 'S&P', 'KOSPI']
TECH_HINTS = ['科技', 'AI', '5G', '創新', '機器人', '新經濟']
# 這些名稱本身就代表科技（美股科技指數/重倉股），不需再搭配別的科技字眼
ALWAYS_OVERSEAS_TECH = ['NASDAQ', '那斯達克', '納斯達克', 'FANG', 'ARK']


def classify(name, code):
    if code.endswith('B'):
        return '債券'
    if code.endswith('U'):
        if '油' in name or '原油' in name:
            return '原油'
        if '金' in name:
            return '黃金'
        return '期貨商品'
    if code.endswith('L'):
        return '槓桿反向'
    if code.endswith('R'):
        return '槓桿反向'
    if any(k in name for k in ALWAYS_OVERSEAS_TECH):
        return '海外科技'
    if any(r in name for r in REGION_WORDS) and any(t in name for t in TECH_HINTS):
        return '海外科技'
    for cat, kws in RULES:
        if any(k in name for k in kws):
            return cat
    return '未分類'


def curl_json(url):
    p = subprocess.run(['curl', '-sf', '-m', '30', url], capture_output=True, timeout=60)
    if p.returncode:
        raise RuntimeError(f'curl exit {p.returncode}: {url}')
    return json.loads(p.stdout.decode())


def main():
    items = {}
    twse = curl_json('https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL')
    for x in twse:
        if x['Code'][:2] == '00':
            items[x['Code']] = {'name': x['Name'], 'market': 'TWSE'}
    tpex = curl_json('https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes')
    for x in tpex:
        if x['SecuritiesCompanyCode'][:2] == '00':
            items[x['SecuritiesCompanyCode']] = {'name': x['CompanyName'], 'market': 'TPEx'}

    for code, v in items.items():
        v['category'] = classify(v['name'], code)

    by_cat = {}
    for code, v in items.items():
        by_cat.setdefault(v['category'], []).append(code)

    out = {'fetched': datetime.now(timezone.utc).isoformat(timespec='seconds'),
           'source': 'TWSE openapi STOCK_DAY_ALL + TPEx openapi tpex_mainboard_quotes',
           'count': len(items), 'etfs': items,
           'by_category': {k: sorted(v) for k, v in sorted(by_cat.items(), key=lambda x: -len(x[1]))},
           'disclaimer': '分類用名稱關鍵字規則判斷（見 RULES），非官方分類，可能誤判，使用前應核對投信官網'}
    json.dump(out, open(os.path.join(D, 'etf_universe.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f'{len(items)} 檔 ETF，分類：')
    for k, v in out['by_category'].items():
        print(f'  {k}: {len(v)} 檔')
    return 0


if __name__ == '__main__':
    sys.exit(main())
