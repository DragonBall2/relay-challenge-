#!/usr/bin/env python3
"""app.log 분석 도구.

사용:
    python3 tools/log_summary.py              # 오늘 로그
    python3 tools/log_summary.py app.log.2026-05-10  # 특정 파일
    python3 tools/log_summary.py --tail 100   # 최근 100건만

요약:
- 이벤트 카운트 (event=X 별)
- 시간대별 활동 (시 단위)
- 경고·에러 항목 나열
- 느린 요청 TOP
"""
import os
import re
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LOG = os.path.join(os.path.dirname(HERE), 'app.log')

# 2026-05-11 14:23:45,123 [INFO] event=login runner_id=42 ...
LINE_RE = re.compile(
    r'^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})[,\d]*\s+\[(?P<level>\w+)\]\s+(?P<msg>.*)$'
)
EVENT_RE = re.compile(r'event=(?P<event>\S+)')
KV_RE = re.compile(r'(\w+)=("(?:[^"]|\\")*"|\S+)')


def parse_line(line):
    m = LINE_RE.match(line)
    if not m:
        return None
    ts = m.group('ts')
    level = m.group('level')
    msg = m.group('msg')
    em = EVENT_RE.search(msg)
    event = em.group('event') if em else None
    kv = {}
    for k, v in KV_RE.findall(msg):
        kv[k] = v.strip('"')
    return {'ts': ts, 'level': level, 'event': event, 'kv': kv, 'raw': line.rstrip()}


def hr(title):
    print()
    print('=' * 70)
    print(' ' + title)
    print('=' * 70)


def main():
    path = DEFAULT_LOG
    tail = None
    args = sys.argv[1:]
    while args:
        a = args.pop(0)
        if a == '--tail':
            tail = int(args.pop(0))
        else:
            path = a

    if not os.path.exists(path):
        print(f'로그 파일 없음: {path}')
        return 1

    entries = []
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            e = parse_line(line)
            if e:
                entries.append(e)
    if tail:
        entries = entries[-tail:]

    if not entries:
        print('파싱된 로그 없음')
        return 1

    hr('파일 정보')
    print(f'  경로: {path}')
    print(f'  엔트리 수: {len(entries)}')
    print(f'  기간: {entries[0]["ts"]} ~ {entries[-1]["ts"]}')

    hr('이벤트별 카운트')
    counter = Counter(e['event'] or '(no-event)' for e in entries)
    for event, count in counter.most_common(30):
        print(f'  {count:>6}  {event}')

    hr('레벨별 카운트')
    levels = Counter(e['level'] for e in entries)
    for lv, c in levels.most_common():
        print(f'  {c:>6}  {lv}')

    hr('시간대별 활동 (시 단위)')
    hourly = defaultdict(int)
    for e in entries:
        hour = e['ts'][:13]  # 'YYYY-MM-DD HH'
        hourly[hour] += 1
    for h in sorted(hourly):
        bar = '#' * min(60, hourly[h] // max(1, max(hourly.values()) // 60))
        print(f'  {h}:00  {hourly[h]:>5}  {bar}')

    hr('경고 (WARNING) 이벤트')
    warns = [e for e in entries if e['level'] == 'WARNING']
    if warns:
        wcount = Counter(e['event'] or '(no-event)' for e in warns)
        for event, count in wcount.most_common():
            print(f'  {count:>4}  {event}')
            # 처음 3건 샘플
            samples = [e for e in warns if e['event'] == event][:3]
            for s in samples:
                print(f'        {s["ts"]}  {s["raw"][-100:]}')
    else:
        print('  (없음)')

    hr('에러 (ERROR) 이벤트')
    errs = [e for e in entries if e['level'] == 'ERROR']
    if errs:
        for e in errs[-10:]:
            print(f'  {e["ts"]}  {e["raw"][-150:]}')
    else:
        print('  (없음)')

    hr('느린 요청 TOP 10 (slow_request)')
    slows = [(int(e['kv'].get('ms', 0)), e) for e in entries
             if e['event'] == 'slow_request']
    slows.sort(reverse=True)
    if slows:
        for ms, e in slows[:10]:
            print(f'  {ms:>6}ms  {e["ts"]}  {e["kv"].get("path","?")}  status={e["kv"].get("status","?")}')
    else:
        print('  (없음)')

    return 0


if __name__ == '__main__':
    sys.exit(main())
