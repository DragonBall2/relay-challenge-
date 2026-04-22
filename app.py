#!/usr/bin/env python3
"""
코딩 에이전트 릴레이 설치 챌린지 - Flask 웹 애플리케이션
"""

import os
import secrets
import string
from datetime import datetime, timedelta
from functools import wraps

from flask import (Flask, render_template, request, redirect, url_for,
                   session, flash, send_file, jsonify, abort, make_response)
from models import db, Group, Runner, AttemptLog
import config


def create_app():
    app = Flask(__name__)
    app.config.from_object(config)
    db.init_app(app)
    return app


app = create_app()


# ============================================================
# 유틸리티
# ============================================================
def generate_password(length=8):
    alphabet = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def check_answer(submitted, correct, problem_type):
    """답안 비교. 타입별로 비교 방식이 다릅니다.
    1차: A/B/D=int, C=float 2dec, E=문자열
    2차: F/I/J=int, G=float 1dec, H=float 2dec
    """
    submitted = submitted.strip()
    correct = correct.strip()
    if problem_type in ('A', 'B', 'D', 'F', 'I', 'J'):
        try:
            return str(int(float(submitted))) == correct
        except (ValueError, OverflowError):
            return False
    elif problem_type == 'G':
        try:
            return f"{float(submitted):.1f}" == f"{float(correct):.1f}"
        except (ValueError, OverflowError):
            return False
    elif problem_type in ('C', 'H'):
        try:
            return f"{float(submitted):.2f}" == f"{float(correct):.2f}"
        except (ValueError, OverflowError):
            return False
    else:  # Type E
        return submitted == correct


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'runner_id' not in session:
            flash('로그인이 필요합니다.', 'warning')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('is_admin'):
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated


def get_group_rankings():
    """조별 순위 계산. 완주한 조 → 완주 시간순, 미완주 조 → 진행률순."""
    groups = Group.query.order_by(Group.id).all()
    rankings = []

    for group in groups:
        runners = Runner.query.filter_by(group_id=group.id).order_by(Runner.run_order).all()
        total = len(runners)
        completed = sum(1 for r in runners if r.status in ('completed', 'passed', 'skipped'))
        # tie-breaker: skipped 제외(완주/PASS만)
        real_completed = sum(1 for r in runners if r.status in ('completed', 'passed'))
        current_runner = next((r for r in runners if r.status == 'active'), None)

        # 조 시작 시간: 가장 이른 started_at (skipped 주자 제외)
        started_times = [r.started_at for r in runners
                         if r.started_at and r.status != 'skipped']
        start_time = min(started_times) if started_times else None

        # 조 완료 시간
        finish_time = group.finished_at

        # 경과 시간
        if finish_time and start_time:
            elapsed = finish_time - start_time
        elif start_time:
            elapsed = datetime.utcnow() - start_time
        else:
            elapsed = None

        rankings.append({
            'group': group,
            'total': total,
            'completed': completed,
            'real_completed': real_completed,
            'progress': round(completed / total * 100) if total else 0,
            'current_runner': current_runner,
            'start_time': start_time,
            'finish_time': finish_time,
            'elapsed': elapsed,
            'is_finished': finish_time is not None,
        })

    # 정렬: 완주 조가 먼저 (완주시간 빠른순), 미완주 조는 정규 완료수 높은순(skip 제외)
    finished = sorted([r for r in rankings if r['is_finished']],
                      key=lambda x: x['finish_time'])
    unfinished = sorted([r for r in rankings if not r['is_finished']],
                        key=lambda x: (-x['real_completed'], x['group'].id))

    all_ranked = finished + unfinished
    for i, r in enumerate(all_ranked):
        r['rank'] = i + 1
    return all_ranked


def format_timedelta(td):
    """timedelta를 읽기 좋은 문자열로 변환."""
    if td is None:
        return '-'
    total_seconds = int(td.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    if hours > 0:
        return f"{hours}시간 {minutes}분 {seconds}초"
    elif minutes > 0:
        return f"{minutes}분 {seconds}초"
    else:
        return f"{seconds}초"


app.jinja_env.globals['format_timedelta'] = format_timedelta


def to_kst(dt):
    """UTC datetime을 KST(+9)로 변환."""
    if dt is None:
        return None
    return dt + timedelta(hours=9)

app.jinja_env.globals['to_kst'] = to_kst


def _is_ajax():
    return (request.headers.get('X-Requested-With') == 'XMLHttpRequest'
            or request.accept_mimetypes.best == 'application/json')


# ============================================================
# 공개 라우트
# ============================================================
@app.route('/')
def index():
    rankings = get_group_rankings()
    total_completed = sum(r['completed'] for r in rankings)
    total_runners = sum(r['total'] for r in rankings)
    return render_template('index.html',
                           rankings=rankings,
                           total_completed=total_completed,
                           total_runners=total_runners)


@app.route('/login', methods=['POST'])
def login():
    knox_id = request.form.get('knox_id', '').strip()
    password = request.form.get('password', '').strip()

    if not knox_id or not password:
        flash('Knox-ID와 비밀번호를 모두 입력하세요.', 'danger')
        return redirect(url_for('index'))

    runner = Runner.query.filter(
        db.func.lower(Runner.knox_id) == knox_id.lower()
    ).first()

    if not runner:
        flash('Knox-ID를 찾을 수 없습니다.', 'danger')
        return redirect(url_for('index'))

    if runner.status == 'waiting':
        flash('아직 차례가 아닙니다. 이전 주자가 완료해야 접속할 수 있습니다.', 'warning')
        return redirect(url_for('index'))

    if runner.status in ('completed', 'passed'):
        if runner.password != password:
            flash('비밀번호가 일치하지 않습니다.', 'danger')
            return redirect(url_for('index'))
        session['runner_id'] = runner.id
        return redirect(url_for('success'))

    if runner.status == 'skipped':
        flash('관리자에 의해 건너뛰기 처리된 참가자입니다.', 'info')
        return redirect(url_for('index'))

    if runner.password != password:
        flash('비밀번호가 일치하지 않습니다.', 'danger')
        return redirect(url_for('index'))

    # 디바이스 쿠키 체크: 이 브라우저에서 다른 사람이 이미 풀었는지 확인
    device_owner = request.cookies.get('device_completed_by')
    if device_owner and device_owner != knox_id.lower():
        flash('이 PC(브라우저)에서 이미 다른 참가자가 문제를 풀었습니다. '
              '본인의 PC에서 접속해 주세요.', 'danger')
        return redirect(url_for('index'))

    # 로그인 성공
    session['runner_id'] = runner.id
    session.permanent = True
    if not runner.started_at:
        runner.started_at = datetime.utcnow()
        db.session.commit()

    return redirect(url_for('challenge'))


@app.route('/challenge')
@login_required
def challenge():
    runner = db.session.get(Runner, session['runner_id'])
    if not runner:
        session.pop('runner_id', None)
        flash('세션이 만료되었습니다.', 'warning')
        return redirect(url_for('index'))

    if runner.status in ('completed', 'passed'):
        return redirect(url_for('success'))

    return render_template('challenge.html', runner=runner)


@app.route('/submit', methods=['POST'])
@login_required
def submit():
    runner = db.session.get(Runner,session['runner_id'])
    if not runner or runner.status in ('completed', 'passed'):
        return redirect(url_for('index'))

    submitted = request.form.get('answer', '').strip()
    if not submitted:
        flash('답을 입력하세요.', 'warning')
        return redirect(url_for('challenge'))

    runner.attempts += 1
    is_correct = check_answer(submitted, runner.correct_answer, runner.problem_type)

    # 시도 로그 기록
    log = AttemptLog(
        runner_id=runner.id,
        submitted_answer=submitted,
        is_correct=is_correct,
        submitted_at=datetime.utcnow()
    )
    db.session.add(log)

    if is_correct:
        runner.status = 'completed'
        runner.completed_at = datetime.utcnow()
        runner.submitted_answer = submitted
        _activate_next_runner(runner)
        db.session.commit()
        # 디바이스 쿠키 설정: 이 브라우저를 이 사용자에게 귀속
        resp = make_response(redirect(url_for('success')))
        resp.set_cookie('device_completed_by', runner.knox_id.lower(),
                        max_age=60*60*24, httponly=True, samesite='Lax')
        return resp
    else:
        runner.submitted_answer = submitted
        db.session.commit()
        flash(f'오답입니다. 다시 시도하세요. (시도 횟수: {runner.attempts}회)', 'danger')
        return redirect(url_for('challenge'))


@app.route('/defer', methods=['POST'])
@login_required
def defer():
    """주자 본인이 자기 차례를 조 맨 뒤로 미룸."""
    runner = db.session.get(Runner, session['runner_id'])
    if not runner or runner.status != 'active':
        flash('현재 진행 중인 주자만 미룰 수 있습니다.', 'warning')
        return redirect(url_for('index'))

    old_order = runner.run_order
    max_order = db.session.query(db.func.max(Runner.run_order))\
        .filter_by(group_id=runner.group_id).scalar()

    if old_order == max_order:
        flash('이미 마지막 주자라 더 미룰 수 없습니다.', 'info')
        return redirect(url_for('challenge'))

    # 뒤에 있는 주자들을 1씩 당김
    later_runners = Runner.query.filter(
        Runner.group_id == runner.group_id,
        Runner.run_order > old_order
    ).order_by(Runner.run_order).all()
    for r in later_runners:
        r.run_order -= 1

    reason = request.form.get('reason', '').strip()[:200]
    runner.run_order = max_order
    runner.status = 'waiting'
    runner.password = ''
    runner.started_at = None
    runner.attempts = 0
    runner.reason = reason or None

    # 당겨진 순서에 있는 waiting 주자 활성화
    next_runner = Runner.query.filter_by(
        group_id=runner.group_id,
        run_order=old_order
    ).first()
    if next_runner and next_runner.status == 'waiting':
        password = generate_password()
        next_runner.password = password
        next_runner.status = 'active'

    db.session.commit()
    session.pop('runner_id', None)
    flash('차례를 맨 뒤로 미뤘습니다. 다음 주자가 활성화됩니다.', 'info')
    return redirect(url_for('index'))


@app.route('/success')
@login_required
def success():
    runner = db.session.get(Runner,session['runner_id'])
    if not runner or runner.status not in ('completed', 'passed'):
        return redirect(url_for('index'))

    # 다음 주자 정보
    next_runner = Runner.query.filter_by(
        group_id=runner.group_id,
        run_order=runner.run_order + 1
    ).first()

    # 소요 시간
    elapsed = None
    if runner.started_at and runner.completed_at:
        elapsed = runner.completed_at - runner.started_at

    # 조 완주 여부
    group = db.session.get(Group,runner.group_id)
    is_group_finished = group.finished_at is not None

    return render_template('success.html',
                           runner=runner,
                           next_runner=next_runner,
                           elapsed=elapsed,
                           is_group_finished=is_group_finished)


def get_individual_rankings(limit=20):
    """개인 경과시간 랭킹 — status='completed' 주자만, 경과시간 오름차순."""
    runners = Runner.query.filter_by(status='completed')\
        .filter(Runner.started_at.isnot(None))\
        .filter(Runner.completed_at.isnot(None)).all()
    items = []
    for r in runners:
        elapsed = r.completed_at - r.started_at
        items.append({
            'runner': r,
            'elapsed': elapsed,
            'elapsed_seconds': int(elapsed.total_seconds()),
        })
    items.sort(key=lambda x: x['elapsed_seconds'])
    for i, it in enumerate(items[:limit]):
        it['rank'] = i + 1
    return items[:limit]


@app.route('/leaderboard')
def leaderboard():
    rankings = get_group_rankings()
    individuals = get_individual_rankings(limit=20)
    return render_template('leaderboard.html',
                           rankings=rankings,
                           individuals=individuals)


@app.route('/guide')
def guide():
    return render_template('guide.html')


@app.route('/download/challenge_data')
def download_challenge_data():
    path = config.CHALLENGE_DATA_PATH
    if not os.path.exists(path):
        abort(404)
    return send_file(path, as_attachment=True, download_name='challenge_data.dat')


@app.route('/api/leaderboard')
def api_leaderboard():
    rankings = get_group_rankings()
    data = []
    for r in rankings:
        data.append({
            'rank': r['rank'],
            'group_id': r['group'].id,
            'group_name': r['group'].name,
            'total': r['total'],
            'completed': r['completed'],
            'progress': r['progress'],
            'current_runner': r['current_runner'].name if r['current_runner'] else None,
            'elapsed': format_timedelta(r['elapsed']),
            'is_finished': r['is_finished'],
        })
    return jsonify(data)


@app.route('/logout')
def logout():
    session.pop('runner_id', None)
    return redirect(url_for('index'))


# ============================================================
# 관리자 라우트
# ============================================================
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form.get('username', '')
        password = request.form.get('password', '')
        if username == config.ADMIN_USERNAME and password == config.ADMIN_PASSWORD:
            session['is_admin'] = True
            session.permanent = True
            return redirect(url_for('admin_dashboard'))
        flash('관리자 인증 실패', 'danger')
    return render_template('admin_login.html')


@app.route('/admin')
@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    groups = Group.query.order_by(Group.id).all()
    all_runners = Runner.query.order_by(Runner.group_id, Runner.run_order).all()

    # 그룹별로 묶기
    grouped = {}
    for runner in all_runners:
        grouped.setdefault(runner.group_id, []).append(runner)

    rankings = get_group_rankings()
    total_completed = sum(r['completed'] for r in rankings)
    total_runners = sum(r['total'] for r in rankings)

    return render_template('admin_dashboard.html',
                           groups=groups,
                           grouped=grouped,
                           rankings=rankings,
                           total_completed=total_completed,
                           total_runners=total_runners)


@app.route('/admin/skip/<int:runner_id>', methods=['POST'])
@admin_required
def admin_skip(runner_id):
    runner = Runner.query.get_or_404(runner_id)
    if runner.status in ('waiting', 'active'):
        reason = request.form.get('reason', '').strip()[:200]
        runner.status = 'skipped'
        # Skip은 개인 경과시간에서 제외 → completed_at 비워둠
        runner.completed_at = None
        runner.reason = reason or None
        _activate_next_runner(runner)
        db.session.commit()
        if _is_ajax():
            return jsonify({'ok': True})
        flash(f'{runner.name} 건너뛰기 완료', 'info')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/pass/<int:runner_id>', methods=['POST'])
@admin_required
def admin_pass(runner_id):
    runner = Runner.query.get_or_404(runner_id)
    if runner.status in ('waiting', 'active'):
        reason = request.form.get('reason', '').strip()[:200]
        # started_at이 아직 없으면 지금으로 설정(로그인 전에 PASS 처리된 경우)
        if not runner.started_at:
            runner.started_at = datetime.utcnow()
        runner.status = 'passed'
        runner.completed_at = datetime.utcnow()
        runner.reason = reason or None
        runner.submitted_answer = '[admin_pass]'
        _activate_next_runner(runner)
        db.session.commit()
        if _is_ajax():
            return jsonify({'ok': True})
        flash(f'{runner.name} PASS 처리 완료', 'success')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/defer/<int:runner_id>', methods=['POST'])
@admin_required
def admin_defer(runner_id):
    runner = Runner.query.get_or_404(runner_id)
    if runner.status not in ('waiting', 'active'):
        flash('대기 중 또는 진행 중인 주자만 미룰 수 있습니다.', 'warning')
        return redirect(url_for('admin_dashboard'))

    was_active = runner.status == 'active'
    old_order = runner.run_order

    # 조의 마지막 run_order 조회
    max_order = db.session.query(db.func.max(Runner.run_order))\
        .filter_by(group_id=runner.group_id).scalar()

    if old_order == max_order:
        flash(f'{runner.name}은(는) 이미 마지막 주자입니다.', 'info')
        return redirect(url_for('admin_dashboard'))

    # old_order보다 뒤에 있는 waiting 주자들의 순서를 1씩 당기기
    later_runners = Runner.query.filter(
        Runner.group_id == runner.group_id,
        Runner.run_order > old_order
    ).order_by(Runner.run_order).all()

    for r in later_runners:
        r.run_order -= 1

    # 해당 주자를 맨 뒤로 (시작 시간 초기화)
    runner.run_order = max_order
    runner.status = 'waiting'
    runner.password = ''
    runner.started_at = None
    runner.attempts = 0

    # active였으면 다음 주자(당겨진 순서) 활성화
    if was_active:
        next_runner = Runner.query.filter_by(
            group_id=runner.group_id,
            run_order=old_order
        ).first()
        if next_runner and next_runner.status == 'waiting':
            password = generate_password()
            next_runner.password = password
            next_runner.status = 'active'

    db.session.commit()
    if _is_ajax():
        return jsonify({'ok': True})
    flash(f'{runner.name}을(를) 맨 뒤로 미뤘습니다. (새 순서: {max_order}번째)', 'info')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/reset/<int:runner_id>', methods=['POST'])
@admin_required
def admin_reset(runner_id):
    runner = Runner.query.get_or_404(runner_id)

    # 다음 주자가 이미 활성화되어 있으면 되돌리기
    next_runner = Runner.query.filter_by(
        group_id=runner.group_id,
        run_order=runner.run_order + 1
    ).first()
    if next_runner and next_runner.status == 'active' and not next_runner.started_at:
        next_runner.status = 'waiting'
        next_runner.password = ''

    runner.status = 'active'
    runner.completed_at = None
    runner.submitted_answer = None
    runner.attempts = 0
    runner.next_runner_password = None
    runner.reason = None

    # 조 완료 상태도 리셋
    group = db.session.get(Group,runner.group_id)
    if group.finished_at:
        group.finished_at = None

    db.session.commit()
    if _is_ajax():
        return jsonify({'ok': True})
    flash(f'{runner.name} 리셋 완료', 'info')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/partial/groups')
@admin_required
def admin_partial_groups():
    groups = Group.query.order_by(Group.id).all()
    all_runners = Runner.query.order_by(Runner.group_id, Runner.run_order).all()
    grouped = {}
    for runner in all_runners:
        grouped.setdefault(runner.group_id, []).append(runner)
    rankings = get_group_rankings()
    total_completed = sum(r['completed'] for r in rankings)
    total_runners = sum(r['total'] for r in rankings)
    return render_template('_admin_groups.html',
                           groups=groups,
                           grouped=grouped,
                           rankings=rankings,
                           total_completed=total_completed,
                           total_runners=total_runners)


@app.route('/admin/api/status')
@admin_required
def admin_api_status():
    rankings = get_group_rankings()
    data = {
        'total_completed': sum(r['completed'] for r in rankings),
        'total_runners': sum(r['total'] for r in rankings),
        'groups': [],
    }
    for r in rankings:
        runners = Runner.query.filter_by(group_id=r['group'].id)\
            .order_by(Runner.run_order).all()
        runner_list = []
        for rn in runners:
            elapsed = None
            if rn.status != 'skipped' and rn.started_at and rn.completed_at:
                elapsed = format_timedelta(rn.completed_at - rn.started_at)
            runner_list.append({
                'id': rn.id,
                'name': rn.name,
                'knox_id': rn.knox_id,
                'run_order': rn.run_order,
                'status': rn.status,
                'attempts': rn.attempts,
                'elapsed': elapsed,
                'reason': rn.reason,
            })
        data['groups'].append({
            'group_id': r['group'].id,
            'group_name': r['group'].name,
            'rank': r['rank'],
            'completed': r['completed'],
            'total': r['total'],
            'progress': r['progress'],
            'is_finished': r['is_finished'],
            'elapsed': format_timedelta(r['elapsed']),
            'runners': runner_list,
        })
    return jsonify(data)


@app.route('/admin/logout')
def admin_logout():
    session.pop('is_admin', None)
    return redirect(url_for('index'))


# ============================================================
# 내부 함수
# ============================================================
def _activate_next_runner(current_runner):
    """현재 주자 완료 시 다음 주자를 활성화합니다.

    defer로 미뤄진 주자도 run_order가 맨 뒤로 갔기 때문에 자동으로 이어짐.
    조 완주는 조 내 모든 주자가 종료 상태(completed/passed/skipped)일 때만 확정.
    """
    next_runner = Runner.query.filter_by(
        group_id=current_runner.group_id,
        run_order=current_runner.run_order + 1
    ).first()

    if next_runner and next_runner.status == 'waiting':
        password = generate_password()
        next_runner.password = password
        next_runner.status = 'active'
        current_runner.next_runner_password = password
        return

    # 다음이 없거나 이미 활성/완료 상태면, 조 내 남은 waiting 주자를 찾음
    remaining = Runner.query.filter_by(
        group_id=current_runner.group_id,
        status='waiting'
    ).order_by(Runner.run_order).first()
    if remaining:
        password = generate_password()
        remaining.password = password
        remaining.status = 'active'
        current_runner.next_runner_password = password
        return

    # 모두 종료 → 조 완주
    group = db.session.get(Group, current_runner.group_id)
    if not group.finished_at:
        group.finished_at = datetime.utcnow()


# ============================================================
# 실행
# ============================================================
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=True)
