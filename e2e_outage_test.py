#!/usr/bin/env python3
"""e2e_outage_test.py — 주자 순서 변경·서버 다운타임·이중 active 시나리오 테스트.

기본 e2e_test.py 가 정상 경로 위주라면, 이 파일은:
A. 주자 순서 변경 종합 (자기 미루기·관리자 미루기 모든 모드)
B. 서버 다운→재시작 시나리오 (로그인 주자 잔존 포함)
C. 이중 active 발생·해소
D. race 보호 (None 가드)

실행:
    python e2e_outage_test.py

각 섹션은 독립적으로 DB를 초기화 후 시작.
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


def section(title):
    print()
    print('=' * 70)
    print(f' {title}')
    print('=' * 70)


def reset_db():
    """DB와 settings.json을 깨끗한 초기 상태로."""
    if os.path.exists('settings.json'):
        os.remove('settings.json')
    subprocess.run(['python', 'init_db.py'],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def current_epoch():
    from app import _read_settings
    return int(_read_settings().get('session_epoch', 1))


def login_as(c, runner_id):
    """테스트에서 직접 세션 설정 — login 라우트를 거치지 않음."""
    with c.session_transaction() as sess:
        sess['runner_id'] = runner_id
        sess['session_epoch'] = current_epoch()


def reset_client(c):
    with c.session_transaction() as sess:
        sess.clear()
    try:
        c.cookie_jar.clear()
    except AttributeError:
        try:
            c._cookies.clear()
        except AttributeError:
            pass


# ============================================================
# A. 주자 순서 변경 종합 시나리오
# ============================================================
def test_order_changes():
    section('A. 주자 순서 변경 종합')
    reset_db()
    from app import app
    from models import db, Runner, SpareProblem

    with app.test_client() as c, app.app_context():
        # 챌린지 공개
        with c.session_transaction() as sess:
            sess['is_admin'] = True
        c.post('/admin/toggle-open',
               headers={'X-Requested-With': 'XMLHttpRequest'})

        # ----- A-1: 본인이 3칸 뒤로 -----
        with app.app_context():
            r1 = Runner.query.filter_by(group_id=1, run_order=1).first()
            r1.started_at = datetime.utcnow()
            db.session.commit()
            rid = r1.id
            spare0 = SpareProblem.query.filter_by(consumed_at=None).count()
        reset_client(c)
        login_as(c, rid)
        r = c.post('/defer', data={'steps': '3', 'reason': 'A-1'},
                   follow_redirects=False)
        with app.app_context():
            r1 = db.session.get(Runner, rid)
            mid_r = Runner.query.filter_by(group_id=1, run_order=1).first()
            new4 = Runner.query.filter_by(group_id=1, run_order=4).first()
            spare1 = SpareProblem.query.filter_by(consumed_at=None).count()
        check(r.status_code == 302 and '/defer/result' in r.headers.get('Location', ''),
              'A-1 본인 N=3칸 → /defer/result로')
        check(r1.run_order == 4 and r1.deferred_count == 1,
              'A-1 본인 run_order=4, deferred_count=1')
        check(new4.id == rid, 'A-1 새 4번 자리가 본인')
        check(mid_r.id != rid, 'A-1 1번 자리에 다른 주자 시프트됨')
        check(spare1 == spare0 - 1, 'A-1 스페어 1개 소비됨')

        # ----- A-2: 본인이 특정 주자와 1:1 swap -----
        reset_db()
        with c.session_transaction() as sess:
            sess['is_admin'] = True
        c.post('/admin/toggle-open',
               headers={'X-Requested-With': 'XMLHttpRequest'})
        with app.app_context():
            r1 = Runner.query.filter_by(group_id=1, run_order=1).first()
            r1.started_at = datetime.utcnow()
            r5 = Runner.query.filter_by(group_id=1, run_order=5).first()
            r2 = Runner.query.filter_by(group_id=1, run_order=2).first()
            r3 = Runner.query.filter_by(group_id=1, run_order=3).first()
            r4 = Runner.query.filter_by(group_id=1, run_order=4).first()
            db.session.commit()
            rid, r2_kn, r3_kn, r4_kn, r5_id = r1.id, r2.knox_id, r3.knox_id, r4.knox_id, r5.id
        reset_client(c)
        login_as(c, rid)
        r = c.post('/defer/swap', data={'target_order': '5', 'reason': 'A-2'},
                   follow_redirects=False)
        with app.app_context():
            r1 = db.session.get(Runner, rid)
            r5_after = db.session.get(Runner, r5_id)
            r2_after = Runner.query.filter_by(group_id=1, run_order=2).first()
            r3_after = Runner.query.filter_by(group_id=1, run_order=3).first()
            r4_after = Runner.query.filter_by(group_id=1, run_order=4).first()
        check(r.status_code == 302, 'A-2 swap → 302')
        check(r1.run_order == 5 and r5_after.run_order == 1,
              'A-2 1번↔5번 위치 교환됨')
        check(r5_after.status == 'active', 'A-2 5번 주자가 active로 활성화')
        check(r2_after.knox_id == r2_kn and r3_after.knox_id == r3_kn and r4_after.knox_id == r4_kn,
              'A-2 사이 주자(2,3,4번) 영향 없음')

        # ----- A-3: 관리자 mode=max -----
        reset_db()
        with c.session_transaction() as sess:
            sess['is_admin'] = True
        c.post('/admin/toggle-open',
               headers={'X-Requested-With': 'XMLHttpRequest'})
        with app.app_context():
            r1 = Runner.query.filter_by(group_id=1, run_order=1).first()
            rid = r1.id
            max_order = db.session.query(db.func.max(Runner.run_order))\
                .filter_by(group_id=1).scalar()
        r = c.post(f'/admin/defer/{rid}',
                   data={'mode': 'max', 'reason': 'A-3'},
                   headers={'X-Requested-With': 'XMLHttpRequest'})
        with app.app_context():
            r1 = db.session.get(Runner, rid)
        check(r.status_code == 200 and r.get_json().get('ok'), 'A-3 admin mode=max 성공')
        check(r1.run_order == max_order, f'A-3 run_order={max_order} (max)')

        # ----- A-4: 관리자 mode=steps -----
        reset_db()
        with c.session_transaction() as sess:
            sess['is_admin'] = True
        c.post('/admin/toggle-open',
               headers={'X-Requested-With': 'XMLHttpRequest'})
        with app.app_context():
            r1 = Runner.query.filter_by(group_id=1, run_order=1).first()
            rid = r1.id
        r = c.post(f'/admin/defer/{rid}',
                   data={'mode': 'steps', 'steps': '4', 'reason': 'A-4'},
                   headers={'X-Requested-With': 'XMLHttpRequest'})
        with app.app_context():
            r1 = db.session.get(Runner, rid)
        check(r.status_code == 200 and r.get_json().get('ok'), 'A-4 admin mode=steps 성공')
        check(r1.run_order == 5, 'A-4 run_order=5 (1+4)')

        # ----- A-5: 관리자 mode=swap (active 주자 1:1 swap) -----
        reset_db()
        with c.session_transaction() as sess:
            sess['is_admin'] = True
        c.post('/admin/toggle-open',
               headers={'X-Requested-With': 'XMLHttpRequest'})
        with app.app_context():
            r1 = Runner.query.filter_by(group_id=1, run_order=1).first()
            rid = r1.id
        r = c.post(f'/admin/defer/{rid}',
                   data={'mode': 'swap', 'target_order': '7', 'reason': 'A-5'},
                   headers={'X-Requested-With': 'XMLHttpRequest'})
        with app.app_context():
            r1 = db.session.get(Runner, rid)
            new_active = Runner.query.filter_by(group_id=1, run_order=1).first()
        check(r.status_code == 200, 'A-5 admin mode=swap 성공')
        check(r1.run_order == 7 and r1.status == 'waiting', 'A-5 본인 7번/waiting')
        check(new_active.status == 'active', 'A-5 새 1번이 active')

        # ----- A-6: 관리자 mode=reorder_swap (waiting↔waiting 양방향) -----
        reset_db()
        with c.session_transaction() as sess:
            sess['is_admin'] = True
        with app.app_context():
            r5 = Runner.query.filter_by(group_id=1, run_order=5).first()
            r10 = Runner.query.filter_by(group_id=1, run_order=10).first()
            r5_id, r5_def = r5.id, r5.deferred_count
            r10_id, r10_def = r10.id, r10.deferred_count
            r5_problem = r5.problem_text[:40]
        r = c.post(f'/admin/defer/{r5_id}',
                   data={'mode': 'reorder_swap', 'target_order': '10',
                         'reason': 'A-6'},
                   headers={'X-Requested-With': 'XMLHttpRequest'})
        with app.app_context():
            r5_a = db.session.get(Runner, r5_id)
            r10_a = db.session.get(Runner, r10_id)
        check(r.status_code == 200 and r.get_json().get('ok'),
              'A-6 reorder_swap 성공')
        check(r5_a.run_order == 10 and r10_a.run_order == 5,
              'A-6 5번 ↔ 10번 위치 교환')
        check(r5_a.deferred_count == r5_def and r10_a.deferred_count == r10_def,
              'A-6 deferred_count 변화 없음 (패널티 X)')
        check(r5_a.problem_text[:40] == r5_problem,
              'A-6 문제 교체 없음 (5번 문제 유지)')


# ============================================================
# B. 서버 다운→재시작 시나리오
# ============================================================
def test_outage_recovery():
    section('B. 서버 다운→재시작 시나리오')
    reset_db()
    from app import app, _read_settings, _write_settings
    from models import db, Runner

    with app.test_client() as c, app.app_context():
        # ----- B-1: 로그인 주자가 있는 상태에서 종료/재시작 → DB 상태 보존 -----
        # 운영 공개
        with c.session_transaction() as sess:
            sess['is_admin'] = True
        c.post('/admin/toggle-open',
               headers={'X-Requested-With': 'XMLHttpRequest'})
        with app.app_context():
            r1 = Runner.query.filter_by(group_id=1, run_order=1).first()
            login_time = datetime.utcnow() - timedelta(minutes=15)
            r1.started_at = login_time
            r1.attempts = 2
            db.session.commit()
            rid = r1.id
            saved_started = r1.started_at
            saved_attempts = r1.attempts
            saved_status = r1.status

        # "서버 종료" 시뮬레이션 (db.session.remove + 새 컨텍스트)
        db.session.remove()
        # "재시작" 후 DB 상태 검증
        with app.app_context():
            r1 = Runner.query.filter_by(group_id=1, run_order=1).first()
            check(r1.started_at == saved_started, 'B-1 started_at 보존됨')
            check(r1.attempts == saved_attempts, 'B-1 attempts 보존됨')
            check(r1.status == saved_status, 'B-1 status 보존됨')

        # ----- B-2: 세션 쿠키 유효성 (재시작 후에도 epoch 일치하면 로그인 유지) -----
        reset_client(c)
        login_as(c, rid)
        r = c.get('/challenge')
        check(r.status_code == 200, 'B-2 재시작 후 세션으로 /challenge 정상 접근')

        # ----- B-3: 비공개 토글 → 강제 로그아웃 (session_epoch 증가) -----
        epoch_before = current_epoch()
        with c.session_transaction() as sess:
            sess['is_admin'] = True
        c.post('/admin/toggle-open',
               headers={'X-Requested-With': 'XMLHttpRequest'})  # 비공개 전환
        epoch_after = current_epoch()
        check(epoch_after == epoch_before + 1,
              f'B-3 비공개 토글 시 session_epoch +1 ({epoch_before}→{epoch_after})')

        # 기존 주자 세션은 무효화
        with c.session_transaction() as sess:
            sess.clear()
            sess['runner_id'] = rid
            sess['session_epoch'] = epoch_before  # 옛 epoch
        r = c.get('/challenge')
        with c.session_transaction() as sess:
            check('runner_id' not in sess, 'B-3 옛 session_epoch면 runner_id 자동 제거')

        # ----- B-4: active 리셋 → started_at None + 새 문제 -----
        with app.app_context():
            r1 = db.session.get(Runner, rid)
            old_problem_answer = r1.correct_answer
        with c.session_transaction() as sess:
            sess['is_admin'] = True
        r = c.post(f'/admin/reset/{rid}',
                   headers={'X-Requested-With': 'XMLHttpRequest'})
        rj = r.get_json()
        with app.app_context():
            r1 = db.session.get(Runner, rid)
        check(rj.get('ok') and rj.get('kind') == 'active_reset',
              'B-4 active 리셋 성공')
        check(r1.started_at is None, 'B-4 started_at = None')
        check(r1.attempts == 0, 'B-4 attempts = 0')
        check(r1.correct_answer != old_problem_answer, 'B-4 새 문제 교체됨')

        # ----- B-5: 재공개 → 재로그인 → 새 시계 -----
        c.post('/admin/toggle-open',
               headers={'X-Requested-With': 'XMLHttpRequest'})  # 공개
        reset_client(c)
        login_as(c, rid)  # 재로그인 시뮬 (실제 라우트의 login은 password 필요)
        # 실 login 시뮬: /challenge 진입 → started_at 자동 set
        before_visit = datetime.utcnow()
        c.get('/challenge')
        with app.app_context():
            r1 = db.session.get(Runner, rid)
        check(r1.started_at is not None, 'B-5 /challenge 방문 시 started_at 새로 set')
        check(r1.started_at >= before_visit - timedelta(seconds=2),
              'B-5 started_at이 방문 시점 근처')


# ============================================================
# C. 이중 active 발생/해소
# ============================================================
def test_double_active():
    section('C. 이중 active 발생/해소')
    reset_db()
    from app import app
    from models import db, Runner

    with app.test_client() as c, app.app_context():
        with c.session_transaction() as sess:
            sess['is_admin'] = True
        c.post('/admin/toggle-open',
               headers={'X-Requested-With': 'XMLHttpRequest'})

        # ----- C-1: completed 리셋 + 다음 주자 미시작 → 단일 active -----
        with app.app_context():
            r1 = Runner.query.filter_by(group_id=1, run_order=1).first()
            r2 = Runner.query.filter_by(group_id=1, run_order=2).first()
            r1.status = 'completed'
            r1.started_at = datetime.utcnow() - timedelta(minutes=5)
            r1.completed_at = datetime.utcnow()
            r2.status = 'active'
            r2.password = 'TEST1234'
            r2.started_at = None  # 아직 로그인 안 함
            db.session.commit()
            r1_id, r2_id = r1.id, r2.id

        r = c.post(f'/admin/reset/{r1_id}',
                   headers={'X-Requested-With': 'XMLHttpRequest'})
        with app.app_context():
            r1 = db.session.get(Runner, r1_id)
            r2 = db.session.get(Runner, r2_id)
            actives = Runner.query.filter_by(group_id=1, status='active').count()
        check(r.status_code == 200, 'C-1 리셋 성공')
        check(r1.status == 'active' and r2.status == 'waiting',
              'C-1 1번 active, 2번 waiting으로 복귀')
        check(actives == 1, 'C-1 단일 active (이중 active 안 만들어짐)')

        # ----- C-2: completed 리셋 + 다음 주자 시작됨 → 이중 active 생성 -----
        reset_db()
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
            r2.started_at = datetime.utcnow() - timedelta(minutes=2)  # 이미 로그인
            db.session.commit()
            r1_id, r2_id = r1.id, r2.id

        r = c.post(f'/admin/reset/{r1_id}',
                   headers={'X-Requested-With': 'XMLHttpRequest'})
        rj = r.get_json()
        check(r.status_code == 409 and rj.get('error_code') == 'NEXT_RUNNER_STARTED',
              f'C-2 이중 active 방지: 리셋 거부 (409 NEXT_RUNNER_STARTED)')
        with app.app_context():
            actives = Runner.query.filter_by(group_id=1, status='active').count()
        check(actives == 1, f'C-2 active 그대로 1명 (실제: {actives})')

        # ----- C-3: 다음 주자 먼저 처리 후 리셋 가능 -----
        c.post(f'/admin/defer/{r2_id}', data={'mode': 'max'},
               headers={'X-Requested-With': 'XMLHttpRequest'})
        r = c.post(f'/admin/reset/{r1_id}',
                   headers={'X-Requested-With': 'XMLHttpRequest'})
        with app.app_context():
            actives = Runner.query.filter_by(group_id=1, status='active').count()
        check(actives == 1, f'C-3 next defer 후 reset OK (단일 active: {actives})')


# ============================================================
# D. race 보호 (None 가드)
# ============================================================
def test_race_guards():
    section('D. race 보호 (None 가드)')
    reset_db()
    from app import app
    from models import db, Runner

    with app.test_client() as c, app.app_context():
        with c.session_transaction() as sess:
            sess['is_admin'] = True
        c.post('/admin/toggle-open',
               headers={'X-Requested-With': 'XMLHttpRequest'})

        # ----- D-1: started_at=None일 때 /submit 호출 → 자동으로 NOW로 set -----
        with app.app_context():
            r1 = Runner.query.filter_by(group_id=1, run_order=1).first()
            r1.started_at = None
            r1.attempts = 0
            ans = r1.correct_answer
            db.session.commit()
            rid = r1.id

        reset_client(c)
        login_as(c, rid)
        before = datetime.utcnow()
        r = c.post('/submit', data={'answer': '99999'})  # 일부러 오답
        with app.app_context():
            r1 = db.session.get(Runner, rid)
        check(r1.started_at is not None, 'D-1 오답 제출 후 started_at None → NOW 자동 설정')
        check(r1.started_at >= before - timedelta(seconds=2),
              'D-1 started_at이 제출 시점 근처')

        # ----- D-2: started_at=None일 때 /challenge 방문 → 자동으로 NOW로 set -----
        with app.app_context():
            r1 = db.session.get(Runner, rid)
            r1.started_at = None
            db.session.commit()
        before = datetime.utcnow()
        c.get('/challenge')
        with app.app_context():
            r1 = db.session.get(Runner, rid)
        check(r1.started_at is not None and r1.started_at >= before - timedelta(seconds=2),
              'D-2 /challenge 방문 시 started_at None → NOW 자동 설정')

        # ----- D-3: get_individual_rankings가 None 안전 처리 -----
        from app import get_individual_rankings
        with app.app_context():
            # 임의 주자 completed + started_at=None 강제
            bad = Runner.query.filter_by(group_id=2, run_order=1).first()
            bad.status = 'completed'
            bad.completed_at = datetime.utcnow()
            bad.started_at = None
            db.session.commit()
            try:
                items = get_individual_rankings(limit=20)
                check(True, 'D-3 None started_at 있어도 예외 없이 동작')
            except TypeError as e:
                check(False, 'D-3 None started_at 있을 때 TypeError 발생',
                      detail=str(e))


# ============================================================
# 메인
# ============================================================
def main():
    # 테스트는 test_client만 사용하므로 외부 프로세스 종료 불필요
    try:
        test_order_changes()
        test_outage_recovery()
        test_double_active()
        test_race_guards()
    except Exception as e:
        import traceback
        print(f'\n[FATAL] 테스트 실행 중 예외:\n{traceback.format_exc()}')
        _results['fail'] += 1
        _results['failures'].append(('exception', str(e)))

    # 마무리: 기본 DB 복구
    reset_db()
    if os.path.exists('settings.json'):
        os.remove('settings.json')

    print()
    print('=' * 70)
    print(f' 결과: {_results["pass"]}개 성공 / {_results["fail"]}개 실패')
    print('=' * 70)
    if _results['failures']:
        print('\n[실패 항목]')
        for name, detail in _results['failures']:
            print(f'  - {name}')
            if detail:
                print(f'      {detail}')
    return 0 if _results['fail'] == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
