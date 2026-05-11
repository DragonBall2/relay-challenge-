#!/usr/bin/env python3
"""e2e_race_test.py — 운영에서 발견된/의심되는 race 조건 시뮬.

검증 항목:
  R1. /submit과 admin_reset(active)이 같은 runner에 동시
  R2. 두 admin 액션이 같은 runner에 동시 (defer + skip)
  R3. completed 리셋이 next_runner started 상태에서 호출 → 이중 active 감지
  R4. admin_defer가 이미 deferred된 runner에 또 호출
  R5. 비공개 토글 후 session_epoch 변경, 옛 세션으로 /submit 시도
"""
import os
import subprocess
import sys
from datetime import datetime, timedelta

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


def test_r1_submit_vs_reset(app, db, Runner):
    """R1: /submit이 처리되는 도중 admin_reset이 들어옴 (timing reversed)."""
    section('R1. /submit과 admin_reset(active) 동시 시뮬')
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess['is_admin'] = True
        c.post('/admin/toggle-open',
               headers={'X-Requested-With': 'XMLHttpRequest'})

        with app.app_context():
            r1 = Runner.query.filter_by(group_id=1, run_order=1).first()
            r1.started_at = datetime.utcnow()
            db.session.commit()
            rid = r1.id
            ans = r1.correct_answer

        # 시뮬 1: admin reset 먼저 → /submit 시도 (race result: submit이 None된 started_at 보고 가드 발동)
        c.post(f'/admin/reset/{rid}',
               headers={'X-Requested-With': 'XMLHttpRequest'})
        with app.app_context():
            r1 = db.session.get(Runner, rid)
            # 리셋이 문제도 새로 교체했으므로 새 정답을 가져와야 함
            new_ans = r1.correct_answer
            check(r1.started_at is None, 'R1-a admin_reset → started_at None')
            check(new_ans != ans, 'R1-a2 reset이 문제도 새로 교체 (새 정답)')

        # /submit 호출 (세션은 reset 전 상태로 가정, 새 정답으로 제출)
        from app import _read_settings
        with c.session_transaction() as sess:
            sess.clear()
            sess['runner_id'] = rid
            sess['session_epoch'] = int(_read_settings().get('session_epoch', 1))
        r = c.post('/submit', data={'answer': new_ans})
        with app.app_context():
            r1 = db.session.get(Runner, rid)
        check(r.status_code == 302, 'R1-b reset 후 /submit 처리 (302)')
        check(r1.status == 'completed', 'R1-c 정답 인정 → completed')
        check(r1.started_at is not None, 'R1-d submit이 started_at NOW로 가드 발동')
        check(r1.completed_at is not None, 'R1-e completed_at 존재')


def test_r2_concurrent_admin_actions(app, db, Runner):
    """R2: 같은 runner에 admin defer와 admin skip이 연이어 호출."""
    section('R2. 같은 runner에 admin_defer + admin_skip 연속')
    reset_db()
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess['is_admin'] = True
        c.post('/admin/toggle-open',
               headers={'X-Requested-With': 'XMLHttpRequest'})
        with app.app_context():
            r1 = Runner.query.filter_by(group_id=1, run_order=1).first()
            rid = r1.id

        # defer 먼저 (active → waiting at max_order)
        c.post(f'/admin/defer/{rid}', data={'mode': 'max'},
               headers={'X-Requested-With': 'XMLHttpRequest'})
        with app.app_context():
            r1 = db.session.get(Runner, rid)
            check(r1.status == 'waiting', 'R2-a defer 후 waiting')

        # skip 호출 (waiting → skipped)
        r = c.post(f'/admin/skip/{rid}', data={'reason': 'r2'},
                   headers={'X-Requested-With': 'XMLHttpRequest'})
        with app.app_context():
            r1 = db.session.get(Runner, rid)
            check(r1.status == 'skipped', 'R2-b skip 후 skipped (defer 후에도 적용 가능)')

        # 다음 active runner는 정상 존재
        with app.app_context():
            actives = Runner.query.filter_by(group_id=1, status='active').count()
            check(actives == 1, 'R2-c 조 1에 active 정확히 1명')


def test_r3_double_active_detection(app, db, Runner):
    """R3: completed 리셋 + next runner started → 이중 active 발생 확인."""
    section('R3. 이중 active 발생 시나리오 (운영 사고 재현)')
    reset_db()
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess['is_admin'] = True
        c.post('/admin/toggle-open',
               headers={'X-Requested-With': 'XMLHttpRequest'})
        with app.app_context():
            r1 = Runner.query.filter_by(group_id=1, run_order=1).first()
            r2 = Runner.query.filter_by(group_id=1, run_order=2).first()
            r1.status = 'completed'
            r1.started_at = datetime.utcnow() - timedelta(minutes=5)
            r1.completed_at = datetime.utcnow()
            r2.status = 'active'
            r2.started_at = datetime.utcnow() - timedelta(minutes=2)  # 이미 시작
            db.session.commit()
            r1_id, r2_id = r1.id, r2.id

        # admin_reset 시도 → 다음 주자가 이미 시작했으므로 거부됨 (방지)
        r = c.post(f'/admin/reset/{r1_id}',
                   headers={'X-Requested-With': 'XMLHttpRequest'})
        rj = r.get_json()
        check(r.status_code == 409 and rj.get('error_code') == 'NEXT_RUNNER_STARTED',
              'R3-a 이중 active 방지: 리셋 거부 (409 NEXT_RUNNER_STARTED)')
        with app.app_context():
            actives = Runner.query.filter_by(group_id=1, status='active').count()
        check(actives == 1, 'R3-b 거부됨 → active 그대로 1명')

        # 운영자 가이드: 먼저 2번 처리한 후 1번 리셋
        c.post(f'/admin/defer/{r2_id}', data={'mode': 'max'},
               headers={'X-Requested-With': 'XMLHttpRequest'})
        r = c.post(f'/admin/reset/{r1_id}',
                   headers={'X-Requested-With': 'XMLHttpRequest'})
        with app.app_context():
            actives = Runner.query.filter_by(group_id=1, status='active').count()
        check(actives == 1, 'R3-c 2번을 먼저 defer 후 1번 리셋 → 단일 active')


def test_r4_repeated_defer(app, db, Runner):
    """R4: 같은 active runner를 계속 defer (deferred_count 누적)."""
    section('R4. 같은 active runner를 반복 defer')
    reset_db()
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess['is_admin'] = True
        c.post('/admin/toggle-open',
               headers={'X-Requested-With': 'XMLHttpRequest'})

        # 한 주자를 3번 active 만들어서 defer
        defers_done = 0
        for attempt in range(5):
            with app.app_context():
                actives = Runner.query.filter_by(group_id=1, status='active').all()
                if not actives:
                    break
                rid = actives[0].id
            r = c.post(f'/admin/defer/{rid}', data={'mode': 'max'},
                       headers={'X-Requested-With': 'XMLHttpRequest'})
            if r.status_code == 200 and r.get_json().get('ok'):
                defers_done += 1

        check(defers_done >= 3, f'R4-a 반복 defer 가능 ({defers_done}회)')

        # 모든 active가 deferred되면 빈 active 상태? 또는 누군가 active
        with app.app_context():
            actives = Runner.query.filter_by(group_id=1, status='active').count()
        check(actives <= 1, f'R4-b 반복 defer 후에도 active ≤ 1 (실제 {actives})')


def test_r5_stale_session(app, db, Runner):
    """R5: 비공개 토글로 session_epoch 변경 후 옛 세션으로 /submit."""
    section('R5. session_epoch 변경 후 옛 세션 사용 시도')
    reset_db()
    from app import _read_settings
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess['is_admin'] = True
        c.post('/admin/toggle-open',
               headers={'X-Requested-With': 'XMLHttpRequest'})
        with app.app_context():
            r1 = Runner.query.filter_by(group_id=1, run_order=1).first()
            r1.started_at = datetime.utcnow()
            db.session.commit()
            rid = r1.id
            ans = r1.correct_answer

        epoch_old = int(_read_settings().get('session_epoch', 1))
        with c.session_transaction() as sess:
            sess.clear()
            sess['runner_id'] = rid
            sess['session_epoch'] = epoch_old

        # 비공개 토글 → epoch++
        with c.session_transaction() as sess:
            sess['is_admin'] = True
        c.post('/admin/toggle-open',
               headers={'X-Requested-With': 'XMLHttpRequest'})
        epoch_new = int(_read_settings().get('session_epoch', 1))
        check(epoch_new == epoch_old + 1, 'R5-a 비공개 토글 시 session_epoch +1')

        # 옛 세션으로 /submit 시도
        with c.session_transaction() as sess:
            sess.clear()
            sess['runner_id'] = rid
            sess['session_epoch'] = epoch_old
        r = c.post('/submit', data={'answer': ans})
        with app.app_context():
            r1 = db.session.get(Runner, rid)
        check(r1.status == 'active' and r1.completed_at is None,
              'R5-b 옛 epoch 세션으로 /submit 차단 (상태 변경 없음)')


def main():
    reset_db()
    from app import app
    from models import db, Runner

    try:
        test_r1_submit_vs_reset(app, db, Runner)
        test_r2_concurrent_admin_actions(app, db, Runner)
        test_r3_double_active_detection(app, db, Runner)
        test_r4_repeated_defer(app, db, Runner)
        test_r5_stale_session(app, db, Runner)
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
