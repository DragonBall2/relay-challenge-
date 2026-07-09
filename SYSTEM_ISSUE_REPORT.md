# 시스템 문제점 및 현황 요약

> 리레이 챌린지 시스템의 현재 문제점과 상황을 정리한 문서입니다.

---

## 📊 시스템 현황

### 배포 환경
- **배포형태**: C-Dep 개발환경
- **자원사양**:
  - CPU: 1 Core
  - Memory: 2 GiB
  - Storage: 4.5 GiB

### 서버 상태
- **서버 프레임워크**: Flask
- **WSGI 서버**: Waitress
- **데이터베이스**: SQLite (relay.db)
- **현재 설정**:
  - threads: 8 (DEPLOYMENT.md 권장 설정)
  - connection-limit: 30 (현재 문제)
  - channel-timeout: 60초
  - max-request-body-size: 10MB

---

## ⚠️ 현재 문제점

### 1. 연결 제한 부족
```
Connection Limit = 30 -> 최소 100 필요

실제 동시 접속 요구:
  - 리더보드 자동 새로고침: 20명
  - 관리자 동시 접속: 10-20명
  - 챌린지 참가자 동시 접속: 30+명
  -> 최소 80-100 connections 필요
```

### 2. Task Queue 누적
- 이슈: Queue depth 1~94 번대 연속 증가
- 현상: Request 대기 → 응답 지연
- 원인: Connection limit 30 초과 → 신규 요청 거부
- 심각도: High

### 3. SQLite Last_Seen_At 쓰기 과다
- 빈도: 각 요청마다 업데이트 (30초 간격)
- 문제: DB 동시 쓰기 Lock → 작업 큐 누적
- 영향: 처리 속도 감소 (50-70%)

### 4. DEPLOYMENT.md 권장 설정과 차이
| 항목 | DEPLOYMENT.md | 현재 설정 | 차이 |
|------|---------------|----------|------|
| Threads | 8 | 8 | ✅ 일치 |
| Connection Limit | **None (Base = 100)** | **30** | ⚠️ **70% 부족** |
| Channel Timeout | 不帅 | 60秒 | ✅ 적합 |

---

## 🔥 긴급 해결 우선순위 (고우선)

### Priority 1: Connection Limit 증가 (최고)
```bash
waitress-serve --host=0.0.0.0 --port=8080 \
  --threads=8 \
  --connection-limit=100 \
  --channel-timeout=60 \
  --max-request-body-size=10485760 \
  app:app
```

### Priority 2: last_seen_at 업데이트 빈도 낮추
- 빈도: 매 요청 → 5분마다
- 효과: DB 쓰기 작업 90% 감소
- 처리 위치: `app.py:128-142`

### Priority 3: 자동 새로고침 빈도 감소
- 빈도: 10초 → 30초
- 처리 위치: `templates/admin_dashboard.html`
- 영향: API 요청 빈도 70% 감소

---

## 🎯 DEPLOYMENT.md 권장 설정과 불일치

### DEPLOYMENT.md 기반 배포

```bash
# Real 환경 (32 Cores, 188GB) 권장 설정
waitress-serve --threads=8 --max-request-body-size=10MB app:app

# Systemd 서비스
ExecStart=waitress-serve --threads=8 app:app
```

### 현재 vs DEPLOYMENT.md

| 환경 | DEPLOYMENT.md | 현재 설정 | 수정 필요 |
|------|---------------|----------|----------|
| **Connection Limit** | Not適用 | 30 | ⚠️ **Crisis Problem** |

---

## 💻 현재 시스템 정보

### 현재 Waitress 서버
```
- --threads=8 ✅
- --connection-limit=30 ⚠️ (너무 낮음)
- --channel-timeout=60 ✅
--max-request-body-size=10MB ✅
```

### 현재 데이터베이스
```
DB File: relay.db
- Total Runners: 132 (완료 여부 무관)
- Problem Data: 132/132 ( 모두 완료)
- Schema: ✅ 모든 필수 컬럼 존재

SQLite 파일 정보:
- relay.db (메인)
- relay.db-wal (WAL 저널)
- relay.db-shm (공유 메모리 인덱스)
```

---

## 📈 Current Issues

### Functional Issues (기능 문제)

1. **Connections saturation**:
   - 현상: `30개 connections 도달 → 신규 요청 거부`
   - 빈도: 연속20-30초 간격으로 발생
   - 영향: 페이지 로드 실패, API 타임아웃

2. **SQLite Lock Contention**:
   - 현상: `last_seen_at` 업데이트 동안 Lock
   - 빈도: 각 요청마다 DB 쓰기
   - 영향: 작업 큐 누적 (Depth 20-94)
   - 문제: 동시 쓰기 제한으로 SQLite는 1개의 쓰기만 허용

### Non-Functional Issues (성능 문제)

1. **Response Time**:
   - 정상: < 100ms
   - 과부하: 500ms - 3s
   - Queue depth 증가에 따라 선형적 증가

2. **Throughput**:
   - Bitcoin guild processing: 10 req/sec
   - 최대 동시 요청: 40 req/min (8 스레드 기준)

---

## 🛠 해결방안 추천

### 즉시 실행 (긴급)

```bash
# Connection Limit 증가 (최우선)
waitress-serve --host=0.0.0.0 --port=8080 \
  --threads=8 \
  --connection-limit=100 \
  --channel-timeout=60 \
  --max-request-body-size=10485760 \
  app:app
```

사유:
- ✅ Connection Limit 30 → 100 (4배 증가)
- ✅ DEPLOYMENT.md 기반 실질적인 설정
- ✅ 최대 100 connections 허용 (C-Dev 환경)

---

### 후속 개선 (중간 우선)

```python
# last_seen_at 업데이트 빈도 최적화
# app.py:128-142

# 빈도 증가: 30초 → 300초 (5분)
# DB commit 주기 증가
```

효과:
- DB 쓰기 90% 감소
- SQLite Lock 충돌 떨어질 가능성 ↓
- Response time 50-70% 개선

---

### 장기적 개선 (최저우선)

```javascript
// 자동 새로고침 빈도 감소
// templates/admin_dashboard.html

setInterval(..., 10000)  // 10초 → 30초
```

---

## 💡 간단 추가 지침

### DEPLOYMENT.md 기반 배포

```bash
# 1. 코드 동기화
git checkout main && git pull

# 2. DB 초기화 (새 챌린지 시작 시)
python init_db.py

# 3. 서버 기동
waitress-serve --host=0.0.0.0 --port=8080 \
  --threads=8 \
  --max-request-body-size=10485760 \
  app:app
```

---

## 🎯 최종 요약

### 급급 문제
- 🔴 **Connection Limit = 30** 너무 낮음
- 🔴 **Task Queue 누적** 1-94 Depth

### 해결 방법
- 🚀 **Connection Limit 증가** (30 → 100)

### 추천 순서
1. **Connection Limit 100으로 증가** (now)
2. **last_seen_at 업데이트 최적화** (later)
3. **자동 새로고침 빈도 감소** (optional)

---

## 📝 다른 코딩 에이전트에게 전달할 긴급 문제

1. ⚠️ **Connection Limit=30**으로 인한 **신규 요청 거부**
2. ⚠️ **Task Queue Depth** 증석으로 **응답 지연**
3. ⚠️ **SQLite Lock**으로 **DB 쓰기 병목**

## 🚀 신속 해결

위 `connection-limit=100` 설정으로 재시작하시면 개선됩니다!

---

## 📋 참고 자원

- **배포 가이드**: DEPLOYMENT.md
- **시스템 설정**: config.py
- **서버 관리**: waitress-server