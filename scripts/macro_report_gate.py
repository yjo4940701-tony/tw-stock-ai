#!/usr/bin/env python3
"""判斷這次排程該不該跑、該用哪個 slot（am/pm），並在跑完後記錄「上次發送時間」。

背景：GitHub Actions 免費 repo 的 cron 排程「盡力而為」，常常延遲甚至跳過
（2026-09-24 實測：08:00 台北排程延到 10:58 才跑）。解法：同一個 slot 設多個
備援時間點觸發，記錄「這個 slot 上次成功發送是什麼時候」，備援觸發時如果
剛發過不久就跳過，避免重複發送。

🔴 2026-09-28 修正過一次：原本用「程式執行當下的時鐘」判斷 am/pm，結果早報
備援被延遲到下午執行，誤判成晚報。改用 github.event.schedule（觸發的 cron
字串本身，不受延遲影響）對照表決定 slot。

🔴 2026-09-29 又修正一次：原本用「日期字串」（YYYY-MM-DD）判斷「今天發過了
沒」，但晚報備援被延遲到跨過午夜（延到隔天 01:31 才執行）→ 日曆已經跳到新的
一天，跟狀態檔記錄的「昨天已發送」對不上 → 誤判成「今天還沒發過」又重複發
一次。改法：不比較日期字串，改記錄「上次成功發送的精確時間」，用「距離上次
同 slot 發送是否超過一段緩衝時間（12 小時）」判斷要不要跳過，不受日期跳動
影響。12 小時的選擇：同一輪 3 個備援時間點最長間隔約 3 小時，遠小於 12 小
時；同 slot 隔天再發至少相隔約 21 小時（現觀察最大延遲約 5.5 小時），大於
12 小時，兩種情況分得開。

用法：
  python macro_report_gate.py check   → 印 GITHUB_OUTPUT 格式：skip=yes/no、slot=am/pm
  python macro_report_gate.py mark <am|pm>  → 記錄這個 slot 剛剛成功發送的時間

workflow_dispatch（手動觸發）一律不跳過，方便測試。
"""
import json, os, sys
from datetime import datetime, timedelta, timezone

STATE_FILE = os.path.join(os.path.dirname(__file__), '..', 'data', 'macro_report_state.json')
TPE = timezone(timedelta(hours=8))
MIN_GAP = timedelta(hours=12)   # 同一個 slot 距離上次發送小於這個時間 → 視為同一輪的重複備援，跳過

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


def now_tpe():
    return datetime.now(TPE)


def pick_slot_by_hour():
    """備援：schedule 字串對不到表時才用（理論上不該發生），或 workflow_dispatch 沒指定 slot 時。"""
    h = now_tpe().hour
    return 'am' if h < 12 else 'pm'


def check():
    event = os.environ.get('GITHUB_EVENT_NAME', '')
    dispatch_slot = os.environ.get('DISPATCH_SLOT', '').strip()
    cron = os.environ.get('GITHUB_EVENT_SCHEDULE', '').strip()
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

    last = state.get(f'{slot}_last')
    skip = False
    if last:
        try:
            gap = now_tpe() - datetime.fromisoformat(last)
            skip = gap < MIN_GAP
        except Exception as e:
            print(f'解析上次發送時間失敗（{last}）: {e}，視為未發送', file=sys.stderr)
    print(f'skip={"yes" if skip else "no"}')
    print(f'slot={slot}')


def mark(slot):
    if slot not in ('am', 'pm'):
        raise SystemExit(f'slot 必須是 am 或 pm，收到: {slot}')
    state = load_state()
    state[f'{slot}_last'] = now_tpe().isoformat(timespec='seconds')
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    json.dump(state, open(STATE_FILE, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f'已標記 {slot} @ {state[f"{slot}_last"]}')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        raise SystemExit('用法: macro_report_gate.py check | mark <am|pm>')
    if sys.argv[1] == 'check':
        check()
    elif sys.argv[1] == 'mark':
        mark(sys.argv[2])
    else:
        raise SystemExit(f'未知指令: {sys.argv[1]}')
