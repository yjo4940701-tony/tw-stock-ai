#!/usr/bin/env python3
"""判斷這次排程該不該跑、該用哪個 slot（am/pm），並在跑完後記錄「今天已發過」。

背景：GitHub Actions 免費 repo 的 cron 排程「盡力而為」，常常延遲甚至跳過
（2026-09-24 實測：08:00 台北排程延到 10:58 才跑）。解法：同一個 slot 設多個
備援時間點觸發，用 data/macro_report_state.json（git 追蹤、非機敏，只存日期）
記錄「今天這個 slot 發過了沒」，備援觸發時如果已經發過就直接跳過，避免重複發送。

🔴 2026-09-28 修正過一次錯誤設計：原本用「程式實際執行當下的時鐘」判斷 am/pm
（超過中午算 pm），結果早報的備援（09:30/11:00）被 GitHub 延遲到下午才執行，
執行當下已過中午 → 被誤判成「這是晚報」，發出一則貼錯標籤的訊息；等真正
17:00 那組晚報執行時，系統又誤以為「今天晚報已經發過」而跳過，導致使用者
收到的第二則其實是延遲又貼錯標籤的早報，不是準時的晚報。
**改法**：不看「現在幾點」，看「是哪一條 cron 表達式觸發的」（GitHub 在
schedule 事件會附上 github.event.schedule，內容是那條 cron 字串本身，不受
延遲影響——就算延到下午才跑，schedule 欄位仍是原本那條 '0 0 * * *' 等）。

用法：
  python macro_report_gate.py check   → 印 GITHUB_OUTPUT 格式：skip=yes/no、slot=am/pm
  python macro_report_gate.py mark <am|pm>  → 標記今天這個 slot 已發送，寫回 state 檔

workflow_dispatch（手動觸發）一律不跳過，方便測試。
"""
import json, os, sys
from datetime import datetime, timedelta, timezone

STATE_FILE = os.path.join(os.path.dirname(__file__), '..', 'data', 'macro_report_state.json')
TPE = timezone(timedelta(hours=8))

# cron 表達式 → slot（對應 macro-report.yml 的 schedule 清單，兩邊改動要同步）
CRON_SLOT = {
    '0 0 * * *': 'am', '30 1 * * *': 'am', '0 3 * * *': 'am',
    '0 9 * * *': 'pm', '30 10 * * *': 'pm', '0 12 * * *': 'pm',
}


def load_state():
    try:
        return json.load(open(STATE_FILE, encoding='utf-8'))
    except Exception:
        return {}


def today_tpe():
    return datetime.now(TPE).strftime('%Y-%m-%d')


def pick_slot_by_hour():
    """備援：schedule 字串對不到表時才用（理論上不該發生），或 workflow_dispatch 沒指定 slot 時。"""
    h = datetime.now(TPE).hour
    return 'am' if h < 12 else 'pm'


def check():
    event = os.environ.get('GITHUB_EVENT_NAME', '')
    dispatch_slot = os.environ.get('DISPATCH_SLOT', '').strip()
    cron = os.environ.get('GITHUB_EVENT_SCHEDULE', '').strip()
    today = today_tpe()
    state = load_state()

    if event == 'workflow_dispatch':
        slot = dispatch_slot if dispatch_slot in ('am', 'pm') else pick_slot_by_hour()
        print(f'skip=no')
        print(f'slot={slot}')
        return

    slot = CRON_SLOT.get(cron)
    if slot is None:
        print(f'警告：schedule「{cron}」不在 CRON_SLOT 對照表，退回用現在時鐘判斷', file=sys.stderr)
        slot = pick_slot_by_hour()
    already = state.get(f'{slot}_date') == today
    print(f'skip={"yes" if already else "no"}')
    print(f'slot={slot}')


def mark(slot):
    if slot not in ('am', 'pm'):
        raise SystemExit(f'slot 必須是 am 或 pm，收到: {slot}')
    state = load_state()
    state[f'{slot}_date'] = today_tpe()
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    json.dump(state, open(STATE_FILE, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f'已標記 {slot} @ {today_tpe()}')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        raise SystemExit('用法: macro_report_gate.py check | mark <am|pm>')
    if sys.argv[1] == 'check':
        check()
    elif sys.argv[1] == 'mark':
        mark(sys.argv[2])
    else:
        raise SystemExit(f'未知指令: {sys.argv[1]}')
