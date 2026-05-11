#!/usr/bin/env python3
"""e2e_adversarial_test.py — 적대적 입력 / 권한 우회 시도 방어 검증.

검증 항목:
  AD1. 비로그인 + 보호된 라우트 직접 POST → 차단 또는 redirect
  AD2. 비admin + admin 라우트 직접 POST → 차단
  AD3. SQL injection 시도 (답안, 사유, 후기) → 안전 처리
  AD4. XSS 시도 (review, reason) → escape 처리
  AD5. 매우 긴 답안 (10KB) → 거부 또는 잘림
  AD6. 잘못된 mode/steps/target_order
  AD7. 존재하지 않는 runner_id로 admin 액션
"""
import os
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


def test_unauthenticated(app, db, Runner):
    section('AD1. 비로그인 / 비admin 권한 우회 시도')
    with app.test_client() as c:
        # 챌린지 공개
        with c.session_transaction() as sess:
            sess['is_admin'] = True
        c.post('/admin/toggle-open',
               headers={'X-Requested-With': 'XMLHttpRequest'})

        # 비로그인 상태에서 /submit 직접 POST
        with c.session_transaction() as sess:
            sess.clear()
        r = c.post('/submit', data={'answer': '12345'})
        check(r.status_code in (302, 401, 403),
              f'AD1-a /submit 비로그인 → 차단/redirect (실제 {r.status_code})')

        # 비로그인 /defer POST
        r = c.post('/defer', data={'steps': '1'})
        check(r.status_code in (302, 401, 403),
              f'AD1-b /defer 비로그인 → 차단/redirect (실제 {r.status_code})')

        # 비admin /admin/defer POST
        with app.app_context():
            rid = Runner.query.first().id
        r = c.post(f'/admin/defer/{rid}', data={'mode': 'max'})
        check(r.status_code in (302, 401, 403),
              f'AD1-c /admin/defer 비admin → 차단 (실제 {r.status_code})')

        # 비admin /admin/init/commit
        r = c.post('/admin/init/commit', json={'confirm': 'INIT'})
        check(r.status_code in (302, 401, 403),
              f'AD1-d /admin/init/commit 비admin → 차단 (실제 {r.status_code})')

        # 비admin /admin/dashboard
        r = c.get('/admin/dashboard')
        check(r.status_code == 302,
              f'AD1-e /admin/dashboard 비admin → 302 (실제 {r.status_code})')


def test_sql_injection(app, db, Runner):
    section('AD2. SQL injection 시도')
    reset_db()
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

        from app import _read_settings
        with c.session_transaction() as sess:
            sess['runner_id'] = rid
            sess['session_epoch'] = int(_read_settings().get('session_epoch', 1))

        injections = [
            "'; DROP TABLE runners; --",
            "1' OR '1'='1",
            "; UPDATE runners SET status='completed'; --",
            "<script>alert('x')</script>",
        ]

        for inj in injections:
            r = c.post('/submit', data={'answer': inj})
            check(r.status_code in (200, 302),
                  f'AD2 SQL/XSS in answer: {inj[:30]}... → 정상 처리')

        # DB 무결성 확인 (테이블 안 떨어졌는지)
        with app.app_context():
            count = Runner.query.count()
        check(count == 130,
              f'AD2 SQL injection 시도 후에도 130명 그대로 (실제 {count})')


def test_xss_in_review(app, db, Runner):
    section('AD3. XSS 시도 (후기 / 사유 필드)')
    reset_db()
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess['is_admin'] = True
        c.post('/admin/toggle-open',
               headers={'X-Requested-With': 'XMLHttpRequest'})

        # 한 runner를 completed로 만든 뒤 review 작성
        with app.app_context():
            r1 = Runner.query.filter_by(group_id=1, run_order=1).first()
            r1.status = 'completed'
            r1.started_at = datetime.utcnow()
            r1.completed_at = datetime.utcnow()
            db.session.commit()
            rid = r1.id

        from app import _read_settings
        with c.session_transaction() as sess:
            sess.clear()
            sess['runner_id'] = rid
            sess['session_epoch'] = int(_read_settings().get('session_epoch', 1))

        xss_payload = '<script>alert("xss")</script><img src=x onerror=alert(1)>'
        r = c.post('/review', data={'review': xss_payload})
        check(r.status_code == 302, 'AD3-a XSS in review POST 정상 처리')

        # 관리자 대시보드에서 후기 모달 렌더링 — 스크립트 태그 raw 노출 X
        with c.session_transaction() as sess:
            sess.clear()
            sess['is_admin'] = True
        r = c.get('/admin/dashboard')
        body = r.data.decode('utf-8', errors='ignore')
        # Jinja2 자동 escape: <script>가 &lt;script&gt;로 escape
        check('<script>alert("xss")</script>' not in body,
              'AD3-b 대시보드에서 script 태그 raw 출력 안 됨 (Jinja2 escape)')


def test_long_answer(app, db, Runner):
    section('AD4. 매우 긴 답안 / 비정상 입력')
    reset_db()
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

        from app import _read_settings
        with c.session_transaction() as sess:
            sess['runner_id'] = rid
            sess['session_epoch'] = int(_read_settings().get('session_epoch', 1))

        # 1MB 답안
        big = 'A' * (1024 * 1024)
        r = c.post('/submit', data={'answer': big})
        check(r.status_code in (200, 302, 413),
              f'AD4-a 1MB 답안 → 정상 처리/거부 (실제 {r.status_code})')

        # 빈 답안
        r = c.post('/submit', data={'answer': ''})
        check(r.status_code == 302,
              f'AD4-b 빈 답안 → 302 (실제 {r.status_code})')

        # 매우 큰 숫자 답안
        r = c.post('/submit', data={'answer': '9' * 1000})
        check(r.status_code in (200, 302),
              f'AD4-c 매우 큰 숫자 답안 → 정상 처리 (실제 {r.status_code})')


def test_invalid_admin_params(app, db, Runner):
    section('AD5. 잘못된 admin 파라미터')
    reset_db()
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess['is_admin'] = True

        with app.app_context():
            rid = Runner.query.filter_by(group_id=1, run_order=1).first().id

        # 잘못된 mode
        r = c.post(f'/admin/defer/{rid}', data={'mode': 'unknown_mode'},
                   headers={'X-Requested-With': 'XMLHttpRequest'})
        check(r.status_code in (200, 400),
              f'AD5-a 알 수 없는 mode → 400 또는 max 폴백 (실제 {r.status_code})')

        # 음수 steps
        r = c.post(f'/admin/defer/{rid}', data={'mode': 'steps', 'steps': '-1'},
                   headers={'X-Requested-With': 'XMLHttpRequest'})
        check(r.status_code == 400,
              f'AD5-b 음수 steps → 400 (실제 {r.status_code})')

        # 매우 큰 steps
        r = c.post(f'/admin/defer/{rid}', data={'mode': 'steps', 'steps': '99999'},
                   headers={'X-Requested-With': 'XMLHttpRequest'})
        check(r.status_code == 400,
              f'AD5-c 큰 steps → 400 (실제 {r.status_code})')

        # 존재하지 않는 target_order
        r = c.post(f'/admin/defer/{rid}', data={'mode': 'swap', 'target_order': '99'},
                   headers={'X-Requested-With': 'XMLHttpRequest'})
        check(r.status_code == 400,
              f'AD5-d 존재하지 않는 target_order → 400 (실제 {r.status_code})')

        # 존재하지 않는 runner_id로 admin 액션
        r = c.post('/admin/defer/99999', data={'mode': 'max'},
                   headers={'X-Requested-With': 'XMLHttpRequest'})
        check(r.status_code == 404,
              f'AD5-e 존재하지 않는 runner_id → 404 (실제 {r.status_code})')


def main():
    reset_db()
    from app import app
    from models import db, Runner

    try:
        test_unauthenticated(app, db, Runner)
        test_sql_injection(app, db, Runner)
        test_xss_in_review(app, db, Runner)
        test_long_answer(app, db, Runner)
        test_invalid_admin_params(app, db, Runner)
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
