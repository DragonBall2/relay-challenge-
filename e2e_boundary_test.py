#!/usr/bin/env python3
"""e2e_boundary_test.py — 경계값·엣지 케이스 검증.

검증 항목:
  B1. N=2, G=2 최소
  B2. N=500, G=50 최대
  B3. 조당 1명 (조 안 릴레이 불가, 즉시 완주 가능)
  B4. 불균등 분배 (N=125, G=10)
  B5. 빈 CSV / 행 부족 / 중복 knox_id
  B6. compute_group_sizes 경계값
  B7. 모든 주자가 동시에 done 시 group.finished_at
"""
import math
import os
import subprocess
import sys

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
    # Windows: SQLAlchemy 연결을 먼저 닫아야 init_db.py가 relay.db를 삭제할 수 있음
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
    # init 후에도 한 번 더 dispose해서 stale 캐시 무효화
    try:
        from app import app
        from models import db
        with app.app_context():
            db.session.remove()
            db.engine.dispose()
    except Exception:
        pass


def init_via_wizard(c, n, g, difficulty='medium', seed=2026, buffer_ratio=0.3):
    """마법사 commit 호출. 성공 시 True."""
    csv = '\n'.join([f'u{i:03d},u{i}' for i in range(1, n + 1)])
    r = c.post('/admin/init/parse',
               json={'csv_text': csv, 'total_n': n, 'group_count': g})
    d = r.get_json()
    if not d.get('ok'):
        return False, d
    ordered = []
    idx = 0
    for grp, sz in enumerate(d['group_sizes'], 1):
        for o in range(1, sz + 1):
            p = d['participants'][idx]; idx += 1
            ordered.append({'knox_id': p['knox_id'], 'name': p['name'],
                            'group': grp, 'order': o})
    body = {
        'group_count': g, 'group_sizes': d['group_sizes'],
        'ordered_participants': ordered, 'regen_challenge_data': True,
        'force': True, 'show_individual_ranking': True,
        'difficulty': difficulty, 'defer_penalty_seconds': 0,
        'buffer_ratio': buffer_ratio, 'seed': seed, 'confirm': 'INIT',
    }
    r = c.post('/admin/init/commit', json=body)
    return r.get_json().get('ok'), r.get_json()


def test_b1_minimum(app, db, Runner):
    section('B1. 최소 규모 N=2, G=2')
    reset_db()
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess['is_admin'] = True
        ok, result = init_via_wizard(c, n=2, g=2)
        check(ok, f'B1 init N=2 G=2 성공')
        if ok:
            with app.app_context():
                count = Runner.query.count()
                groups = sorted(Runner.query.with_entities(Runner.group_id).distinct().all())
            check(count == 2, f'B1 주자 2명 (실제 {count})')
            check(len(groups) == 2, f'B1 조 2개 (실제 {len(groups)})')


def test_b2_max(app, db, Runner):
    section('B2. 최대 규모 N=500, G=50')
    reset_db()
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess['is_admin'] = True
        # 문제 생성기 한계 ≈ 600개. N=500 + buffer 0.15(75개) = 575개 요청
        ok, result = init_via_wizard(c, n=500, g=50, buffer_ratio=0.15)
        check(ok, f'B2 init N=500 G=50 성공',
              detail=f'message: {result.get("message") if isinstance(result, dict) else result}')
        if ok:
            with app.app_context():
                count = Runner.query.count()
            check(count == 500, f'B2 주자 500명 (실제 {count})')


def test_b3_one_per_group(app, db, Runner):
    section('B3. 조당 1명 (G == N)')
    reset_db()
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess['is_admin'] = True
        ok, _ = init_via_wizard(c, n=5, g=5)
        check(ok, f'B3 init N=5 G=5 성공')
        with app.app_context():
            actives = Runner.query.filter_by(status='active').count()
        check(actives == 5, f'B3 조당 1명 모두 active (실제 {actives})')


def test_b4_uneven_distribution(app, db):
    section('B4. 불균등 분배 (N=125, G=10)')
    from init_db import compute_group_sizes
    sizes = compute_group_sizes(125, 10)
    # 앞 5개 조 13명, 나머지 5개 조 12명
    expected = [13, 13, 13, 13, 13, 12, 12, 12, 12, 12]
    check(sizes == expected,
          f'B4 compute_group_sizes(125,10) = {expected}',
          detail=f'실제 {sizes}')

    # 더 극단적: N=11, G=10 → 1조만 2명, 나머지 9조 1명
    sizes = compute_group_sizes(11, 10)
    check(sizes == [2] + [1]*9, f'B4 compute_group_sizes(11,10) = [2]+[1]*9')


def test_b5_invalid_csv(app, db):
    section('B5. 빈 CSV / 중복 / 부족')
    reset_db()
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess['is_admin'] = True

        # 빈 CSV
        r = c.post('/admin/init/parse',
                   json={'csv_text': '', 'total_n': 10, 'group_count': 2})
        check(not r.get_json().get('ok'), 'B5-a 빈 CSV → 거부')

        # 중복 knox_id
        csv = 'alice,A\nalice,B\nbob,C\n'
        r = c.post('/admin/init/parse',
                   json={'csv_text': csv, 'total_n': 3, 'group_count': 1})
        check(not r.get_json().get('ok'), 'B5-b 중복 knox_id → 거부')

        # 빈 knox_id
        csv = ',이름A\n'
        r = c.post('/admin/init/parse',
                   json={'csv_text': csv, 'total_n': 1, 'group_count': 1})
        check(not r.get_json().get('ok'), 'B5-c 빈 knox_id → 거부')

        # 행 < N (자동 채움 동작) — group_count는 2 이상 필요
        csv = 'a,A\nb,B'
        r = c.post('/admin/init/parse',
                   json={'csv_text': csv, 'total_n': 5, 'group_count': 2})
        d = r.get_json()
        check(d.get('ok') and len(d.get('participants', [])) == 5,
              'B5-d 행<N: extra### 자동 채움',
              detail=f'd={d}')


def test_b6_compute_group_sizes_edge(app, db):
    section('B6. compute_group_sizes 경계값')
    from init_db import compute_group_sizes

    # 정확히 균등
    check(compute_group_sizes(100, 10) == [10]*10, 'B6-a 100/10 = [10]*10')

    # 1명 더
    check(compute_group_sizes(101, 10) == [11] + [10]*9, 'B6-b 101/10 = [11,10..10]')

    # 최소
    check(compute_group_sizes(2, 2) == [1, 1], 'B6-c 2/2 = [1,1]')

    # N < G → ValueError 예상
    try:
        compute_group_sizes(2, 5)
        check(False, 'B6-d N<G 시 ValueError')
    except ValueError:
        check(True, 'B6-d N<G 시 ValueError')


def test_b7_group_finished_at(app, db, Runner, Group):
    section('B7. 조 완주 시 group.finished_at 설정')
    reset_db()
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess['is_admin'] = True
        ok, _ = init_via_wizard(c, n=4, g=2)
        check(ok, f'B7 init N=4 G=2 성공')
        c.post('/admin/toggle-open',
               headers={'X-Requested-With': 'XMLHttpRequest'})

        # 조 1의 두 주자를 모두 pass 처리하여 완주
        with app.app_context():
            r1, r2 = Runner.query.filter_by(group_id=1).order_by(Runner.run_order).all()
            r1_id, r2_id = r1.id, r2.id

        c.post(f'/admin/pass/{r1_id}', data={'reason': 'b7'},
               headers={'X-Requested-With': 'XMLHttpRequest'})
        c.post(f'/admin/pass/{r2_id}', data={'reason': 'b7'},
               headers={'X-Requested-With': 'XMLHttpRequest'})

        with app.app_context():
            g1 = Group.query.get(1)
        check(g1.finished_at is not None,
              f'B7 조 1 모두 종료 → group.finished_at 설정 (실제 {g1.finished_at})')


def main():
    reset_db()
    from app import app
    from models import db, Runner, Group

    try:
        test_b1_minimum(app, db, Runner)
        test_b2_max(app, db, Runner)
        test_b3_one_per_group(app, db, Runner)
        test_b4_uneven_distribution(app, db)
        test_b5_invalid_csv(app, db)
        test_b6_compute_group_sizes_edge(app, db)
        test_b7_group_finished_at(app, db, Runner, Group)
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
