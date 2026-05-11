#!/usr/bin/env python3
"""End-to-end integration test for relay-challenge.
Run: python e2e_test.py

Covers backend logic (HTTP routes, DB state, problem integrity).
UI 요소(마법사 클릭·모달 작동 등)는 별도 수동 점검 필요.
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Windows 콘솔에서 UTF-8 출력 가능하도록
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# ============================================================
# 테스트 결과 추적
# ============================================================
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
    print('=' * 60)
    print(f' {title}')
    print('=' * 60)


def reset_client_state(c):
    """세션·쿠키(device_completed_by 등) 초기화."""
    with c.session_transaction() as sess:
        sess.clear()
    # 쿠키 jar 초기화 (test_client 내부 cookie storage)
    try:
        c.cookie_jar.clear()
    except AttributeError:
        # Werkzeug 새 버전 호환
        try:
            c._cookies.clear()
        except AttributeError:
            pass


def _current_session_epoch():
    """settings.json 의 현재 epoch 값."""
    from app import _read_settings
    return int(_read_settings().get('session_epoch', 1))


def login_as_runner(c, sess, runner_id):
    """테스트에서 수동 로그인 시뮬 — runner_id + session_epoch 함께 설정."""
    sess['runner_id'] = runner_id
    sess['session_epoch'] = _current_session_epoch()


# ============================================================
# 챌린지 데이터 파서 (정답 무결성 검증용)
# ============================================================
def parse_challenge_data(path):
    """challenge_data.dat 파싱. (json_records, log_entries, reg_entries) 반환."""
    json_records, log_entries, reg_entries = [], [], []
    section_start = re.compile(r'^=====\s+(LOG_SECTION|JSON_BLOCK|REGISTRY)\s+([A-Z]+-\d+)\s+=====')
    section_end = re.compile(r'^=====\s+END\s+[A-Z]+-\d+\s+=====')
    log_re = re.compile(
        r'^\[(?P<ts>[^\]]+)\]\s+\[(?P<level>[A-Z]+)\]\s+\[(?P<module>[^\]]+)\]\s+'
        r'id=(?P<emp>EMP\d+)\s+duration=(?P<dur>\d+)ms\s+msg="[^"]*"\s+tag=(?P<tag>\S+)'
    )
    reg_re = re.compile(
        r'^(?P<id>REG-\d+-\d+)\s+\|\s+tag=(?P<tag>\S+)\s+\|\s+priority=(?P<pri>\S+)\s+\|\s+'
        r'ref=(?P<ref>EMP\d+)\s+\|\s+metric=(?P<metric>[\d.]+)'
    )
    with open(path, 'r', encoding='utf-8') as f:
        current = None
        json_buf, json_depth = [], 0
        for raw in f:
            line = raw.rstrip('\n')
            m = section_start.match(line)
            if m:
                current = (m.group(1), m.group(2))
                json_buf, json_depth = [], 0
                continue
            if section_end.match(line):
                current = None
                continue
            if current is None:
                continue
            sec_type, _ = current
            if sec_type == 'LOG_SECTION':
                lm = log_re.match(line)
                if lm:
                    log_entries.append({
                        'level': lm.group('level'),
                        'module': lm.group('module'),
                        'employee_id': lm.group('emp'),
                        'duration': int(lm.group('dur')),
                        'tag': lm.group('tag'),
                    })
            elif sec_type == 'REGISTRY':
                rm = reg_re.match(line)
                if rm:
                    reg_entries.append({
                        'entry_id': rm.group('id'),
                        'tag': rm.group('tag'),
                        'priority': rm.group('pri'),
                        'ref': rm.group('ref'),
                        'metric': float(rm.group('metric')),
                    })
            elif sec_type == 'JSON_BLOCK':
                s = line.strip()
                if not s:
                    continue
                if json_depth == 0:
                    if not s.startswith('{'):
                        continue
                    json_buf = [s]
                    json_depth = s.count('{') - s.count('}')
                    if json_depth == 0:
                        try:
                            json_records.append(json.loads(''.join(json_buf)))
                        except json.JSONDecodeError:
                            pass
                        json_buf = []
                else:
                    json_buf.append(s)
                    json_depth += s.count('{') - s.count('}')
                    if json_depth == 0:
                        try:
                            json_records.append(json.loads(''.join(json_buf)))
                        except json.JSONDecodeError:
                            pass
                        json_buf = []
    return json_records, log_entries, reg_entries


def compute_answer_for_problem(runner, json_records, log_entries, reg_entries):
    """주자의 problem_text·problem_type 으로부터 정답 재계산.
    F/G/H/I/J 의 step1/step2 힌트로부터 파라미터 추출 후 재계산.
    재계산 가능한 경우 답 문자열 반환, 아니면 None.
    """
    text = runner.problem_text
    pt = runner.problem_type

    if pt == 'F':
        m = re.search(r'department="([^"]+)", status="([^"]+)"', text)
        m2 = re.search(r'로그 레벨이 (\w+) 인', text)
        if not m or not m2:
            return None
        dept, status = m.group(1), m.group(2)
        level = m2.group(1)
        emp_ids = {r['employee_id'] for r in json_records
                   if r.get('department') == dept and r.get('status') == status}
        logs = [e for e in log_entries if e['employee_id'] in emp_ids and e['level'] == level]
        return f"{sum(e['duration'] for e in logs)}"
    if pt == 'G':
        m = re.search(r'role="([^"]+)", status="([^"]+)"', text)
        m2 = re.search(r'priority="([^"]+)"', text)
        if not m or not m2:
            return None
        role, status = m.group(1), m.group(2)
        priority = m2.group(1)
        emp_ids = {r['employee_id'] for r in json_records
                   if r.get('role') == role and r.get('status') == status}
        regs = [e for e in reg_entries if e['ref'] in emp_ids and e['priority'] == priority]
        return f"{round(sum(e['metric'] for e in regs), 1):.1f}"
    if pt == 'H':
        m = re.search(r'tag="([^"]+)", priority="([^"]+)"', text)
        m2 = re.search(r'department="([^"]+)"', text)
        if not m or not m2:
            return None
        tag, priority = m.group(1), m.group(2)
        dept = m2.group(1)
        refs = {e['ref'] for e in reg_entries if e['tag'] == tag and e['priority'] == priority}
        records = [r for r in json_records if r.get('employee_id') in refs and r.get('department') == dept]
        return f"{round(sum(r['score'] for r in records), 2):.2f}"
    if pt == 'I':
        m = re.search(r'module="([^"]+)", level=(\w+)', text)
        m2 = re.search(r'status="([^"]+)"', text)
        if not m or not m2:
            return None
        module, level = m.group(1), m.group(2)
        status = m2.group(1)
        emp_ids = {e['employee_id'] for e in log_entries
                   if e['module'] == module and e['level'] == level}
        records = [r for r in json_records if r.get('employee_id') in emp_ids and r.get('status') == status]
        return f"{len(records)}"
    if pt == 'J':
        m = re.search(r'module="([^"]+)", level=(\w+)', text)
        m2 = re.search(r'tag="([^"]+)"', text)
        if not m or not m2:
            return None
        module, level = m.group(1), m.group(2)
        tag = m2.group(1)
        emp_ids = {e['employee_id'] for e in log_entries
                   if e['module'] == module and e['level'] == level}
        entries = [e for e in reg_entries if e['ref'] in emp_ids and e['tag'] == tag]
        return f"{len(entries)}"
    return None  # Easy types (A/B/C/D/E)는 추후


# ============================================================
# 메인 테스트
# ============================================================
def main():
    if os.path.exists('settings.json'):
        os.remove('settings.json')

    from app import app, _read_settings
    from models import db, Runner, Group, SpareProblem, AttemptLog

    # ----------------------------------------------------------
    section('SECTION 1: 초기화 (마법사 commit) — 10명 2조, medium, seed=2026')
    # ----------------------------------------------------------
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess['is_admin'] = True

        csv = '\n'.join([f'u{i:03d},참가자{i}' for i in range(1, 11)])
        r = c.post('/admin/init/parse',
                   json={'csv_text': csv, 'total_n': 10, 'group_count': 2})
        d = r.get_json()
        check(r.status_code == 200 and d.get('ok'), 'parse 200 OK')
        check(d.get('group_sizes') == [5, 5], 'group_sizes [5,5]')
        check(len(d.get('participants', [])) == 10, '참가자 10명 파싱')

        ordered = []
        idx = 0
        for g, sz in enumerate(d['group_sizes'], 1):
            for o in range(1, sz + 1):
                p = d['participants'][idx]; idx += 1
                ordered.append({'knox_id': p['knox_id'], 'name': p['name'], 'group': g, 'order': o})

        body = {
            'group_count': 2, 'group_sizes': d['group_sizes'],
            'ordered_participants': ordered, 'regen_challenge_data': True,
            'force': True, 'show_individual_ranking': True,
            'difficulty': 'medium', 'defer_penalty_seconds': 60,
            'buffer_ratio': 0.4, 'seed': 2026, 'confirm': 'INIT',
        }
        r = c.post('/admin/init/commit', json=body)
        cj = r.get_json()
        check(r.status_code == 200 and cj.get('ok'), 'commit 200 OK')
        check(cj['summary']['runners'] == 10, '주자 10명 생성')
        check(cj['summary']['spare_count'] == 4, '스페어 풀 4개 (10 * 0.4)')
        check(len(cj['first_player_lines']) == 2, 'firstPlayer 2줄')

        # settings.json 검증
        settings = _read_settings()
        check(settings.get('difficulty') == 'medium', 'settings.difficulty=medium')
        check(settings.get('seed') == 2026, 'settings.seed=2026')
        check(settings.get('challenge_opened') is False, 'challenge_opened=False (init 직후)')

        # ----------------------------------------------------------
        section('SECTION 2: 공개 토글 + 접근 제어')
        # ----------------------------------------------------------
        # 비공개 상태에서 / 접근
        with c.session_transaction() as sess:
            sess.clear()
        r = c.get('/')
        check(r.status_code == 200 and '준비 중' in r.data.decode('utf-8'), '비공개시 / → 준비 중 페이지')
        r = c.get('/leaderboard')
        check('준비 중' in r.data.decode('utf-8'), '비공개시 /leaderboard → 준비 중')
        r = c.get('/roster')
        check('준비 중' in r.data.decode('utf-8'), '비공개시 /roster → 준비 중')

        # 관리자 공개 토글
        with c.session_transaction() as sess:
            sess['is_admin'] = True
        r = c.post('/admin/toggle-open',
                   headers={'X-Requested-With': 'XMLHttpRequest'})
        rj = r.get_json()
        check(r.status_code == 200 and rj.get('ok') and rj.get('opened'), '공개 토글 성공')

        # 공개 후 / 접근 가능
        with c.session_transaction() as sess:
            sess.clear()
        r = c.get('/')
        check('Knox-ID' in r.data.decode('utf-8') and '준비 중' not in r.data.decode('utf-8'),
              '공개 후 / → 로그인 폼')

        # ----------------------------------------------------------
        section('SECTION 3: 문제 무결성 (DB 정답 == .dat 재계산)')
        # ----------------------------------------------------------
        json_records, log_entries, reg_entries = parse_challenge_data('challenge_data.dat')
        check(len(json_records) > 0, 'JSON 레코드 파싱')
        check(len(log_entries) > 0, 'LOG 항목 파싱')
        check(len(reg_entries) > 0, 'REGISTRY 항목 파싱')

        with app.app_context():
            runners = Runner.query.all()
            mismatches = []
            verified = 0
            for r_ in runners:
                computed = compute_answer_for_problem(r_, json_records, log_entries, reg_entries)
                if computed is None:
                    continue
                verified += 1
                if computed != r_.correct_answer:
                    mismatches.append((r_.knox_id, r_.problem_type, computed, r_.correct_answer))
            check(verified == 10, f'유형 F/G/H/I/J 10개 모두 재계산 가능 (실제: {verified})')
            check(len(mismatches) == 0, '모든 주자 정답 일치',
                  detail=f'mismatches: {mismatches[:3]}')

        # ----------------------------------------------------------
        section('SECTION 4: 로그인 + 정답 제출 + 다음 주자 활성화')
        # ----------------------------------------------------------
        with app.app_context():
            r1 = Runner.query.filter_by(group_id=1, run_order=1).first()
            r1_id = r1.id
            r1_pw = r1.password
            r1_knox = r1.knox_id
            r1_ans = r1.correct_answer

        # 1번 주자 로그인
        r = c.post('/login', data={'knox_id': r1_knox, 'password': r1_pw}, follow_redirects=False)
        check(r.status_code == 302, '로그인 → 302 redirect')
        with app.app_context():
            r1_after = db.session.get(Runner, r1_id)
            check(r1_after.started_at is not None, '로그인 후 started_at 기록됨')

        # 오답 제출
        r = c.post('/submit', data={'answer': '999999999'}, follow_redirects=False)
        check(r.status_code == 302, '오답 제출 → 302 redirect (challenge로)')
        with app.app_context():
            r1_after = db.session.get(Runner, r1_id)
            check(r1_after.attempts == 1, '오답 후 attempts=1')
            check(r1_after.status == 'active', '오답 후 status=active 유지')

        # 정답 제출
        r = c.post('/submit', data={'answer': r1_ans}, follow_redirects=False)
        check(r.status_code == 302, '정답 제출 → 302 redirect')
        with app.app_context():
            r1_after = db.session.get(Runner, r1_id)
            check(r1_after.status == 'completed', '정답 후 status=completed')
            check(r1_after.completed_at is not None, 'completed_at 기록')
            r2 = Runner.query.filter_by(group_id=1, run_order=2).first()
            check(r2.status == 'active', '2번 주자 자동 활성화')
            check(r2.password and len(r2.password) > 0, '2번 주자 비번 발급')
            r2_pw = r2.password
            r2_id = r2.id

        # ----------------------------------------------------------
        section('SECTION 5: 미루기 N칸 + 스페어 문제 교체')
        # ----------------------------------------------------------
        # 2번 주자로 로그인 (이전 device 쿠키 초기화 필요)
        reset_client_state(c)
        with app.app_context():
            r2 = db.session.get(Runner, r2_id)
            r2_knox = r2.knox_id
            r2_orig_answer = r2.correct_answer
            r2_orig_text = r2.problem_text[:30]
        r = c.post('/login', data={'knox_id': r2_knox, 'password': r2_pw}, follow_redirects=False)
        check(r.status_code == 302 and '/challenge' in r.headers.get('Location', ''),
              '2번 주자 로그인 → /challenge')

        # 2칸 뒤로 이동
        r = c.post('/defer', data={'steps': '2', 'reason': 'e2e'}, follow_redirects=False)
        check(r.status_code == 302 and '/defer/result' in r.headers.get('Location', ''),
              '/defer steps=2 → /defer/result로')

        with app.app_context():
            r2_after = db.session.get(Runner, r2_id)
            check(r2_after.run_order == 4, '2번 주자 → 4번 자리로 이동')
            check(r2_after.deferred_count == 1, 'deferred_count=1')
            check(r2_after.correct_answer != r2_orig_answer, '문제 새 문제로 교체됨 (correct_answer 변경)')
            check(r2_after.next_runner_password, '본인 next_runner_password에 저장')
            new_active = Runner.query.filter_by(group_id=1, run_order=2).first()
            check(new_active.status == 'active', '새 2번 주자 active')
            new2_id = new_active.id
            new2_knox = new_active.knox_id
            new2_pw = new_active.password

            # 사이 주자(원래 3, 4번)는 시프트
            shifted = Runner.query.filter_by(group_id=1, run_order=3).first()
            check(shifted.status == 'waiting', '시프트된 주자 status=waiting 유지')

        # /defer/result 페이지 확인
        r = c.get('/defer/result')
        body = r.data.decode('utf-8')
        check(r.status_code == 200, '/defer/result 200')
        check(new2_pw in body, '새 활성 주자 비번이 페이지에 표시됨')

        # ----------------------------------------------------------
        section('SECTION 6: 미루기 1:1 swap')
        # ----------------------------------------------------------
        # 새 2번 주자로 로그인 (원래 3번 주자)
        reset_client_state(c)
        r = c.post('/login', data={'knox_id': new2_knox, 'password': new2_pw}, follow_redirects=False)
        check(r.status_code == 302 and '/challenge' in r.headers.get('Location', ''),
              '새 2번 주자 로그인 → /challenge')

        # 4번과 swap (4번에 원래 2번 주자가 있음)
        r = c.post('/defer/swap', data={'target_order': '4', 'reason': 'swap_test'}, follow_redirects=False)
        check(r.status_code == 302, 'swap → 302')

        with app.app_context():
            this_runner = db.session.get(Runner, new2_id)
            check(this_runner.run_order == 4, 'swap 후 본인 run_order=4')
            check(this_runner.deferred_count == 1, 'swap도 deferred_count=1')
            # 사이 주자(3번)는 영향 없음
            r3 = Runner.query.filter_by(group_id=1, run_order=3).first()
            check(r3.status == 'waiting', '3번 자리 주자 영향 없음')
            new_active2 = Runner.query.filter_by(group_id=1, run_order=2).first()
            check(new_active2.status == 'active', '2번 자리 새 활성화 (원래 4번이 swap으로 옴)')

        # ----------------------------------------------------------
        section('SECTION 7: 관리자 액션 (Pass / Skip / Reset)')
        # ----------------------------------------------------------
        with c.session_transaction() as sess:
            sess.clear()
            sess['is_admin'] = True
        with app.app_context():
            current_active = Runner.query.filter_by(group_id=1, status='active').first()
            ca_id = current_active.id

        # Pass
        r = c.post(f'/admin/pass/{ca_id}', data={'reason': 'pass_test'},
                   headers={'X-Requested-With': 'XMLHttpRequest'})
        check(r.status_code == 200, 'admin pass 200')
        with app.app_context():
            r_ = db.session.get(Runner, ca_id)
            check(r_.status == 'passed', 'status=passed')
            check(r_.completed_at is not None, 'completed_at 기록 (Pass도 시간 기록)')

        # Reset
        r = c.post(f'/admin/reset/{ca_id}', headers={'X-Requested-With': 'XMLHttpRequest'})
        check(r.status_code == 200, 'admin reset 200')
        with app.app_context():
            r_ = db.session.get(Runner, ca_id)
            check(r_.status == 'active', 'reset 후 status=active')
            check(r_.completed_at is None, 'reset 후 completed_at=None')

        # Skip 다른 주자
        with app.app_context():
            target = Runner.query.filter_by(group_id=2, run_order=2).first()
            target_id = target.id
        r = c.post(f'/admin/skip/{target_id}', data={'reason': 'skip_test'},
                   headers={'X-Requested-With': 'XMLHttpRequest'})
        check(r.status_code == 200, 'admin skip 200')
        with app.app_context():
            r_ = db.session.get(Runner, target_id)
            check(r_.status == 'skipped', 'status=skipped')
            check(r_.completed_at is None, 'skip은 completed_at=None (시간 제외)')

        # ----------------------------------------------------------
        section('SECTION 8: 관리자 미루기 모달 3가지 모드')
        # ----------------------------------------------------------
        with app.app_context():
            current_active = Runner.query.filter_by(group_id=1, status='active').first()
            ca_id = current_active.id
            ca_order = current_active.run_order
            max_order = db.session.query(db.func.max(Runner.run_order))\
                .filter_by(group_id=1).scalar()

        # mode=max
        r = c.post(f'/admin/defer/{ca_id}', data={'mode': 'max', 'reason': 'mode_max'},
                   headers={'X-Requested-With': 'XMLHttpRequest'})
        rj = r.get_json()
        check(r.status_code == 200 and rj.get('ok'), 'admin defer mode=max 성공')
        with app.app_context():
            r_ = db.session.get(Runner, ca_id)
            check(r_.run_order == max_order, f'mode=max → run_order={max_order}')

        # ----------------------------------------------------------
        section('SECTION 9: 후기 작성 / 조회')
        # ----------------------------------------------------------
        with c.session_transaction() as sess:
            sess.clear()
        with app.app_context():
            completed = Runner.query.filter_by(status='completed').first()
            cmp_id = completed.id
        with c.session_transaction() as sess:
            login_as_runner(c, sess, cmp_id)

        r = c.post('/review', data={'review': '재미있었어요. e2e 테스트입니다.'}, follow_redirects=False)
        check(r.status_code == 302, '후기 POST 302')
        with app.app_context():
            r_ = db.session.get(Runner, cmp_id)
            check(r_.review and '재미있었어요' in r_.review, '후기 저장됨')

        # 두 번째 시도 거부
        r = c.post('/review', data={'review': '두번째'}, follow_redirects=False)
        with app.app_context():
            r_ = db.session.get(Runner, cmp_id)
            check('재미있었어요' in r_.review and '두번째' not in r_.review, '두 번째 후기 거부됨')

        # ----------------------------------------------------------
        section('SECTION 10: last_seen_at 갱신 (before_request)')
        # ----------------------------------------------------------
        with c.session_transaction() as sess:
            sess.clear()
        with app.app_context():
            active = Runner.query.filter_by(status='active').first()
            ac_id = active.id
            active.last_seen_at = datetime.utcnow() - timedelta(hours=1)
            db.session.commit()
            before = active.last_seen_at
        with c.session_transaction() as sess:
            login_as_runner(c, sess, ac_id)
        c.get('/')
        with app.app_context():
            r_ = db.session.get(Runner, ac_id)
            check(r_.last_seen_at != before, 'last_seen_at 갱신됨')
            check((datetime.utcnow() - r_.last_seen_at).total_seconds() < 5,
                  'last_seen_at 최근 (5초 이내)')

        # ----------------------------------------------------------
        section('SECTION 11: 시드 결정성 (같은 시드 = 같은 답)')
        # ----------------------------------------------------------
        from generate_challenge import get_all_problems
        p1 = get_all_problems(20, difficulty='medium', seed=2026)
        p2 = get_all_problems(20, difficulty='medium', seed=2026)
        check(all(a.answer == b.answer for a, b in zip(p1, p2)),
              '같은 seed=2026 → 답 동일')
        p3 = get_all_problems(20, difficulty='medium', seed=9999)
        diff_count = sum(1 for a, b in zip(p1, p3) if a.answer != b.answer)
        check(diff_count == 20, f'다른 seed → 모든 답 다름 (실제 다른 답 {diff_count}/20)')

        # ----------------------------------------------------------
        section('SECTION 12: 난이도 — Easy 모드 (Type A/B/C/D/E)')
        # ----------------------------------------------------------
        easy_p = get_all_problems(15, difficulty='easy', seed=2026)
        types = {p.ptype for p in easy_p}
        check(types == {'A', 'B', 'C', 'D', 'E'}, f'Easy 모드 5유형 (실제: {types})')

        # ----------------------------------------------------------
        section('SECTION 13: /roster 페이지')
        # ----------------------------------------------------------
        with c.session_transaction() as sess:
            sess.clear()
        r = c.get('/roster')
        body = r.data.decode('utf-8')
        check(r.status_code == 200, '/roster 200')
        check('조 1' in body and '조 2' in body, '모든 조 표시')
        # 비밀번호·정답 노출 안 됨
        with app.app_context():
            sample_pw = Runner.query.filter(Runner.password != '').first()
            if sample_pw:
                check(sample_pw.password not in body,
                      '평문 비번 미노출 (sample 비번이 본문에 없음)')

        # ----------------------------------------------------------
        section('SECTION 14: 개인 랭킹 보정 elapsed (미루기 패널티)')
        # ----------------------------------------------------------
        from app import get_individual_rankings
        with app.app_context():
            # 임의 2명을 completed로 + 미루기 횟수 다르게 강제 세팅
            rs = Runner.query.limit(2).all()
            rs[0].status = 'completed'
            rs[0].started_at = datetime(2026, 5, 10, 10, 0, 0)
            rs[0].completed_at = datetime(2026, 5, 10, 10, 5, 0)  # 5분
            rs[0].deferred_count = 0
            rs[1].status = 'completed'
            rs[1].started_at = datetime(2026, 5, 10, 10, 10, 0)
            rs[1].completed_at = datetime(2026, 5, 10, 10, 13, 0)  # 3분 raw
            rs[1].deferred_count = 3  # +3분 패널티 → 보정 6분
            db.session.commit()
            items = get_individual_rankings(limit=10, defer_penalty_seconds=60)
            check(len(items) >= 2, f'개인 랭킹 ≥2명 (실제 {len(items)})')
            if len(items) >= 2:
                check(items[0]['runner'].id == rs[0].id,
                      '미루기 0회의 5분이 미루기 3회의 6분 보정값보다 앞섬')
                # 보정 elapsed 값 자체 확인
                rs1_item = next((i for i in items if i['runner'].id == rs[1].id), None)
                if rs1_item:
                    check(rs1_item['elapsed'].total_seconds() == 360,
                          f'미루기 3회 보정 elapsed=360초 (3분 raw + 3분 penalty), 실제: {rs1_item["elapsed"].total_seconds()}초')

    # ============================================================
    print()
    print('=' * 60)
    print(f' 결과: {_results["pass"]}개 성공 / {_results["fail"]}개 실패')
    print('=' * 60)
    if _results['failures']:
        print('\n[실패 항목]')
        for name, detail in _results['failures']:
            print(f'  - {name}')
            if detail:
                print(f'      {detail}')
    return 0 if _results['fail'] == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
