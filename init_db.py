#!/usr/bin/env python3
"""
데이터베이스 초기화 스크립트.
참가자/조/문제를 DB에 세팅합니다. CLI 기본값은 130명 10조.

웹(/admin/init)에서 호출 시에는 init_database()에 group_count, group_sizes,
ordered_participants 를 넘겨 순서를 확정한 상태로 초기화합니다.
"""

import os
import sys
import csv
import random
import secrets
import string

from generate_challenge import get_all_problems, main as generate_main

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
CSV_PATH = os.path.join(BASE_DIR, "Generative-AI팀-AI샌터_챌린지_대상.csv")

DEFAULT_NUM_GROUPS = 10
DEFAULT_PARTICIPANTS_PER_GROUP = 13
DEFAULT_TOTAL = DEFAULT_NUM_GROUPS * DEFAULT_PARTICIPANTS_PER_GROUP  # 130


def generate_password(length=8):
    alphabet = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def compute_group_sizes(total: int, group_count: int) -> list[int]:
    """나머지를 앞 조부터 +1명 분배하여 조별 정원 리스트 반환."""
    if group_count <= 0 or total < group_count:
        raise ValueError(f"invalid total={total} group_count={group_count}")
    base = total // group_count
    remainder = total % group_count
    return [base + 1 if g < remainder else base for g in range(group_count)]


def load_participants_from_csv(path):
    """CSV에서 참가자 목록을 읽어옵니다. 컬럼: knox_id, name, group(선택)
    group 컬럼이 있고 값이 1 이상이면 해당 조의 첫 번째 주자로 고정됩니다.
    반환: (first_players dict {group_id: member}, 일반참가자 list)
    """
    first_players = {}
    participants = []
    with open(path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            member = {
                'knox_id': row.get('knox_id', row.get('Knox-ID', '')).strip(),
                'name': row.get('name', row.get('이름', '')).strip(),
            }
            group_val = row.get('group', '').strip()
            if group_val.isdigit() and int(group_val) >= 1:
                first_players[int(group_val)] = member
            else:
                participants.append(member)
    return first_players, participants


def generate_test_participants(n=DEFAULT_TOTAL):
    participants = []
    for i in range(1, n + 1):
        participants.append({
            'knox_id': f'testuser{i:03d}',
            'name': f'테스트참가자{i:03d}',
        })
    return participants


def _write_first_player_file(first_player_lines, path):
    with open(path, 'w', encoding='utf-8') as f:
        f.write("=" * 60 + "\n")
        f.write("코딩 에이전트 릴레이 챌린지 - 첫 번째 주자 비밀번호\n")
        f.write("=" * 60 + "\n\n")
        for line in first_player_lines:
            f.write(line + "\n")
        f.write("\n" + "=" * 60 + "\n")
        f.write("이 비밀번호를 각 조의 첫 번째 주자에게 전달하세요.\n")


def _write_roster_file(group_roster, path):
    with open(path, 'w', encoding='utf-8') as f:
        f.write("=" * 80 + "\n")
        f.write("코딩 에이전트 릴레이 챌린지 - 조별 주자 순서 (비상용)\n")
        f.write("=" * 80 + "\n")
        f.write("※ 시스템 장애 시 이 파일을 보고 수동으로 진행할 수 있습니다.\n")
        f.write("※ 정답이 포함되어 있으므로 참가자에게 공유하지 마세요.\n")
        f.write("=" * 80 + "\n\n")
        for g_id, members in group_roster.items():
            f.write(f"[ 조 {g_id} ]\n")
            f.write(f"{'순서':>4}  {'이름':<20}  {'Knox-ID':<20}  {'정답'}\n")
            f.write("-" * 70 + "\n")
            for m in members:
                f.write(f"{m['order']:>4}  {m['name']:<20}  {m['knox_id']:<20}  {m['answer']}\n")
            f.write("\n")
        f.write("=" * 80 + "\n")


def init_database(
    group_count: int = DEFAULT_NUM_GROUPS,
    group_sizes: list[int] | None = None,
    ordered_participants: list[dict] | None = None,
    regen_challenge_data: bool = False,
    difficulty: str = 'medium',
) -> dict:
    """
    DB를 삭제 후 재생성하고 참가자·조·문제를 배정한다.

    group_sizes=None 이면 group_count에 따라 균등 분배(+나머지는 앞 조부터).
    ordered_participants=None 이면 기존 CSV + random.shuffle 로직으로 폴백 (CLI 하위호환).

    반환: {
        'first_player_lines': [...],
        'roster_path': str,
        'runners': int,
        'groups': int,
    }
    """
    sys.path.insert(0, BASE_DIR)
    from models import db, Group, Runner
    from flask import current_app, has_app_context

    # ordered_participants가 주어지면 N도 거기서 도출
    if ordered_participants is not None:
        total = len(ordered_participants)
    else:
        total = sum(group_sizes) if group_sizes else DEFAULT_TOTAL

    if group_sizes is None:
        group_sizes = compute_group_sizes(total, group_count)
    else:
        if len(group_sizes) != group_count:
            raise ValueError(
                f"group_sizes 길이({len(group_sizes)}) != group_count({group_count})"
            )
        if sum(group_sizes) != total:
            raise ValueError(
                f"group_sizes 합({sum(group_sizes)}) != total({total})"
            )

    # 문제 풀 재생성 (필요 시)
    if regen_challenge_data:
        print(f"challenge_data.dat 재생성 (N={total}, difficulty={difficulty})...")
        generate_main(
            total_problems=total,
            difficulty=difficulty,
            output_path=os.path.join(BASE_DIR, "challenge_data.dat"),
            excel_path=os.path.join(BASE_DIR, "challenge_admin.xlsx"),
        )

    # Flask 앱 컨텍스트가 이미 있으면 재사용(웹 경로), 없으면 새로 생성(CLI 경로)
    from contextlib import nullcontext
    if has_app_context():
        app = current_app._get_current_object()
        ctx = nullcontext()
    else:
        from app import create_app
        app = create_app()
        ctx = app.app_context()

    first_player_lines: list[str] = []
    group_roster: dict[int, list] = {}

    with ctx:
        # 기존 DB 삭제 후 재생성 — Windows에서 파일 잠금 해제
        db.session.remove()
        db.engine.dispose()
        db_path = app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', '')
        if os.path.exists(db_path):
            os.remove(db_path)
            print(f"기존 DB 삭제: {db_path}")

        db.create_all()
        print("DB 테이블 생성 완료")

        try:
            # 1. 문제 로드 (total개)
            print(f"{total}개 문제 로드 중 (difficulty={difficulty})...")
            problems = get_all_problems(total_problems=total, difficulty=difficulty)
            if len(problems) < total:
                raise RuntimeError(
                    f"문제 부족: 요청 {total}개, 생성 {len(problems)}개. "
                    "regen_challenge_data=True 로 재생성하세요."
                )
            print(f"  {len(problems)}개 문제 준비 완료")

            # 2. 참가자 순서 확정
            if ordered_participants is not None:
                # 웹 경로: 이미 확정된 순서 그대로 사용
                final_order = ordered_participants
            else:
                # CLI 폴백: CSV + random.shuffle
                if os.path.exists(CSV_PATH):
                    first_players, participants = load_participants_from_csv(CSV_PATH)
                    print(f"CSV에서 고정 첫번째 주자 {len(first_players)}명, 일반 참가자 {len(participants)}명 로드")
                else:
                    first_players = {}
                    participants = generate_test_participants(total)
                    print(f"테스트 참가자 {len(participants)}명 생성")

                remaining_needed = total - len(first_players)
                if len(participants) < remaining_needed:
                    print(f"경고: 일반 참가자 {len(participants)}명 < 필요 {remaining_needed}명")
                    for i in range(len(participants) + 1, remaining_needed + 1):
                        participants.append({
                            'knox_id': f'extra{i:03d}',
                            'name': f'추가참가자{i:03d}',
                        })
                participants = participants[:remaining_needed]

                random.seed(2026)
                random.shuffle(participants)

                # 조별로 정원만큼 배정, 고정 1번 주자는 앞에
                final_order = []
                regular_idx = 0
                for g in range(group_count):
                    size = group_sizes[g]
                    if (g + 1) in first_players:
                        fixed = first_players[g + 1]
                        members = [fixed] + participants[regular_idx:regular_idx + size - 1]
                        regular_idx += size - 1
                    else:
                        members = participants[regular_idx:regular_idx + size]
                        regular_idx += size
                    for order, m in enumerate(members, 1):
                        final_order.append({
                            'knox_id': m['knox_id'],
                            'name': m['name'],
                            'group': g + 1,
                            'order': order,
                        })

            # 3. 조 생성 및 Runner 레코드 삽입
            for g in range(group_count):
                group = Group(id=g + 1, name=f"조 {g + 1}")
                db.session.add(group)
                group_roster[g + 1] = []

            # 조별 시작 인덱스 (문제 배정용)
            group_offsets = [sum(group_sizes[:g]) for g in range(group_count)]

            for entry in final_order:
                g_id = entry['group']
                order = entry['order']
                problem_idx = group_offsets[g_id - 1] + (order - 1)
                problem = problems[problem_idx]

                if order == 1:
                    password = generate_password()
                    status = 'active'
                    first_player_lines.append(
                        f"조 {g_id} | {entry['name']} ({entry['knox_id']}) | 비밀번호: {password}"
                    )
                else:
                    password = ''
                    status = 'waiting'

                runner = Runner(
                    knox_id=entry['knox_id'],
                    name=entry['name'],
                    group_id=g_id,
                    run_order=order,
                    password=password,
                    problem_text=problem.text,
                    problem_type=problem.ptype,
                    correct_answer=problem.answer,
                    status=status,
                )
                db.session.add(runner)

                group_roster[g_id].append({
                    'order': order,
                    'name': entry['name'],
                    'knox_id': entry['knox_id'],
                    'answer': problem.answer,
                })

            db.session.commit()
            print(f"\n{group_count}개 조, {total}명 배정 완료")

        except Exception:
            db.session.rollback()
            raise

        # 4. 파일 생성
        first_player_path = os.path.join(BASE_DIR, 'firstPlayer.txt')
        _write_first_player_file(first_player_lines, first_player_path)
        print(f"firstPlayer.txt 생성 완료")

        roster_path = os.path.join(BASE_DIR, 'groupRoster.txt')
        _write_roster_file(group_roster, roster_path)
        print(f"groupRoster.txt 생성 완료")

        # 5. 요약
        print("\n" + "=" * 60)
        print("초기화 완료 요약")
        print("=" * 60)
        for line in first_player_lines:
            print(f"  {line}")
        print(f"\n[조별 주자 순서] groupRoster.txt에 저장됨")
        print("=" * 60)

    return {
        'first_player_lines': first_player_lines,
        'roster_path': roster_path,
        'runners': total,
        'groups': group_count,
    }


if __name__ == '__main__':
    init_database()
