"""사고 후 운영자가 DB 상태를 점검할 때 사용하는 진단 스크립트.

사용:
    cd /path/to/relay-challenge
    python3 tools/check_runners.py

점검 항목:
  1) 짧은 elapsed (< 30초) 로 완료된 주자 — hotfix #1 적용 직후 race 의심 사례
  2) 같은 조에 active 주자가 2명 이상 — admin_reset 빈틈으로 발생할 수 있는 이상 상태
  3) started_at 없이 completed 상태인 주자 — 이론상 hotfix 이후엔 발생 안 함
"""

import os
import sys
from datetime import timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from app import app
from models import Runner
from sqlalchemy import func


def main():
    with app.app_context():
        section('1) 짧은 elapsed (< 30초) 완료 주자')
        suspicious = []
        for r in Runner.query.filter_by(status='completed').all():
            if r.started_at and r.completed_at:
                elapsed = (r.completed_at - r.started_at).total_seconds()
                if elapsed < 30:
                    suspicious.append((r.group_id, r.run_order, r.knox_id, elapsed))
        suspicious.sort()
        if suspicious:
            for g, o, k, e in suspicious:
                print(f'  조{g:>2} 순서{o:>2}: {k:<20} elapsed={e:>5.1f}초')
            print(f'  → 총 {len(suspicious)}명. 개인 시상 검토 필요.')
        else:
            print('  (없음)')

        section('2) 같은 조에 active 주자 2명 이상')
        rows = Runner.query.filter_by(status='active').with_entities(
            Runner.group_id, func.count()
        ).group_by(Runner.group_id).all()
        dup_groups = [(g, c) for g, c in rows if c > 1]
        if dup_groups:
            for g, c in dup_groups:
                actives = Runner.query.filter_by(group_id=g, status='active').order_by(Runner.run_order).all()
                names = ', '.join(f'{r.run_order}번 {r.knox_id}' for r in actives)
                print(f'  ⚠ 조 {g}: {c}명 active ({names})')
            print('  → 관리 대시보드에서 한 명만 남기도록 처리 필요 (완료/뒤로/리셋).')
        else:
            print('  (없음)')

        section('3) started_at 없이 completed 상태')
        broken = Runner.query.filter_by(status='completed').filter(
            Runner.started_at.is_(None)
        ).all()
        if broken:
            for r in broken:
                print(f'  조{r.group_id:>2} 순서{r.run_order:>2}: {r.knox_id} (completed_at={r.completed_at})')
            print('  → hotfix 이전 데이터 잔존. 개인 랭킹에서 자동 제외됨.')
        else:
            print('  (없음)')

        section('요약')
        total = Runner.query.count()
        active = Runner.query.filter_by(status='active').count()
        completed = Runner.query.filter_by(status='completed').count()
        waiting = Runner.query.filter_by(status='waiting').count()
        passed = Runner.query.filter_by(status='passed').count()
        skipped = Runner.query.filter_by(status='skipped').count()
        print(f'  총 {total}명 | active {active} | waiting {waiting} | '
              f'completed {completed} | passed {passed} | skipped {skipped}')


def section(title):
    print()
    print('=' * 60)
    print(' ' + title)
    print('=' * 60)


if __name__ == '__main__':
    main()
