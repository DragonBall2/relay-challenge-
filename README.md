# Coding Agent Relay Challenge

코딩 에이전트(Open Code / Roo Code) 설치 체험을 위한 팀 릴레이 챌린지 운영 시스템입니다.

---

## 주요 특징

### 릴레이 방식 진행
- 이전 주자가 문제를 풀어야 다음 주자가 활성화되는 릴레이 구조
- 다음 주자의 Knox ID와 비밀번호가 정답 제출 시 자동 생성 및 표시
- 첫 번째 주자는 운영자가 직접 비밀번호를 전달하여 시작

### 참가자 구성
- 총 130명, 10개 조, 조당 13명
- CSV 파일로 참가자 등록 (knox_id, name, group 컬럼)
- `group` 컬럼으로 조별 첫번째 주자 지정 가능, 나머지는 랜덤 배정
- `random.seed(2026)` 기반 결정론적 조 배정 (재현 가능)

### 고유 문제 배정
- 참가자 1인당 1개의 고유 문제 배정 (총 130개)
- 5가지 문제 유형: 숫자 합계(A), 레코드 카운트(B), 통계(C), 코드 분석(D), 텍스트 검색(E)
- `challenge_data.dat` 파일을 코딩 에이전트로 분석하여 답 도출

### 부정 방지
- 디바이스 쿠키: 동일 브라우저에서 다른 참가자 대리 풀기 방지
- 비밀번호 방식: active 상태인 주자만 로그인 및 문제 풀기 가능
- 뒤로 미뤄진 주자는 비밀번호 미생성, 차례가 올 때까지 진행 불가

### 관리자 기능
- 실시간 전체 진행 현황 모니터링
- 주자별 시작 시각, 완료 시각(KST), 소요시간, 시도 횟수 확인
- 부재 주자 **뒤로 미루기**: 해당 주자를 조의 맨 뒤로 이동, 다음 주자 자동 활성화
- **건너뛰기**: 주자를 skipped 처리하고 다음 주자 활성화
- **리셋**: 완료/건너뜀 상태의 주자를 초기 상태로 되돌리기
- 오프라인 백업: `groupRoster.txt`로 수동 진행 가능

### 리더보드
- 조별 진행률, 현재 주자, 경과 시간 실시간 표시 (10초마다 갱신)
- 완주 조는 첫 주자 시작 ~ 마지막 주자 완료 시간 기준으로 순위 결정

### 경품
| 순위 | 상품 |
|------|------|
| 1등 (1위 조) | 잠바주스 12만원 쿠폰 |
| 2등 (2위 조) | 잠바주스 9만원 쿠폰 |
| 3등 (3위 조) | 잠바주스 6만원 쿠폰 |
| 완주 | 던킨쿠폰 4만원 |

---

## 기술 스택

- **Backend**: Python, Flask, Flask-SQLAlchemy
- **Database**: SQLite (relay.db)
- **Server**: Waitress (Windows 호환 WSGI)
- **Frontend**: Bootstrap 5, 라이트 테마

---

## 서버 실행

```bash
# 1. 의존성 설치 및 초기화
bash setup_server.sh

# 2. 서버 실행
waitress-serve --host=0.0.0.0 --port=8080 app:app
```

### DB 초기화 (재시작 시)
```bash
# 서버 중지 후
python init_db.py
# 서버 재시작
waitress-serve --host=0.0.0.0 --port=8080 app:app
```

---

## 환경변수 (.env)

```
SECRET_KEY=your-secret-key
ADMIN_PASSWORD=challenge2026!
```

`.env.example` 참고

---

## 관리자 접속

- URL: `/admin/login`
- ID: `admin`
- PW: `.env`의 `ADMIN_PASSWORD` (기본값: `challenge2026!`)
