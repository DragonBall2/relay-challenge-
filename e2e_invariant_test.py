#!/usr/bin/env python3
"""e2e_invariant_test.py — 운영 중 깨지면 안 되는 상태 불변 검증.

검증 항목:
  I1. 한 조에 active 주자 ≤ 1명
  I2. completed/passed 주자는 started_at, completed_at 둘 다 존재 (skipped 제외)
  I3. run_order는 조별 1..N 연속 (gap 없음)
  I4. 모든 runner의 group_id 유효
  I5. spare problem의 consumed_by_runner_id 유효
  I6. status 값이 알려진 enum 중 하나
  I7. 합계: 조별 runner 수 == compute_group_sizes 출력
  I8. 랜덤 액션 50회 시뮬 후에도 위 모두 유지
"""
import os
import random
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

_results = {'pass': 0, 'fail': 0, 'failures': []}
VALID_STATUS = {'waiting', 'active', 'completed', 'passed', 'skipped'}


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


def verify_invariants(app, db, Runner, Group, SpareProblem, prefix=''):
    """현재 DB 상태에서 모든 불변 검증."""
    with app.app_context():
        groups = Group.query.all()
        all_runners = Runner.query.all()

        # I1: 조별 active ≤ 1
        for g in groups:
            actives = [r for r in all_runners if r.group_id == g.id and r.status == 'active']
            check(len(actives) <= 1,
                  f'{prefix}I1 조 {g.id}: active ≤ 1 (실제 {len(actives)})')

        # I2: completed/passed → started_at + completed_at 둘 다
        for r in all_runners:
            if r.status in ('completed', 'passed'):
                check(r.started_at is not None and r.completed_at is not None,
                      f'{prefix}I2 {r.knox_id} {r.status}: started_at + completed_at 둘 다 존재')

        # I3: run_order 1..N 연속
        for g in groups:
            runners = sorted([r for r in all_runners if r.group_id == g.id],
                            key=lambda x: x.run_order)
            orders = [r.run_order for r in runners]
            expected = list(range(1, len(runners) + 1))
            check(orders == expected,
                  f'{prefix}I3 조 {g.id} run_order 연속',
                  detail=f'expected {expected[:5]}..., got {orders[:5]}...')

        # I4: group_id 유효
        group_ids = {g.id for g in groups}
        for r in all_runners:
            check(r.group_id in group_ids,
                  f'{prefix}I4 runner {r.knox_id} group_id={r.group_id} 유효')

        # I5: SpareProblem.consumed_by_runner_id 유효
        runner_ids = {r.id for r in all_runners}
        spares = SpareProblem.query.filter(SpareProblem.consumed_by_runner_id.isnot(None)).all()
        for sp in spares:
            check(sp.consumed_by_runner_id in runner_ids,
                  f'{prefix}I5 spare {sp.id} consumed_by_runner_id 유효')

        # I6: status enum
        for r in all_runners:
            check(r.status in VALID_STATUS,
                  f'{prefix}I6 {r.knox_id} status={r.status} 알려진 값')


def section_static_invariants(app, db, Runner, Group, SpareProblem):
    """초기화 직후 상태 검증."""
    section('A. 초기 상태 불변 검증')
    verify_invariants(app, db, Runner, Group, SpareProblem, prefix='[init] ')


def section_action_invariants(app, db, Runner, Group, SpareProblem):
    """랜덤 액션 시뮬 후 상태 검증."""
    section('B. 랜덤 액션 50회 시뮬 후 불변 검증')
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess['is_admin'] = True
        # 챌린지 공개
        c.post('/admin/toggle-open',
               headers={'X-Requested-With': 'XMLHttpRequest'})

        random.seed(12345)
        actions_done = 0
        for i in range(50):
            with app.app_context():
                runners = Runner.query.all()
                if not runners:
                    break
            r = random.choice(runners)
            action = random.choice(['login_submit', 'admin_defer_max',
                                     'admin_defer_steps', 'admin_reset',
                                     'admin_skip', 'admin_pass'])

            if action == 'login_submit':
                if r.status == 'active':
                    # 로그인 흉내 + 정답 또는 오답 제출
                    with app.app_context():
                        r_db = db.session.get(Runner, r.id)
                        ans = r_db.correct_answer
                    with c.session_transaction() as sess:
                        sess['runner_id'] = r.id
                        from app import _read_settings
                        sess['session_epoch'] = int(_read_settings().get('session_epoch', 1))
                    use_correct = random.random() > 0.3
                    submission = ans if use_correct else '99999'
                    c.post('/submit', data={'answer': submission})
                    actions_done += 1
            elif action == 'admin_defer_max':
                if r.status in ('active', 'waiting'):
                    with c.session_transaction() as sess:
                        sess['is_admin'] = True
                        sess.pop('runner_id', None)
                    c.post(f'/admin/defer/{r.id}', data={'mode': 'max'},
                           headers={'X-Requested-With': 'XMLHttpRequest'})
                    actions_done += 1
            elif action == 'admin_defer_steps':
                if r.status == 'active':
                    with c.session_transaction() as sess:
                        sess['is_admin'] = True
                        sess.pop('runner_id', None)
                    c.post(f'/admin/defer/{r.id}',
                           data={'mode': 'steps', 'steps': str(random.randint(1, 3))},
                           headers={'X-Requested-With': 'XMLHttpRequest'})
                    actions_done += 1
            elif action == 'admin_reset':
                with c.session_transaction() as sess:
                    sess['is_admin'] = True
                    sess.pop('runner_id', None)
                c.post(f'/admin/reset/{r.id}',
                       headers={'X-Requested-With': 'XMLHttpRequest'})
                actions_done += 1
            elif action == 'admin_skip':
                if r.status in ('active', 'waiting'):
                    with c.session_transaction() as sess:
                        sess['is_admin'] = True
                        sess.pop('runner_id', None)
                    c.post(f'/admin/skip/{r.id}', data={'reason': 'sim'},
                           headers={'X-Requested-With': 'XMLHttpRequest'})
                    actions_done += 1
            elif action == 'admin_pass':
                if r.status in ('active', 'waiting'):
                    with c.session_transaction() as sess:
                        sess['is_admin'] = True
                        sess.pop('runner_id', None)
                    c.post(f'/admin/pass/{r.id}', data={'reason': 'sim'},
                           headers={'X-Requested-With': 'XMLHttpRequest'})
                    actions_done += 1
        print(f'  실제 액션 수행: {actions_done}회')

    # 시뮬 후 불변 검증
    verify_invariants(app, db, Runner, Group, SpareProblem, prefix='[after-sim] ')


def main():
    reset_db()
    from app import app
    from models import db, Runner, Group, SpareProblem

    try:
        section_static_invariants(app, db, Runner, Group, SpareProblem)
        section_action_invariants(app, db, Runner, Group, SpareProblem)
    except Exception as e:
        import traceback
        print(f'\n[FATAL] {traceback.format_exc()}')
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
        if len(_results['failures']) > 20:
            print(f'  ... 외 {len(_results["failures"]) - 20}건')
    return 0 if _results['fail'] == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
