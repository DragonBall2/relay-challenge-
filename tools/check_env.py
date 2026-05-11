#!/usr/bin/env python3
"""check_env.py — 회사 서버 환경 진단.

용도: 서버 장애 원인 가능성(자원/네트워크/DB/프로세스)을 한 번에 확인.
실행: relay-challenge 디렉토리에서 `python tools/check_env.py`
출력: 사람이 읽기 쉬운 키=값 형식. 그대로 복사해 공유 가능.

설계 원칙:
  - 외부 의존성 없음 (표준 라이브러리만)
  - 명령어/파일 없으면 우아하게 fallback
  - 어떠한 변경도 가하지 않음 (read-only)
  - 민감정보(주자 비밀번호, 답안)는 출력하지 않음
"""
import os
import platform
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from datetime import datetime


def header(title):
    print()
    print('=' * 70)
    print(f' {title}')
    print('=' * 70)


def kv(k, v):
    print(f'  {k:<30} {v}')


def run(cmd, timeout=5):
    """명령어 실행, 실패 시 None 반환."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, shell=isinstance(cmd, str))
        if r.returncode == 0:
            return r.stdout.strip()
        return None
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


def has_cmd(name):
    return shutil.which(name) is not None


# ============================================================
# 1. 시스템 기본
# ============================================================
def section_system():
    header('1. 시스템 기본')
    kv('hostname', platform.node())
    kv('os', f'{platform.system()} {platform.release()}')
    kv('platform', platform.platform())
    kv('arch', platform.machine())
    kv('python', sys.version.split()[0])
    kv('current time', datetime.now().isoformat(timespec='seconds'))
    kv('cwd', os.getcwd())

    # uptime
    if os.path.exists('/proc/uptime'):
        with open('/proc/uptime') as f:
            up = float(f.read().split()[0])
        days = int(up // 86400)
        hours = int((up % 86400) // 3600)
        kv('uptime', f'{days}d {hours}h')
    else:
        out = run('uptime')
        if out:
            kv('uptime', out)


# ============================================================
# 2. CPU
# ============================================================
def section_cpu():
    header('2. CPU')
    kv('cpu_count (logical)', os.cpu_count())

    # /proc/cpuinfo
    if os.path.exists('/proc/cpuinfo'):
        with open('/proc/cpuinfo') as f:
            txt = f.read()
        models = sorted(set(re.findall(r'model name\s*:\s*(.+)', txt)))
        if models:
            kv('cpu_model', models[0])
        physical = len(set(re.findall(r'physical id\s*:\s*(\d+)', txt)))
        cores = len(set(re.findall(r'core id\s*:\s*(\d+)', txt))) or 'unknown'
        if physical:
            kv('physical sockets', physical)
        kv('cores per socket', cores)

    # load average
    if hasattr(os, 'getloadavg'):
        la = os.getloadavg()
        kv('load average 1/5/15m', f'{la[0]:.2f} / {la[1]:.2f} / {la[2]:.2f}')
        if la[0] > os.cpu_count():
            kv('!! load > cpu_count', '경합 의심')


# ============================================================
# 3. 메모리
# ============================================================
def section_memory():
    header('3. 메모리')
    if os.path.exists('/proc/meminfo'):
        info = {}
        with open('/proc/meminfo') as f:
            for line in f:
                k, v = line.split(':', 1)
                m = re.match(r'\s*(\d+)\s*kB', v)
                if m:
                    info[k] = int(m.group(1)) * 1024
        total = info.get('MemTotal', 0)
        avail = info.get('MemAvailable', 0)
        free = info.get('MemFree', 0)
        cached = info.get('Cached', 0)
        swap_total = info.get('SwapTotal', 0)
        swap_free = info.get('SwapFree', 0)

        def mb(b):
            return f'{b / 1024 / 1024:.0f} MiB'

        kv('MemTotal', mb(total))
        kv('MemAvailable', f'{mb(avail)}  ({avail/total*100:.1f}%)' if total else mb(avail))
        kv('MemFree', mb(free))
        kv('Cached', mb(cached))
        kv('SwapTotal', mb(swap_total))
        if swap_total:
            kv('SwapUsed', f'{mb(swap_total - swap_free)}  '
               f'({(swap_total-swap_free)/swap_total*100:.1f}%)')
        if total and avail / total < 0.10:
            kv('!! MemAvailable < 10%', '메모리 부족 위험')
    else:
        out = run('vm_stat') or run('free -m')
        if out:
            for line in out.splitlines()[:6]:
                print(f'  {line}')


# ============================================================
# 4. 디스크
# ============================================================
def section_disk():
    header('4. 디스크')
    try:
        st = shutil.disk_usage(os.getcwd())
        total_gb = st.total / 1024**3
        free_gb = st.free / 1024**3
        used_pct = (1 - st.free / st.total) * 100
        kv('cwd disk total', f'{total_gb:.1f} GiB')
        kv('cwd disk free', f'{free_gb:.1f} GiB')
        kv('cwd disk used%', f'{used_pct:.1f}%')
        if used_pct > 90:
            kv('!! disk > 90%', 'WAL 확장 / 로그 적재 실패 위험')
    except Exception as e:
        kv('disk_usage_error', str(e))

    # relay.db / WAL / SHM 사이즈
    for name in ('relay.db', 'relay.db-wal', 'relay.db-shm'):
        if os.path.exists(name):
            sz = os.path.getsize(name)
            mtime = datetime.fromtimestamp(os.path.getmtime(name)).isoformat(timespec='seconds')
            kv(name, f'{sz:,} bytes (mtime {mtime})')
            if name == 'relay.db-wal' and sz > 50 * 1024 * 1024:
                kv(f'!! {name} > 50MB', 'WAL 체크포인트 미작동 의심')

    # logs/app.log
    if os.path.exists('logs/app.log'):
        sz = os.path.getsize('logs/app.log')
        kv('logs/app.log', f'{sz:,} bytes')

    # challenge_data.dat
    if os.path.exists('challenge_data.dat'):
        sz = os.path.getsize('challenge_data.dat')
        kv('challenge_data.dat', f'{sz:,} bytes ({sz/1024/1024:.1f} MiB)')


# ============================================================
# 5. 네트워크
# ============================================================
def section_network():
    header('5. 네트워크 (waitress 포트 8080)')

    # ESTABLISHED 연결 수 / 상태별 카운트
    # 우선순위: ss → netstat → /proc/net/tcp
    counts = {}
    if has_cmd('ss'):
        out = run('ss -ant')
        if out:
            for line in out.splitlines()[1:]:
                parts = line.split()
                if not parts:
                    continue
                state = parts[0]
                # ss 일부 출력은 첫 컬럼이 'State' 헤더 또는 'LISTEN' 등
                # 그리고 4번째 컬럼이 local addr (Local Address:Port)
                local = parts[3] if len(parts) >= 4 else ''
                if ':8080' in local or local.endswith(':8080'):
                    counts[state] = counts.get(state, 0) + 1
            kv('counted_via', 'ss -ant')
    elif has_cmd('netstat'):
        out = run('netstat -ant')
        if out:
            for line in out.splitlines():
                parts = line.split()
                if len(parts) < 6:
                    continue
                # netstat: Proto Recv-Q Send-Q Local Foreign State
                local = parts[3]
                state = parts[5]
                if local.endswith(':8080'):
                    counts[state] = counts.get(state, 0) + 1
            kv('counted_via', 'netstat -ant')
    elif os.path.exists('/proc/net/tcp'):
        # /proc/net/tcp: hex 형식. local_address는 IP:PORT(hex)
        # 상태 코드: 01 ESTABLISHED, 02 SYN_SENT, 03 SYN_RECV, 06 TIME_WAIT, 0A LISTEN
        STATE_NAMES = {'01': 'ESTABLISHED', '02': 'SYN_SENT', '03': 'SYN_RECV',
                       '04': 'FIN_WAIT1', '05': 'FIN_WAIT2', '06': 'TIME_WAIT',
                       '07': 'CLOSE', '08': 'CLOSE_WAIT', '09': 'LAST_ACK',
                       '0A': 'LISTEN', '0B': 'CLOSING'}
        with open('/proc/net/tcp') as f:
            lines = f.readlines()[1:]
        for line in lines:
            parts = line.split()
            if len(parts) < 4:
                continue
            local = parts[1]  # IP:PORT hex
            state = parts[3]
            try:
                port_hex = local.split(':')[1]
                port = int(port_hex, 16)
            except (IndexError, ValueError):
                continue
            if port == 8080:
                name = STATE_NAMES.get(state.upper(), state)
                counts[name] = counts.get(name, 0) + 1
        kv('counted_via', '/proc/net/tcp')
    else:
        kv('counted_via', '도구 없음 (ss/netstat/proc 모두 불가)')

    if counts:
        for state in sorted(counts):
            kv(f'port 8080 {state}', counts[state])
        total = sum(counts.values())
        kv('port 8080 total', total)
        if counts.get('ESTABLISHED', 0) > 800:
            kv('!! ESTABLISHED > 800', '연결 한계 근접')
        if counts.get('SYN_RECV', 0) > 50:
            kv('!! SYN_RECV > 50', '신규 연결 수락 적체')
        if counts.get('CLOSE_WAIT', 0) > 100:
            kv('!! CLOSE_WAIT > 100', '앱이 close를 안 하고 있음 (소켓 누수 의심)')

    # 호스트의 IP들
    try:
        host_ips = socket.gethostbyname_ex(socket.gethostname())[2]
        kv('host_ips', ', '.join(host_ips))
    except Exception:
        pass


# ============================================================
# 6. 프로세스 (waitress / python)
# ============================================================
def section_processes():
    header('6. 프로세스')

    # Linux: ps -eo 형식 사용
    out = run('ps -eo pid,user,%cpu,%mem,rss,nlwp,cmd --sort=-%mem')
    if not out:
        out = run('ps aux')

    if out:
        lines = out.splitlines()
        if lines:
            print(f'  {lines[0]}')
        # python 또는 waitress 관련 줄만
        match_count = 0
        for line in lines[1:]:
            if 'python' in line.lower() or 'waitress' in line.lower() or 'relay' in line.lower():
                print(f'  {line[:200]}')
                match_count += 1
                if match_count >= 10:
                    break
        if match_count == 0:
            kv('python/waitress', '실행 중 프로세스 없음 (서버 미가동?)')

    # 현재 프로세스 FD 사용량 (자기 자신 기준이라 참고용)
    if os.path.exists(f'/proc/self/fd'):
        try:
            n = len(os.listdir('/proc/self/fd'))
            kv('self fd count', n)
        except Exception:
            pass

    # waitress 프로세스의 PID 찾아서 그 PID의 fd 수 확인
    out = run('pgrep -f waitress-serve')
    if out:
        for pid in out.split():
            fd_dir = f'/proc/{pid}/fd'
            if os.path.exists(fd_dir):
                try:
                    n = len(os.listdir(fd_dir))
                    kv(f'waitress PID {pid} fd_count', n)
                    if n > 800:
                        kv(f'!! PID {pid} fd > 800', 'ulimit 근접')
                except PermissionError:
                    kv(f'waitress PID {pid} fd_count', '(권한 부족)')

    # ulimit
    out = run('sh -c "ulimit -n"')
    if out:
        kv('ulimit -n (max fd)', out)
        try:
            if int(out) < 1024:
                kv('!! ulimit < 1024', '동시 연결 한계 낮음')
        except ValueError:
            pass


# ============================================================
# 7. DB / SQLite PRAGMA
# ============================================================
def section_db():
    header('7. DB / SQLite PRAGMA')
    if not os.path.exists('relay.db'):
        kv('relay.db', '없음')
        return

    # 별도 connection으로 read-only 접근 (앱과 충돌 안 함)
    try:
        # WAL 모드면 다른 프로세스가 쓰고 있어도 read 가능
        conn = sqlite3.connect('file:relay.db?mode=ro', uri=True, timeout=2)
        c = conn.cursor()

        # DB 레벨 영구 설정 (모든 연결이 공유)
        for pragma in ('journal_mode', 'wal_autocheckpoint', 'page_size'):
            try:
                r = c.execute(f'PRAGMA {pragma}').fetchone()
                kv(f'PRAGMA {pragma} (영구)', r[0] if r else '(none)')
            except sqlite3.OperationalError as e:
                kv(f'PRAGMA {pragma}', f'error: {e}')
        # per-connection 설정은 이 진단 스크립트의 값이라 의미 없음 → app.py 소스에서 확인
        if os.path.exists('app.py'):
            with open('app.py', encoding='utf-8') as f:
                src = f.read()
            for pragma in ('journal_mode', 'synchronous', 'busy_timeout'):
                m = re.search(rf"PRAGMA\s+{pragma}\s*=\s*(\S+?)['\"]", src, re.IGNORECASE)
                kv(f'app.py 설정 {pragma}',
                   m.group(1) if m else '(미설정 → SQLite 기본값 사용)')

        # 무결성 (빠른 확인)
        try:
            r = c.execute('PRAGMA quick_check').fetchone()
            kv('quick_check', r[0] if r else '?')
        except sqlite3.OperationalError as e:
            kv('quick_check', f'error: {e}')

        # 행 수
        for tbl in ('groups', 'runners', 'attempt_logs', 'spare_problems'):
            try:
                r = c.execute(f'SELECT COUNT(*) FROM {tbl}').fetchone()
                kv(f'rows.{tbl}', r[0])
            except sqlite3.OperationalError as e:
                kv(f'rows.{tbl}', f'error: {e}')

        # 조별 active 주자 수 (이중 active 감지)
        try:
            r = c.execute(
                "SELECT group_id, COUNT(*) FROM runners "
                "WHERE status='active' GROUP BY group_id HAVING COUNT(*) > 1"
            ).fetchall()
            if r:
                kv('!! 이중 active 그룹', ', '.join(f'g{g}={n}' for g, n in r))
            else:
                kv('이중 active 검사', 'OK (조당 active ≤ 1)')
        except sqlite3.OperationalError as e:
            kv('이중 active 검사', f'error: {e}')

        # status 분포
        try:
            r = c.execute(
                "SELECT status, COUNT(*) FROM runners GROUP BY status"
            ).fetchall()
            for status, cnt in r:
                kv(f'runners.{status}', cnt)
        except sqlite3.OperationalError as e:
            kv('status 분포', f'error: {e}')

        conn.close()
    except sqlite3.OperationalError as e:
        kv('DB 접근 실패', str(e))


# ============================================================
# 8. 앱 설정 / 로그
# ============================================================
def section_app():
    header('8. 앱 설정 / 최근 로그')

    if os.path.exists('settings.json'):
        import json
        try:
            with open('settings.json') as f:
                s = json.load(f)
            for k, v in s.items():
                kv(f'settings.{k}', v)
        except Exception as e:
            kv('settings.json', f'parse error: {e}')
    else:
        kv('settings.json', '없음')

    # 최근 로그 WARNING/ERROR
    log_path = 'logs/app.log'
    if os.path.exists(log_path):
        try:
            with open(log_path, 'rb') as f:
                # 끝에서 200KB 읽기
                f.seek(0, 2)
                size = f.tell()
                read = min(size, 200 * 1024)
                f.seek(-read, 2)
                data = f.read().decode('utf-8', errors='replace')
            lines = data.splitlines()
            warns = [ln for ln in lines if 'WARNING' in ln or 'ERROR' in ln
                     or 'CRITICAL' in ln]
            kv('log 최근 WARNING+ 건수', f'{len(warns)} (최근 200KB 기준)')
            print('  -- 최근 WARNING/ERROR 10건 --')
            for ln in warns[-10:]:
                print(f'  {ln[:300]}')
            # Task queue 관련
            queue_lines = [ln for ln in lines if 'queue' in ln.lower()
                           and 'depth' in ln.lower()]
            if queue_lines:
                print('  -- waitress queue depth 경고 (최근 5) --')
                for ln in queue_lines[-5:]:
                    print(f'  {ln[:300]}')
        except Exception as e:
            kv('log 읽기 오류', str(e))


# ============================================================
# 9. Python 패키지 버전
# ============================================================
def section_packages():
    header('9. Python 패키지')
    for pkg in ('flask', 'flask_sqlalchemy', 'sqlalchemy', 'waitress'):
        try:
            from importlib.metadata import version
            v = version(pkg.replace('_', '-'))
            kv(pkg, v)
        except Exception:
            try:
                mod = __import__(pkg)
                kv(pkg, getattr(mod, '__version__', '?'))
            except ImportError:
                kv(pkg, '(설치 안 됨)')


# ============================================================
# 10. 요약 / 권장
# ============================================================
def section_summary():
    header('10. 진단 체크리스트')
    print('  앞 섹션에서 "!!"로 표시된 항목이 있으면 우선 점검 필요.')
    print('')
    print('  주요 확인 항목:')
    print('    - 2: load > cpu_count → CPU 경합 (waitress threads 조정)')
    print('    - 3: MemAvailable < 10% → 메모리 부족')
    print('    - 4: relay.db-wal > 50MB → WAL 체크포인트 안 됨 (서버 재시작 권장)')
    print('    - 5: ESTABLISHED 폭증 / SYN_RECV 적체 / CLOSE_WAIT 누수')
    print('    - 6: fd > 800 또는 ulimit < 1024 → 연결 한계')
    print('    - 7: 이중 active 감지 / DB 무결성 / PRAGMA 미적용')
    print('    - 8: 최근 WARNING/ERROR / queue depth 경고')


def main():
    print('relay-challenge 서버 환경 진단')
    print(f'(start at {datetime.now().isoformat(timespec="seconds")})')
    for fn in (section_system, section_cpu, section_memory, section_disk,
               section_network, section_processes, section_db,
               section_app, section_packages, section_summary):
        try:
            fn()
        except Exception as e:
            print(f'\n[{fn.__name__} 실패] {e}')
    print()
    print('=' * 70)
    print(' 진단 종료')
    print('=' * 70)


if __name__ == '__main__':
    main()
