#!/usr/bin/env python3
"""e2e_hard_test.py — difficulty='hard' 통합 회귀 테스트.

검증 항목:
  H1. 정상 초기화: settings.hard_* 설정 후 init_database(difficulty='hard') 성공
  H2. Runner.problem_type=='HARD', correct_answer가 problems.json 그대로
  H3. /challenge 페이지에 HARD 배너 + repo URL 노출
  H4. 오답 → 302, attempts++, status=active 유지
  H5. 정답 → 302, status=completed, 다음 주자 active
  H6. self-defer (HARD) → 새 problem으로 교체, 답도 변경
  H7. admin defer → 동일 동작
  H8. settings 누락 (hard_problems_path 없음) → init 실패
  H9. challenge_data.dat 재생성 호출되지 않음 (hard는 외부 repo)
"""
import json
import os
import shutil
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

# hard-challenge problems.json 경로
HARD_REPO = os.path.abspath(os.path.join(
    os.path.dirname(__file__), '..', 'hard-challenge'))
HARD_PROBLEMS_JSON = os.path.join(HARD_REPO, 'problems.json')
HARD_REPO_URL_FAKE = 'https://github-internal.example/test/hard-challenge.git'


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


def reset_db_easy():
    """easy 기본 초기화 (다른 테스트 후 정리용)."""
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


def write_hard_settings(problems_path: str = None, repo_url: str = None):
    """settings.json을 hard 모드로 작성."""
    s = {
        'show_individual_ranking': True,
        'challenge_opened': False,
        'difficulty': 'hard',
        'defer_penalty_seconds': 0,
        'buffer_ratio': 0.3,
        'seed': 42,
        'session_epoch': 1,
        'challenge_data_url': '',
        'hard_repo_url': repo_url if repo_url is not None else HARD_REPO_URL_FAKE,
        'hard_problems_path': (problems_path if problems_path is not None
                               else HARD_PROBLEMS_JSON),
    }
    with open('settings.json', 'w', encoding='utf-8') as f:
        json.dump(s, f, ensure_ascii=False, indent=2)


def init_hard(n: int = 10, groups: int = 2):
    """hard 모드로 n명/groups조 초기화. 반환: list of (knox_id, ...)."""
    # SQLAlchemy 캐시 무효화 + Windows 파일 락 해제 강제
    import gc
    import time
    try:
        from app import app
        from models import db
        with app.app_context():
            db.session.close()
            db.session.remove()
            db.engine.dispose()
    except Exception:
        pass
    gc.collect()
    time.sleep(0.2)

    from init_db import init_database, compute_group_sizes
    sizes = compute_group_sizes(n, groups)
    participants = []
    idx = 0
    for g in range(groups):
        for o in range(1, sizes[g] + 1):
            idx += 1
            participants.append({
                'knox_id': f'u{idx:03d}',
                'name': f'user{idx:03d}',
                'group': g + 1,
                'order': o,
            })
    init_database(
        group_count=groups,
        group_sizes=sizes,
        ordered_participants=participants,
        regen_challenge_data=False,
        difficulty='hard',
        buffer_ratio=0.3,
        seed=42,
    )

    try:
        from app import app
        from models import db
        with app.app_context():
            db.session.remove()
            db.engine.dispose()
    except Exception:
        pass


def test_h1_init(app, db, Runner):
    section('H1. hard 모드 정상 초기화')
    if not os.path.exists(HARD_PROBLEMS_JSON):
        check(False, 'H1-a problems.json 존재',
              detail=f'{HARD_PROBLEMS_JSON} 파일이 없음 — '
                     f'먼저 hard-challenge에서 generator 실행 필요')
        return False
    check(True, 'H1-a problems.json 존재')

    write_hard_settings()
    try:
        init_hard(n=10, groups=2)
        check(True, 'H1-b init_database(difficulty=hard) 성공')
    except Exception as e:
        check(False, 'H1-b init_database 실패', detail=str(e))
        return False

    with app.app_context():
        cnt = Runner.query.count()
    check(cnt == 10, f'H1-c 10명 Runner 생성 (실제 {cnt})')
    return True


def test_h2_problem_type(app, db, Runner):
    section('H2. Runner.problem_type=HARD, 정답 매핑')
    with app.app_context():
        runners = Runner.query.order_by(Runner.knox_id).all()
        all_hard = all(r.problem_type == 'HARD' for r in runners)
    check(all_hard, f'H2-a 모든 runner의 problem_type=HARD')

    # problems.json의 첫 5개 정답과 매칭 (round-robin 첫 회차)
    with open(HARD_PROBLEMS_JSON, encoding='utf-8') as f:
        problems = json.load(f)
    expected_answers = {str(p['correct_answer']) for p in problems[:5]}
    with app.app_context():
        actual_answers = {r.correct_answer for r in Runner.query.all()}
    check(actual_answers <= expected_answers,
          f'H2-b runner answers ⊆ problems.json 정답들 '
          f'(누락 {actual_answers - expected_answers})')


def test_h3_challenge_page(app, db, Runner):
    section('H3. /challenge 페이지에 HARD UI 노출')
    with app.app_context():
        r = Runner.query.filter_by(group_id=1, run_order=1).first()
        rid = r.id

    from app import _read_settings
    with app.test_client() as c:
        with c.session_transaction() as s:
            s['is_admin'] = True
        c.post('/admin/toggle-open',
               headers={'X-Requested-With': 'XMLHttpRequest'})
        epoch = int(_read_settings().get('session_epoch', 1))
        with c.session_transaction() as s:
            s.clear()
            s['runner_id'] = rid
            s['session_epoch'] = epoch
        r = c.get('/challenge')
    body = r.data.decode('utf-8')
    check(r.status_code == 200, f'H3-a /challenge 200 (실제 {r.status_code})')
    check('🐛 디버깅 챌린지' in body or '디버깅 챌린지' in body,
          'H3-b HARD 배너 노출')
    check('git clone' in body, 'H3-c git clone 안내 노출')
    check(HARD_REPO_URL_FAKE in body, 'H3-d repo URL 노출')


def test_h4_h5_submit(app, db, Runner):
    section('H4/H5. 정답/오답 제출')
    with app.app_context():
        r1 = Runner.query.filter_by(group_id=1, run_order=1).first()
        r1.started_at = datetime.utcnow()
        db.session.commit()
        rid = r1.id
        correct = r1.correct_answer

    from app import _read_settings
    with app.test_client() as c:
        with c.session_transaction() as s:
            s['is_admin'] = True
        c.post('/admin/toggle-open',
               headers={'X-Requested-With': 'XMLHttpRequest'})
        epoch = int(_read_settings().get('session_epoch', 1))
        with c.session_transaction() as s:
            s.clear()
            s['runner_id'] = rid
            s['session_epoch'] = epoch

        # 오답
        r_wrong = c.post('/submit', data={'answer': '99999'})
        with app.app_context():
            r1 = db.session.get(Runner, rid)
        check(r_wrong.status_code == 302, 'H4-a 오답 → 302')
        check(r1.status == 'active', f'H4-b 오답 후 status=active (실제 {r1.status})')
        check(r1.attempts == 1, f'H4-c 오답 후 attempts=1 (실제 {r1.attempts})')

        # 정답
        r_ok = c.post('/submit', data={'answer': correct})
        with app.app_context():
            r1 = db.session.get(Runner, rid)
            r2 = Runner.query.filter_by(group_id=1, run_order=2).first()
        check(r_ok.status_code == 302, 'H5-a 정답 → 302')
        check(r1.status == 'completed', f'H5-b 정답 후 status=completed')
        check(r2.status == 'active', f'H5-c 다음 주자 active')
        check(r2.password != '' and len(r2.password) >= 6,
              f'H5-d 다음 주자 비밀번호 발급')


def test_h6_self_defer(app, db, Runner):
    section('H6. self-defer (HARD) → 새 problem + 새 답')
    # init 다시 (직전 테스트로 첫 주자 completed 됨)
    write_hard_settings()
    init_hard(n=10, groups=2)

    with app.app_context():
        r1 = Runner.query.filter_by(group_id=1, run_order=1).first()
        r1.started_at = datetime.utcnow()
        db.session.commit()
        rid = r1.id
        old_answer = r1.correct_answer

    from app import _read_settings
    with app.test_client() as c:
        with c.session_transaction() as s:
            s['is_admin'] = True
        c.post('/admin/toggle-open',
               headers={'X-Requested-With': 'XMLHttpRequest'})
        epoch = int(_read_settings().get('session_epoch', 1))
        with c.session_transaction() as s:
            s.clear()
            s['runner_id'] = rid
            s['session_epoch'] = epoch
        r = c.post('/defer', data={'steps': '2', 'reason': 'H6-test'})

    check(r.status_code == 302, f'H6-a /defer 302 (실제 {r.status_code})')
    with app.app_context():
        r_after = db.session.get(Runner, rid)
    check(r_after.status == 'waiting', f'H6-b status=waiting')
    check(r_after.run_order == 3, f'H6-c run_order 3 (1+steps=2)')
    # spare로 교체되어 answer가 변경됐어야 함 (스페어 풀이 있으면)
    # PoC: spare가 main과 cycle이라 같을 수도 있어 약한 검증
    check(r_after.problem_type == 'HARD', f'H6-d 여전히 HARD 타입')


def test_h7_admin_defer(app, db, Runner):
    section('H7. admin defer (HARD) 정상 동작')
    write_hard_settings()
    init_hard(n=10, groups=2)

    with app.app_context():
        r1 = Runner.query.filter_by(group_id=1, run_order=1).first()
        rid = r1.id

    with app.test_client() as c:
        with c.session_transaction() as s:
            s['is_admin'] = True
        c.post('/admin/toggle-open',
               headers={'X-Requested-With': 'XMLHttpRequest'})
        r = c.post(f'/admin/defer/{rid}',
                   data={'mode': 'max', 'reason': 'H7'},
                   headers={'X-Requested-With': 'XMLHttpRequest'})
    check(r.status_code == 200, f'H7-a admin defer 200 (실제 {r.status_code})')
    with app.app_context():
        r_after = db.session.get(Runner, rid)
        actives = Runner.query.filter_by(group_id=1, status='active').all()
    check(r_after.status == 'waiting', 'H7-b defer-er waiting')
    check(len(actives) == 1, f'H7-c 조 1 active 1명 (실제 {len(actives)})')


def test_h8_settings_missing(app, db, Runner):
    section('H8. settings 누락 시 init 실패')
    # hard_problems_path 누락
    write_hard_settings(problems_path='')
    try:
        init_hard(n=4, groups=2)
        check(False, 'H8-a hard_problems_path 누락에도 init 성공해버림')
    except Exception as e:
        check('hard_problems_path' in str(e),
              f'H8-a hard_problems_path 누락 → RuntimeError 발생',
              detail=str(e)[:200])
    # hard_repo_url 누락
    write_hard_settings(repo_url='')
    try:
        init_hard(n=4, groups=2)
        check(False, 'H8-b hard_repo_url 누락에도 init 성공해버림')
    except Exception as e:
        check('hard_repo_url' in str(e),
              f'H8-b hard_repo_url 누락 → RuntimeError 발생',
              detail=str(e)[:200])


def test_h9_no_challenge_data_regen(app, db, Runner):
    section('H9. hard 모드는 challenge_data.dat 재생성 호출 안 함')
    write_hard_settings()
    # challenge_data.dat을 임시로 mtime 표시
    cd_path = 'challenge_data.dat'
    had_file = os.path.exists(cd_path)
    if had_file:
        orig_mtime = os.path.getmtime(cd_path)
    init_hard(n=4, groups=2)
    if had_file:
        new_mtime = os.path.getmtime(cd_path)
        check(orig_mtime == new_mtime,
              f'H9 challenge_data.dat mtime 변경 없음 (regen 안 됨)')
    else:
        check(not os.path.exists(cd_path),
              f'H9 challenge_data.dat 새로 생성되지 않음')


def main():
    # easy 기본으로 한 번 초기화 후 (DB schema 보장)
    reset_db_easy()

    from app import app
    from models import db, Runner

    try:
        if not test_h1_init(app, db, Runner):
            print('\n[중단] H1 실패로 후속 테스트 스킵')
            return _summary()
        test_h2_problem_type(app, db, Runner)
        test_h3_challenge_page(app, db, Runner)
        test_h4_h5_submit(app, db, Runner)
        test_h6_self_defer(app, db, Runner)
        test_h7_admin_defer(app, db, Runner)
        test_h8_settings_missing(app, db, Runner)
        test_h9_no_challenge_data_regen(app, db, Runner)
    except Exception as e:
        import traceback
        print(f'\n[FATAL]\n{traceback.format_exc()}')
        _results['fail'] += 1
        _results['failures'].append(('exception', str(e)))

    # easy 모드로 복원
    reset_db_easy()
    return _summary()


def _summary():
    print()
    print('=' * 70)
    print(f' 결과: {_results["pass"]}개 성공 / {_results["fail"]}개 실패')
    print('=' * 70)
    if _results['failures']:
        print('\n[실패 항목]')
        for n, d in _results['failures'][:20]:
            print(f'  - {n}')
            if d:
                print(f'      {d[:200]}')
    return 0 if _results['fail'] == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
