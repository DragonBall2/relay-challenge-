"""difficulty='hard' 통합 — 외부 hard-challenge repo의 problems.json을 로드.

설정 (settings.json 또는 환경변수):
  hard_problems_path  : problems.json 절대 경로 (예: C:/.../hard-challenge/problems.json)
  hard_repo_url       : 참가자에게 안내할 repo URL (예: https://github-internal/.../hard-challenge.git)

각 problem을 generate_challenge.Problem 호환 형태로 반환.
미루기용 스페어는 hard에서는 "같은 problem_id에 새 seed 변형" 대신 별도 spare 풀을
같은 JSON에서 추출 (problem.spare=True 같은 표시). PoC에서는 main 풀을 그대로
spare로도 활용 (단순화).
"""
import json
import os
from typing import Any


class HardProblem:
    """generate_challenge.Problem 인터페이스 호환 (pid/ptype/text/answer/params)."""
    def __init__(self, pid, ptype, text, answer, params):
        self.pid = pid
        self.ptype = ptype
        self.text = text
        self.answer = answer
        self.params = params


def _format_problem_text(entry: dict, repo_url: str) -> str:
    """challenge.html이 그대로 노출할 문제 텍스트.

    포맷: Markdown처럼 보이는 텍스트. 줄바꿈/구분선으로 가독성 확보.
    """
    branch = entry['branch']
    question = entry['question']
    pid = entry['problem_id']
    return (
        f"# Hard Problem #{pid:03d}\n\n"
        f"GitHub Repo: {repo_url}\n"
        f"Branch: {branch}\n\n"
        f"진행 순서 (⚠️ venv 사용 강력 권장 — 본인 환경 오염 방지):\n"
        f"  1. git clone {repo_url}\n"
        f"  2. cd hard-challenge && git checkout {branch}\n"
        f"  3. python -m venv .venv\n"
        f"  4. .venv\\Scripts\\Activate.ps1   (PowerShell)\n"
        f"     .venv\\Scripts\\activate.bat  (cmd)\n"
        f"     source .venv/bin/activate    (Linux/Mac)\n"
        f"  5. pip install -r requirements.txt\n"
        f"  6. python -m app.seed\n"
        f"  7. uvicorn app.main:app --port 8000\n"
        f"  8. http://localhost:8000/ 접속해 버그 디버깅\n\n"
        f"질문:\n"
        f"  {question}\n\n"
        f"답: 화면에서 확인한 값 (콤마/단위 없이 숫자만)"
    )


def load_hard_problems(problems_path: str, repo_url: str, total: int,
                       buffer_count: int) -> tuple[list, list]:
    """problems.json을 읽어 main + spare 풀 반환.

    return: (main_problems[total], spare_problems[buffer_count])

    main과 spare는 동일 JSON에서 cycle하여 채움 (PoC).
    """
    if not os.path.exists(problems_path):
        raise RuntimeError(f'hard problems.json not found: {problems_path}')
    with open(problems_path, encoding='utf-8') as f:
        entries: list[dict] = json.load(f)
    if not entries:
        raise RuntimeError('problems.json 비어있음')

    needed = total + buffer_count
    out: list[HardProblem] = []
    for i in range(needed):
        e = entries[i % len(entries)]
        # spare로 재사용 시 problem_id가 같아질 수 있음 — PoC에서는 허용
        text = _format_problem_text(e, repo_url)
        out.append(HardProblem(
            pid=e['problem_id'],
            ptype='HARD',
            text=text,
            answer=str(e['correct_answer']),
            params={'bug_id': e['bug_id'], 'seed': e['seed'],
                    'branch': e['branch']},
        ))
    return out[:total], out[total:total + buffer_count]
