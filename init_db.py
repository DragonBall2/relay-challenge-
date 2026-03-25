#!/usr/bin/env python3
"""
데이터베이스 초기화 스크립트.
130명의 참가자, 10개 조, 130개 고유 문제를 DB에 세팅합니다.
"""

import os
import sys
import csv
import random
import secrets
import string

from generate_challenge import get_all_problems

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
CSV_PATH = os.path.join(BASE_DIR, "Generative-AI팀-AI샌터_챌린지_대상.csv")

NUM_GROUPS = 10
PARTICIPANTS_PER_GROUP = 13
TOTAL_PARTICIPANTS = NUM_GROUPS * PARTICIPANTS_PER_GROUP  # 130


def generate_password(length=8):
    alphabet = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def load_participants_from_csv(path):
    """CSV에서 참가자 목록을 읽어옵니다. 컬럼: knox_id, name"""
    participants = []
    with open(path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            participants.append({
                'knox_id': row.get('knox_id', row.get('Knox-ID', '')).strip(),
                'name': row.get('name', row.get('이름', '')).strip(),
            })
    return participants


def generate_test_participants(n=TOTAL_PARTICIPANTS):
    """테스트용 참가자 데이터 생성."""
    participants = []
    for i in range(1, n + 1):
        participants.append({
            'knox_id': f'testuser{i:03d}',
            'name': f'테스트참가자{i:03d}',
        })
    return participants


def init_database():
    # Flask 앱 컨텍스트에서 DB 생성
    sys.path.insert(0, BASE_DIR)
    from app import create_app
    from models import db, Group, Runner

    app = create_app()

    with app.app_context():
        # 기존 DB 삭제 후 재생성
        db_path = app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', '')
        if os.path.exists(db_path):
            os.remove(db_path)
            print(f"기존 DB 삭제: {db_path}")

        db.create_all()
        print("DB 테이블 생성 완료")

        # 1. 참가자 로드
        if os.path.exists(CSV_PATH):
            participants = load_participants_from_csv(CSV_PATH)
            print(f"CSV에서 {len(participants)}명 로드")
        else:
            participants = generate_test_participants()
            print(f"테스트 참가자 {len(participants)}명 생성")

        if len(participants) < TOTAL_PARTICIPANTS:
            print(f"경고: 참가자 {len(participants)}명 < 필요 {TOTAL_PARTICIPANTS}명")
            # 부족한 만큼 추가 생성
            for i in range(len(participants) + 1, TOTAL_PARTICIPANTS + 1):
                participants.append({
                    'knox_id': f'extra{i:03d}',
                    'name': f'추가참가자{i:03d}',
                })

        participants = participants[:TOTAL_PARTICIPANTS]

        # 2. 문제 로드
        print("130개 문제 생성 중...")
        problems = get_all_problems()
        print(f"  {len(problems)}개 문제 준비 완료")

        # 3. 참가자를 랜덤으로 섞어서 10개 조에 배정
        random.seed(2026)
        random.shuffle(participants)

        # 4. 조 생성 및 참가자 배정
        first_player_lines = []

        for g in range(NUM_GROUPS):
            group = Group(id=g + 1, name=f"조 {g + 1}")
            db.session.add(group)

            start = g * PARTICIPANTS_PER_GROUP
            end = start + PARTICIPANTS_PER_GROUP
            group_members = participants[start:end]

            for order, member in enumerate(group_members, 1):
                problem_idx = g * PARTICIPANTS_PER_GROUP + (order - 1)
                problem = problems[problem_idx]

                # 첫 번째 주자는 초기 비밀번호 생성, status='active'
                if order == 1:
                    password = generate_password()
                    status = 'active'
                    first_player_lines.append(
                        f"조 {g + 1} | {member['name']} ({member['knox_id']}) | 비밀번호: {password}"
                    )
                else:
                    password = ''  # 이전 주자가 완료해야 생성됨
                    status = 'waiting'

                runner = Runner(
                    knox_id=member['knox_id'],
                    name=member['name'],
                    group_id=g + 1,
                    run_order=order,
                    password=password,
                    problem_text=problem.text,
                    problem_type=problem.ptype,
                    correct_answer=problem.answer,
                    status=status,
                )
                db.session.add(runner)

        db.session.commit()
        print(f"\n{NUM_GROUPS}개 조, {TOTAL_PARTICIPANTS}명 배정 완료")

        # 5. firstPlayer.txt 생성
        first_player_path = os.path.join(BASE_DIR, 'firstPlayer.txt')
        with open(first_player_path, 'w', encoding='utf-8') as f:
            f.write("=" * 60 + "\n")
            f.write("코딩 에이전트 릴레이 챌린지 - 첫 번째 주자 비밀번호\n")
            f.write("=" * 60 + "\n\n")
            for line in first_player_lines:
                f.write(line + "\n")
            f.write("\n" + "=" * 60 + "\n")
            f.write("이 비밀번호를 각 조의 첫 번째 주자에게 전달하세요.\n")
        print(f"\nfirstPlayer.txt 생성 완료")

        # 6. 요약 출력
        print("\n" + "=" * 60)
        print("초기화 완료 요약")
        print("=" * 60)
        for line in first_player_lines:
            print(f"  {line}")
        print("=" * 60)


if __name__ == '__main__':
    init_database()
