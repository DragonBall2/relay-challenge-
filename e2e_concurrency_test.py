#!/usr/bin/env python3
"""e2e_concurrency_test.py — 다중 사용자/세션 동시 요청 부하 시뮬.

목적: 여러 참가자가 각자 다른 세션에서 비슷한 시간에 다양한 요청을 보낼 때
시스템이 정상 동작하는지(불변 유지, 응답 일관성, 500 미발생) 검증.

각 스레드는 자기 app.test_client()를 가지며 자기만의 session 쿠키를 보유.
threading.Event로 모든 스레드를 동시에 출발 → 진짜 burst 시뮬레이션.

시나리오:
  C1. 100명 동시 leaderboard/index 폴링 (read 집중)
  C2. 한 active 주자에게 동시 정답 /submit 5회 (write 충돌, 중복 완료 방지)
  C3. 여러 조에서 동시에 정답 제출 (조 간 독립성, _activate_next_runner 무결성)
  C4. self-defer + admin defer 동시 (TOCTOU race)
  C5. 종합 burst (500 요청 5초 — 폴/제출/defer 혼합)

각 단계 끝나면 불변 검증: 조당 active ≤ 1, run_order 연속, 500 0건.
"""
import os
import random
import statistics
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

_results = {'pass': 0, 'fail': 0, 'failures': []}


def check(cond, name, detail=''):
    if cond:
        _results['pass'] += 1
        print(f'  PASS  {name}')
    else:
        _results['fail'] += 1
        _results['failures'].append((name, detail))
        msg = f'  FAIL  {name}'
        if detail:
            msg += f'\n         → {detail}'
        print(msg)


def section(t):
    print()
    print('=' * 70)
    print(f' {t}')
    print('=' * 70)


def reset_db():
    if os.path.exists('settings.json'):
        os.remove('settings.json')
    try:
        from app import app
        from models import db
        with app.app_context():
            db.session.remove()
            db.engine.dispose()
    except Exception:
        pass
    subprocess.run(['python', 'init_db.py'], stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)
    try:
        from app import app
        from models import db
        with app.app_context():
            db.session.remove()
            db.engine.dispose()
    except Exception:
        pass


def _open_challenge(app):
    """챌린지 공개 + admin 세션 별도 client 생성."""
    with app.test_client() as c:
        with c.session_transaction() as s:
            s['is_admin'] = True
        c.post('/admin/toggle-open',
               headers={'X-Requested-With': 'XMLHttpRequest'})


def _login_runner(client, app, runner_id):
    """test_client 세션에 runner_id 주입 (실제 /login 호출 대신 직접 세션 설정)."""
    from app import _read_settings
    with app.app_context():
        epoch = int(_read_settings().get('session_epoch', 1))
    with client.session_transaction() as s:
        s.clear()
        s['runner_id'] = runner_id
        s['session_epoch'] = epoch


def _set_admin(client):
    with client.session_transaction() as s:
        s['is_admin'] = True


def _wait_for_start(start_event, fn, *args, **kwargs):
    """start_event가 set될 때까지 대기한 뒤 fn 실행. 시작 시각 동기화용."""
    start_event.wait()
    return fn(*args, **kwargs)


def _percentiles(values):
    """[p50, p95, p99, max] ms 단위."""
    if not values:
        return (0, 0, 0, 0)
    s = sorted(values)
    def at(p):
        i = max(0, min(len(s) - 1, int(len(s) * p / 100)))
        return s[i]
    return (at(50), at(95), at(99), s[-1])


# ============================================================
# C1. 동시 read 부하 (leaderboard / index 폴링)
# ============================================================
def test_c1_polling(app, db, Runner, n_threads=100):
    section(f'C1. 동시 read 폴링 ({n_threads}개 스레드)')

    start = threading.Event()
    statuses = []
    durations = []
    errors = []

    def worker():
        try:
            with app.test_client() as c:
                t0 = time.time()
                r = c.get('/api/leaderboard')
                dt = (time.time() - t0) * 1000
                statuses.append(r.status_code)
                durations.append(dt)
        except Exception as e:
            errors.append(str(e))

    with ThreadPoolExecutor(max_workers=n_threads) as ex:
        futures = [ex.submit(_wait_for_start, start, worker) for _ in range(n_threads)]
        time.sleep(0.1)  # 모든 스레드가 wait 진입할 시간
        t_start = time.time()
        start.set()
        for f in as_completed(futures):
            f.result()
    elapsed = (time.time() - t_start) * 1000

    p50, p95, p99, pmax = _percentiles(durations)
    counts = Counter(statuses)
    check(errors == [], f'C1-a 예외 0건 (실제 {len(errors)})',
          detail=str(errors[:3]) if errors else '')
    check(counts.get(200, 0) == n_threads,
          f'C1-b 200 응답 {n_threads}건 (실제 분포 {dict(counts)})')
    check(pmax < 5000,
          f'C1-c 최장 응답 < 5s (실제 {pmax:.0f}ms)')
    print(f'  [지표] 전체 {elapsed:.0f}ms, p50={p50:.0f} p95={p95:.0f} p99={p99:.0f} max={pmax:.0f}ms')


# ============================================================
# C2. 한 active 주자에게 동시 정답 /submit 5회
# ============================================================
def test_c2_duplicate_submit(app, db, Runner, n_threads=5):
    section(f'C2. 동시 /submit {n_threads}회 (같은 주자, 같은 정답)')
    reset_db()
    _open_challenge(app)

    with app.app_context():
        r1 = Runner.query.filter_by(group_id=1, run_order=1).first()
        r1.started_at = datetime.utcnow()
        db.session.commit()
        rid = r1.id
        correct = r1.correct_answer

    # 모든 스레드가 같은 세션을 공유하면 안 됨 — 각자 client + 세션 설정
    start = threading.Event()
    statuses = []
    errors = []

    def worker(idx):
        try:
            with app.test_client() as c:
                _login_runner(c, app, rid)
                start.wait()
                r = c.post('/submit', data={'answer': correct})
                statuses.append(r.status_code)
        except Exception as e:
            errors.append(f'{idx}: {e}')

    with ThreadPoolExecutor(max_workers=n_threads) as ex:
        futures = [ex.submit(worker, i) for i in range(n_threads)]
        time.sleep(0.1)
        start.set()
        for f in as_completed(futures):
            f.result()

    check(errors == [], f'C2-a 예외 0건 (실제 {len(errors)})',
          detail=str(errors[:3]) if errors else '')

    # DB 검증: 해당 주자는 정확히 1번 완료, 다음 주자 1명 active
    with app.app_context():
        r_after = db.session.get(Runner, rid)
        next_active = Runner.query.filter_by(group_id=1, status='active').all()
    check(r_after.status == 'completed',
          f'C2-b 본인 status=completed (실제 {r_after.status})')
    check(r_after.completed_at is not None, 'C2-c completed_at 설정됨')
    check(len(next_active) == 1,
          f'C2-d 조 1에 active 정확히 1명 (실제 {len(next_active)})')
    print(f'  [지표] 응답 분포: {dict(Counter(statuses))}')


# ============================================================
# C3. 여러 조에서 동시 정답 제출
# ============================================================
def test_c3_multi_group_submit(app, db, Runner):
    section('C3. 여러 조 동시 정답 제출')
    reset_db()
    _open_challenge(app)

    # 모든 조의 1번 주자 정보 수집
    with app.app_context():
        groups = sorted({r.group_id for r in Runner.query.all()})
        infos = []
        for gid in groups:
            r1 = Runner.query.filter_by(group_id=gid, run_order=1).first()
            if r1:
                r1.started_at = datetime.utcnow()
                infos.append((r1.id, r1.correct_answer, gid))
        db.session.commit()

    start = threading.Event()
    statuses = []
    errors = []

    def worker(rid, ans, gid):
        try:
            with app.test_client() as c:
                _login_runner(c, app, rid)
                start.wait()
                r = c.post('/submit', data={'answer': ans})
                statuses.append((gid, r.status_code))
        except Exception as e:
            errors.append(f'g{gid}: {e}')

    with ThreadPoolExecutor(max_workers=len(infos)) as ex:
        futures = [ex.submit(worker, rid, ans, gid) for rid, ans, gid in infos]
        time.sleep(0.1)
        start.set()
        for f in as_completed(futures):
            f.result()

    check(errors == [], f'C3-a 예외 0건 (실제 {len(errors)})',
          detail=str(errors[:3]) if errors else '')

    # 각 조: 1번 주자는 completed, 2번 주자는 active (조 간 독립성)
    with app.app_context():
        ok_groups = 0
        bad = []
        for gid in groups:
            r1 = Runner.query.filter_by(group_id=gid, run_order=1).first()
            r2 = Runner.query.filter_by(group_id=gid, run_order=2).first()
            if (r1 and r1.status == 'completed'
                    and r2 and r2.status == 'active'):
                ok_groups += 1
            else:
                bad.append((gid, r1.status if r1 else '?',
                           r2.status if r2 else '?'))
        actives_per_group = Counter(
            r.group_id for r in Runner.query.filter_by(status='active').all()
        )
    check(ok_groups == len(groups),
          f'C3-b 모든 조에서 1번→completed + 2번→active ({ok_groups}/{len(groups)})',
          detail=str(bad[:3]))
    check(all(v == 1 for v in actives_per_group.values()),
          f'C3-c 조당 active 정확히 1명 (실제 {dict(actives_per_group)})')


# ============================================================
# C4. self-defer + admin defer 동시
# ============================================================
def test_c4_self_vs_admin_defer(app, db, Runner):
    section('C4. self-defer + admin defer 동시 (같은 주자)')
    reset_db()
    _open_challenge(app)

    with app.app_context():
        r1 = Runner.query.filter_by(group_id=1, run_order=1).first()
        r1.started_at = datetime.utcnow()
        db.session.commit()
        rid = r1.id

    start = threading.Event()
    results = {}
    errors = []

    def self_defer():
        try:
            with app.test_client() as c:
                _login_runner(c, app, rid)
                start.wait()
                r = c.post('/defer', data={'steps': '2', 'reason': 'self'})
                results['self'] = r.status_code
        except Exception as e:
            errors.append(f'self: {e}')

    def admin_defer():
        try:
            with app.test_client() as c:
                _set_admin(c)
                start.wait()
                r = c.post(f'/admin/defer/{rid}',
                           data={'mode': 'max', 'reason': 'admin'},
                           headers={'X-Requested-With': 'XMLHttpRequest'})
                results['admin'] = r.status_code
        except Exception as e:
            errors.append(f'admin: {e}')

    t1 = threading.Thread(target=self_defer)
    t2 = threading.Thread(target=admin_defer)
    t1.start(); t2.start()
    time.sleep(0.1)
    start.set()
    t1.join(); t2.join()

    check(errors == [], f'C4-a 예외 0건 (실제 {len(errors)})',
          detail=str(errors[:3]) if errors else '')

    # 둘 중 하나만 성공해도 OK — 핵심은 데이터 정합성
    with app.app_context():
        r_after = db.session.get(Runner, rid)
        actives = Runner.query.filter_by(group_id=1, status='active').all()
        # run_order 중복 확인
        orders = [r.run_order for r in Runner.query.filter_by(group_id=1).all()]
        unique = len(set(orders)) == len(orders)
    check(r_after.status in ('waiting', 'active'),
          f'C4-b defer-er 상태가 일관됨 (실제 {r_after.status})')
    check(len(actives) <= 1,
          f'C4-c 조 1에 active ≤ 1명 (실제 {len(actives)})')
    check(unique, f'C4-d run_order 중복 없음 (실제 {orders})')
    print(f'  [결과] self={results.get("self")} admin={results.get("admin")}')


# ============================================================
# C5. 종합 burst — 폴/제출/defer 혼합
# ============================================================
def test_c5_mixed_burst(app, db, Runner, total_requests=500, duration_s=5.0):
    section(f'C5. 종합 burst — {total_requests} 요청 {duration_s}초 (혼합)')
    reset_db()
    _open_challenge(app)

    # 각 조의 active 주자 정보 수집
    with app.app_context():
        actives = Runner.query.filter_by(status='active').all()
        active_infos = [(r.id, r.correct_answer, r.group_id) for r in actives]
        all_runner_ids = [r.id for r in Runner.query.all()]

    random.seed(20260512)
    statuses = []
    errors = []
    durations = []
    lock = threading.Lock()

    def do_action(idx):
        action = random.choices(
            ['poll_lb', 'poll_index', 'submit_wrong', 'submit_correct', 'admin_partial'],
            weights=[40, 30, 15, 10, 5],
        )[0]
        try:
            with app.test_client() as c:
                t0 = time.time()
                if action == 'poll_lb':
                    r = c.get('/api/leaderboard')
                elif action == 'poll_index':
                    r = c.get('/')
                elif action == 'admin_partial':
                    _set_admin(c)
                    r = c.get('/admin/partial/groups')
                elif action == 'submit_wrong':
                    if active_infos:
                        rid, ans, gid = random.choice(active_infos)
                        _login_runner(c, app, rid)
                        r = c.post('/submit', data={'answer': '99999'})
                    else:
                        r = c.get('/')
                elif action == 'submit_correct':
                    if active_infos:
                        rid, ans, gid = random.choice(active_infos)
                        _login_runner(c, app, rid)
                        r = c.post('/submit', data={'answer': ans})
                    else:
                        r = c.get('/')
                dt = (time.time() - t0) * 1000
                with lock:
                    statuses.append((action, r.status_code))
                    durations.append(dt)
        except Exception as e:
            with lock:
                errors.append(f'{action}: {e}')

    start = threading.Event()
    completed = [0]
    completed_lock = threading.Lock()

    def worker(idx):
        start.wait()
        # 0~duration_s 범위에서 랜덤 시각에 시작 (jitter)
        delay = random.uniform(0, duration_s)
        time.sleep(delay)
        do_action(idx)
        with completed_lock:
            completed[0] += 1

    with ThreadPoolExecutor(max_workers=64) as ex:
        futures = [ex.submit(worker, i) for i in range(total_requests)]
        time.sleep(0.2)
        t_start = time.time()
        start.set()
        for f in as_completed(futures):
            f.result()
    elapsed = time.time() - t_start

    p50, p95, p99, pmax = _percentiles(durations)
    # 500 응답 카운트 (실패)
    five_hundreds = sum(1 for _, code in statuses if code >= 500)
    by_action = Counter(a for a, _ in statuses)
    code_dist = Counter(c for _, c in statuses)

    check(errors == [],
          f'C5-a 예외 0건 (실제 {len(errors)})',
          detail=str(errors[:3]) if errors else '')
    check(five_hundreds == 0,
          f'C5-b 5xx 응답 0건 (실제 {five_hundreds})')
    check(pmax < 10000,
          f'C5-c 최장 응답 < 10s (실제 {pmax:.0f}ms)')

    # 불변 검증
    with app.app_context():
        from models import Group
        all_actives = Runner.query.filter_by(status='active').all()
        per_group_active = Counter(r.group_id for r in all_actives)
        bad_groups = {g: n for g, n in per_group_active.items() if n > 1}
        # run_order 연속성
        all_runners = Runner.query.all()
        per_group = {}
        for r in all_runners:
            per_group.setdefault(r.group_id, []).append(r.run_order)
        gaps = []
        for gid, orders in per_group.items():
            orders_sorted = sorted(orders)
            expected = list(range(1, len(orders) + 1))
            if orders_sorted != expected:
                gaps.append((gid, orders_sorted))

    check(not bad_groups,
          f'C5-d 모든 조 active ≤ 1 (위반: {bad_groups})')
    check(not gaps,
          f'C5-e 모든 조 run_order 연속 (위반: {gaps[:3]})')

    print(f'  [요청 분포] {dict(by_action)}')
    print(f'  [응답 코드] {dict(code_dist)}')
    print(f'  [지표] 총 시간 {elapsed:.1f}s, '
          f'p50={p50:.0f} p95={p95:.0f} p99={p99:.0f} max={pmax:.0f}ms, '
          f'처리량 {total_requests/elapsed:.0f} req/s')


def main():
    reset_db()
    from app import app
    from models import db, Runner

    try:
        test_c1_polling(app, db, Runner, n_threads=100)
        test_c2_duplicate_submit(app, db, Runner, n_threads=5)
        test_c3_multi_group_submit(app, db, Runner)
        test_c4_self_vs_admin_defer(app, db, Runner)
        test_c5_mixed_burst(app, db, Runner, total_requests=500, duration_s=5.0)
    except Exception as e:
        import traceback
        print(f'\n[FATAL]\n{traceback.format_exc()}')
        _results['fail'] += 1
        _results['failures'].append(('exception', str(e)))

    reset_db()
    print()
    print('=' * 70)
    print(f' 결과: {_results["pass"]}개 성공 / {_results["fail"]}개 실패')
    print('=' * 70)
    if _results['failures']:
        print('\n[실패 항목]')
        for n, d in _results['failures'][:20]:
            print(f'  - {n}')
            if d:
                print(f'      {d}')
    return 0 if _results['fail'] == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
