# 회사 서버 배포 가이드 (코딩 에이전트용)

이 문서는 회사 서버(1 CPU / 2 GiB RAM)에 relay-challenge를 안정적으로 배포·운영하기 위한 단계별 안내입니다. 코딩 에이전트가 이 문서를 그대로 따라 실행할 수 있도록 명령 위주로 작성되어 있습니다.

## 0. 사전 확인

```bash
# Python 3.11 이상 권장
python3 --version

# 작업 디렉토리 진입
cd /path/to/relay-challenge
pwd
```

## 1. 코드 동기화

```bash
# 현재 main 브랜치 확인
git branch --show-current

# 최신 main 가져오기
git fetch origin
git log --oneline HEAD..origin/main | head -20   # 가져올 변경 확인
git pull --ff-only origin main
```

만약 fast-forward 불가 에러가 나면, 로컬 변경이 있는 것이므로:
```bash
git stash               # 로컬 변경 임시 보관
git pull --ff-only origin main
git stash pop           # 필요 시 복원
```

## 2. 의존성 설치

```bash
# 가상환경 사용 권장
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip

pip install -r requirements.txt
pip install waitress    # 프로덕션 WSGI 서버
```

## 3. 배포 시나리오 판단

### 시나리오 A: 새 챌린지 시작 (기존 DB 폐기)
- 회사 서버가 처음 설치되거나, 이전 챌린지가 완전히 종료되어 새 조직으로 시작
- **`init_db.py` 실행 or 마법사 사용 가능**

### 시나리오 B: 진행 중인 챌린지에 핫픽스 배포
- 챌린지가 진행 중이고, 코드만 업데이트하고 싶음
- **`init_db.py` 절대 실행 금지** (DB 통째로 날아감)
- 서버 재시작만 (가능한 짧게 — 진행 중 주자가 일시적으로 끊김)

### 시나리오 C: 스키마 변경된 PR 신규 적용
- 이전 배포 후 새 컬럼·테이블이 추가된 PR이 main에 머지됨
- 진행 중 챌린지가 없으면 `init_db.py` 권장
- 진행 중이면 수동 ALTER TABLE 또는 챌린지 종료 후 재시작

**현재 main 기준 스키마 컬럼·테이블** (Runner 기준):
- `id, knox_id, name, group_id, run_order, password`
- `problem_text, problem_type, correct_answer`
- `status, started_at, completed_at, attempts, submitted_answer, next_runner_password, reason`
- `deferred_count, review, review_submitted_at, last_seen_at`
- 별도 테이블: `groups, attempt_logs, spare_problems`

## 4. DB 초기화 (시나리오 A 또는 신규 설치)

```bash
# CLI 폴백 (CSV 파일 있으면 그것 사용, 없으면 테스트 참가자)
python init_db.py

# 또는 서버 띄운 후 관리자 마법사로 (권장)
# /admin/init 에서 N/G/난이도/시드 등 설정
```

### CSV 파일 형식
`Generative-AI팀-AI샌터_챌린지_대상.csv` (UTF-8 with BOM):
```csv
knox_id,name
alice.kim,김앨리스
bob.lee,이밥
...
```

## 5. WAL 모드 확인

이 코드는 자동으로 SQLite WAL 모드를 활성화합니다. 배포 후 확인:

```bash
python3 -c "
import sys
sys.path.insert(0, '.')
from app import app
from models import db
with app.app_context():
    res = db.session.execute(db.text('PRAGMA journal_mode')).fetchone()
    sync = db.session.execute(db.text('PRAGMA synchronous')).fetchone()
    print(f'journal_mode: {res[0]}, synchronous: {sync[0]}')
"
```

기대 출력: `journal_mode: wal, synchronous: 1`

WAL 모드 활성화 후 디렉토리에 다음 파일이 생깁니다 (정상):
- `relay.db` (메인 DB)
- `relay.db-wal` (WAL 저널)
- `relay.db-shm` (공유 메모리 인덱스)

## 6. 프로덕션 서버 기동 (waitress)

### 단발 실행 (테스트용)
```bash
waitress-serve --host=0.0.0.0 --port=8080 --threads=8 --connection-limit=200 --channel-timeout=60 app:app
```

**옵션 설명**:
- `--threads=8`: 동시 요청 처리 스레드 (1코어 환경에서 8~12 적정)
- `--connection-limit=200`: 동시 TCP 연결 허용 수 (기본 100, 130~500명 운영 시 200 권장)
- `--channel-timeout=60`: 유휴 연결 타임아웃 (초)

### 백그라운드 실행 (nohup)
```bash
nohup waitress-serve --host=0.0.0.0 --port=8080 --threads=8 --connection-limit=200 --channel-timeout=60 app:app \
  > waitress.log 2>&1 &

echo $! > waitress.pid
```

중지:
```bash
kill $(cat waitress.pid)
```

### systemd 서비스 (서버 재부팅 시 자동 시작 권장)

`/etc/systemd/system/relay-challenge.service`:
```ini
[Unit]
Description=Relay Challenge (Coding Agent)
After=network.target

[Service]
Type=simple
User=YOUR_USER
WorkingDirectory=/path/to/relay-challenge
Environment="PATH=/path/to/relay-challenge/venv/bin"
ExecStart=/path/to/relay-challenge/venv/bin/waitress-serve --host=0.0.0.0 --port=8080 --threads=8 --connection-limit=200 --channel-timeout=60 app:app
Restart=on-failure
RestartSec=5
MemoryMax=1.5G

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable relay-challenge
sudo systemctl start relay-challenge
sudo systemctl status relay-challenge
```

## 7. 환경 변수 (선택)

`config.py`는 환경변수로 admin 비밀번호 등 오버라이드 가능:
```bash
export ADMIN_USERNAME=admin
export ADMIN_PASSWORD=challenge2026!
export SECRET_KEY=$(python -c 'import secrets; print(secrets.token_hex(32))')
```

운영 시 `SECRET_KEY`는 반드시 무작위 값으로 설정하세요 (세션 보안).

## 8. 헬스 체크

```bash
# 서버 응답 확인
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8080/

# 관리자 로그인 페이지
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8080/admin/login

# 정상 시 모두 200
```

## 9. 모니터링 (운영 중)

```bash
# 메모리·CPU
top -bn1 | grep -E "(waitress|python|relay)"
free -m

# 로그
tail -f waitress.log

# DB 파일 크기
ls -lh relay.db relay.db-wal challenge_data.dat

# 활성 연결 수
ss -tn 'sport = :8080' | wc -l
```

## 10. 운영 중 점검 체크리스트

- [ ] waitress 프로세스가 살아있는가? (`ps aux | grep waitress`)
- [ ] 응답 시간 정상인가? (브라우저에서 /admin/dashboard 새로고침 시 1초 이내)
- [ ] 메모리 사용량 < 500MB인가?
- [ ] 디스크 여유 공간 충분한가? (`df -h .`)
- [ ] DB-wal 파일이 비정상적으로 크지 않은가? (WAL 체크포인트 자동 발생, 일반적으로 수십 MB 이하)

## 11. 자주 발생하는 문제

### A. 포트 점유
```bash
# 다른 프로세스가 8080 사용 중
sudo lsof -i :8080
sudo kill <PID>
```

### B. DB 파일 권한
```bash
chmod 664 relay.db
# 디렉토리도 쓰기 가능해야 (WAL/SHM 파일 생성)
chmod 775 .
```

### C. WAL 파일 누적
일반적으로 자동 체크포인트로 정리됩니다. 수동 정리:
```bash
python3 -c "
import sqlite3
conn = sqlite3.connect('relay.db')
conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
conn.close()
"
```

### D. 응답 지연
- 스레드 부족 → `--threads=16` 으로 증가
- 다만 1코어에서는 8~12개가 적정

## 12. 운영 중지 시

```bash
# 서비스로 운영 중이면
sudo systemctl stop relay-challenge

# nohup으로 운영 중이면
kill $(cat waitress.pid)

# 모든 python 프로세스 정리 (주의: 다른 python 작업도 종료됨)
pkill -f waitress-serve
```

DB 백업:
```bash
# SQLite는 백업 API 사용 (활성 상태에서 안전)
python3 -c "
import sqlite3
src = sqlite3.connect('relay.db')
dst = sqlite3.connect('relay_backup_$(date +%Y%m%d_%H%M%S).db')
src.backup(dst)
src.close(); dst.close()
"
```

또는 단순히 파일 복사 (WAL 정리 후):
```bash
cp relay.db relay_backup_$(date +%Y%m%d_%H%M%S).db
```

## 13. 자주 쓰는 운영 명령 요약

| 작업 | 명령 |
|---|---|
| 코드 업데이트 | `git pull && sudo systemctl restart relay-challenge` |
| 로그 확인 | `tail -f waitress.log` 또는 `journalctl -u relay-challenge -f` |
| 챌린지 공개 | 관리자 대시보드 → 🟢 공개하기 버튼 |
| firstPlayer.txt 확인 | 관리자 대시보드 → firstPlayer.txt 버튼 |
| DB 백업 | 위 12절 백업 명령 |
| 헬스 체크 | `curl http://localhost:8080/admin/login` (200 OK) |

---

## 부록: 리소스 사양 (1 CPU / 2 GiB)에서 예상 부하

- 130명 챌린지: 거뜬히 처리. 메모리 사용 ~200MB, CPU 평균 5% 미만, 피크 시 30%
- 500명 챌린지: 안정적이지만 모니터링 필요. 메모리 ~250MB, CPU 피크 50~70%
- WAL 모드 효과: 동시 읽기 락 충돌 거의 없음 (대시보드 폴링 + 참가자 접속 동시 처리 가능)
- 주의: `init_db.py` 또는 마법사 재생성은 CPU를 5~10초간 점유 → 진행 중 챌린지에선 절대 금지

문제 발생 시 `waitress.log` 또는 `journalctl -u relay-challenge` 확인 후 운영자에게 보고하세요.
