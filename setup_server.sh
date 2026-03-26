#!/bin/bash
# ============================================================
# 회사 서버 배포 스크립트
# 서버에서 이 스크립트를 실행하세요.
# ============================================================

set -e

echo "=========================================="
echo " 코딩 에이전트 릴레이 챌린지 - 서버 세팅"
echo "=========================================="

# 1. Python 의존성 설치
echo ""
echo "[1/4] Python 패키지 설치..."
pip install Flask Flask-SQLAlchemy openpyxl waitress

# 2. challenge_data.dat 생성
echo ""
echo "[2/4] 챌린지 데이터 파일 생성..."
python generate_challenge.py

# 3. DB 초기화
echo ""
echo "[3/4] 데이터베이스 초기화..."
echo "※ 실제 참가자 CSV가 있으면 아래 파일을 이 폴더에 복사하세요:"
echo "   Generative-AI팀-AI샌터_챌린지_대상.csv"
echo "   (컬럼: knox_id, name)"
echo ""
read -p "CSV 파일이 준비되었습니까? (y/n, n이면 테스트 데이터 사용): " csv_ready
python init_db.py

# 4. 서버 실행 안내
echo ""
echo "[4/4] 세팅 완료!"
echo "=========================================="
echo ""
echo "생성된 파일:"
echo "  - challenge_data.dat  (참가자 배포용 데이터)"
echo "  - relay.db            (데이터베이스)"
echo "  - firstPlayer.txt     (1번 주자 비밀번호)"
echo "  - challenge_admin.xlsx (오프라인 백업용)"
echo ""
echo "서버 실행 명령어:"
echo "  [개발/테스트] python app.py"
echo "  [운영]        waitress-serve --host=0.0.0.0 --port=8080 app:app"
echo ""
echo "관리자 접속: /admin/login"
echo "  ID: admin"
echo "  PW: challenge2026!  (config.py에서 변경 가능)"
echo ""
echo "=========================================="
cat firstPlayer.txt
echo "=========================================="
