"""Observer Ladder: independent pre-release risk reminder, not a trading signal.

Reads official BLS iCalendar data, sends deduplicated T-120/T-60 Telegram
alerts, and keeps all V1/V2 predictions unchanged. Python stdlib only.
"""
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BLS_CALENDAR = 'https://www.bls.gov/schedule/news_release/bls.ics'
STATE = Path('event_alert_sent.json')
NY = ZoneInfo('America/New_York')
IMPORTANT = ('consumer price index', 'producer price index', 'employment situation')
# Date-scoped verified fallback; never silently extrapolate future dates.
# https://www.bls.gov/schedule/2026/11_sched_list.htm
VERIFIED_FALLBACK = [
    {'name': 'Producer Price Index for October 2026 (PPI)',
     'utc': '2026-11-13T13:30:00+00:00',
     'source': 'https://www.bls.gov/schedule/2026/11_sched_list.htm',
     'source_status': 'fixed_official_schedule_backup'},
]


def unfold_ical(data):
    output = []
    for line in data.replace('\r\n', '\n').split('\n'):
        if line.startswith((' ', '\t')) and output:
            output[-1] += line[1:]
        else:
            output.append(line)
    return output


def parse_ical(data):
    events, current = [], None
    for line in unfold_ical(data):
        if line == 'BEGIN:VEVENT':
            current = {}
        elif line == 'END:VEVENT' and current is not None:
            if current.get('SUMMARY') and any(k.startswith('DTSTART') for k in current):
                events.append(current)
            current = None
        elif current is not None and ':' in line:
            key, val = line.split(':', 1)
            current[key] = val
    results = []
    for event in events:
        name = event['SUMMARY'].replace('\\,', ',').replace('\\n', ' ')
        if not any(x in name.lower() for x in IMPORTANT):
            continue
        start_fields = [(k, v) for k, v in event.items() if k.startswith('DTSTART')]
        if not start_fields:
            continue
        key, raw = start_fields[0]
        if re.fullmatch(r'\d{8}T\d{6}Z', raw):
            dt = datetime.strptime(raw, '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)
        elif re.fullmatch(r'\d{8}T\d{6}', raw):
            tzname = re.search(r'TZID=([^;:]+)', key)
            if tzname:
                try:
                    tz = ZoneInfo(tzname.group(1))
                except (KeyError, ValueError):
                    continue
            else:
                # BLS calendar local times are Eastern; refuse unknown unqualified zones.
                continue
            dt = datetime.strptime(raw, '%Y%m%dT%H%M%S').replace(tzinfo=tz)
        else:
            continue
        results.append({'name': name, 'utc': dt.astimezone(timezone.utc).isoformat(),
                        'source': BLS_CALENDAR, 'source_status': 'official_calendar_fetch'})
    return results


def get_events():
    request = urllib.request.Request(BLS_CALENDAR, headers={
        'User-Agent': 'Observer-Ladder-EventRisk/1.0 (research; no trading)',
        'Accept': 'text/calendar, */*',
    })
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            raw = response.read(2_000_000).decode('utf-8-sig', errors='replace')
        parsed = parse_ical(raw)
        if not parsed:
            raise ValueError('No eligible high-impact events parsed')
        print(f'BLS calendar loaded: {len(parsed)} high-impact events')
        return parsed
    except Exception as exc:
        print(f'WARNING: BLS live calendar unavailable ({type(exc).__name__}): using date-scoped backup')
        return VERIFIED_FALLBACK


def send_message(text):
    token, chat = os.getenv('TELEGRAM_BOT_TOKEN'), os.getenv('TELEGRAM_CHAT_ID')
    if not token or not chat:
        raise RuntimeError('Telegram credentials missing')
    data = urllib.parse.urlencode({'chat_id': chat, 'text': text}).encode()
    req = urllib.request.Request(f'https://api.telegram.org/bot{token}/sendMessage', data=data,
                                 method='POST')
    with urllib.request.urlopen(req, timeout=20) as response:
        obj = json.load(response)
    if not obj.get('ok'):
        raise RuntimeError('Telegram did not acknowledge message')


def main(now=None, dry_run=False):
    now = now or datetime.now(timezone.utc)
    events = get_events()
    state = json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else {}
    # Bound the state to avoid unlimited growth.
    state = {k: v for k, v in state.items() if isinstance(v, str) and v >=
             (now - timedelta(days=14)).date().isoformat()}
    candidates = []
    for event in events:
        release = datetime.fromisoformat(event['utc']).astimezone(timezone.utc)
        mins = (release - now).total_seconds() / 60
        # Retry windows handle GitHub scheduling delays; no post-release warnings.
        for threshold, low, high in ((120, 100, 125), (60, 40, 65)):
            if low <= mins <= high:
                key = f"{event['name']}|{release.isoformat()}|T-{threshold}"
                if key not in state:
                    candidates.append((key, threshold, event, release, mins))
    if not candidates:
        print('No new event alerts in time windows')
        return 0
    for key, threshold, event, release, mins in candidates:
        eastern = release.astimezone(NY).strftime('%Y-%m-%d %H:%M %Z')
        utc = release.strftime('%Y-%m-%d %H:%M UTC')
        msg = (f'【Observer Ladder｜重大事件前风险预警 T-{threshold}】\n\n'
               f"事件：{event['name']}\n"
               f'官方计划时间：{utc}\n'
               f'纽约时间：{eastern}\n'
               f'距离发布：约{mins:.0f}分钟\n'
               f"日历来源：{event['source']}\n"
               f"来源状态：{event['source_status']}\n\n"
               '事件结果尚未知，不能提前断定 ETH 涨跌。\n'
               '若当前 V1 60分钟预测跨越该时刻，应降低方向信号可信度；'
               '请警惕剧烈双向波动与杠杆清算。\n'
               '仅为研究风险提醒，不是交易指令。')
        if dry_run:
            print('DRY RUN:', msg)
            continue
        send_message(msg)
        state[key] = now.date().isoformat()  # Write only after Telegram confirms success.
        STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(f'Sent event risk reminder: {key}')
    return len(candidates)


if __name__ == '__main__':
    main(dry_run='--dry-run' in sys.argv)
