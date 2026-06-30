#!/usr/bin/env python3
"""단계 게이트 검증 스크립트 (PostToolUse hook).

Write|Edit 시 자동 실행. 하네스 절차의 단계 스킵을 방지한다.

1. 진행로그_*.md Write/Edit 시:
   - 기록된 단계에 빈칸(gap)이 있으면 exit 2

2. 법리그래프_*.md Write/Edit 시:
   - 동일 폴더의 진행로그에 1~5단계 완료가 없으면 exit 2

3. 사건 폴더 법률 문서(.txt) Write/Edit 시:
   - 동일 폴더의 진행로그에 1~7단계 완료가 없으면 exit 2

사용법:
  stdin으로 PostToolUse hook JSON을 받는다.
  python validate_step_gate.py <project_dir>
"""

import sys
import os
import re
import json
import glob


STEP_PATTERN = re.compile(r'###\s*(\d+)단계\s*완료')

SKIP_BASENAMES = {'진행로그_', '검증이력_'}

LEGAL_DOC_MARKERS = [
    r'보\s*충\s*서\s*면',
    r'청\s*구\s*이\s*유',
    r'별\s*지',
    r'법리보충\s*참고자료',
    r'요\s*약\s*본',
    r'피청구인',
    r'청\s*구\s*인',
    r'정보공개',
    r'행정심판',
]


def is_legal_document(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            head = f.read(2000)
    except (FileNotFoundError, UnicodeDecodeError):
        return False
    count = sum(1 for p in LEGAL_DOC_MARKERS if re.search(p, head))
    return count >= 2


WRITING_DIRS = ('작성_최종', '작성_초안')


def in_writing_dir(normalized_path):
    """cases/ 아래 작성_최종/작성_초안 경로인지 판정 (정규화된 경로)."""
    if '/cases/' not in normalized_path and not normalized_path.startswith('cases/'):
        return False
    return any(f'/{d}/' in normalized_path for d in WRITING_DIRS)


def parse_completed_steps(log_path):
    try:
        with open(log_path, 'r', encoding='utf-8') as f:
            text = f.read()
    except (FileNotFoundError, UnicodeDecodeError):
        return set()
    return set(int(m.group(1)) for m in STEP_PATTERN.finditer(text))


def find_progress_log(file_path):
    directory = os.path.dirname(os.path.abspath(file_path))
    for _ in range(3):
        logs = glob.glob(os.path.join(directory, '진행로그_*.md'))
        if logs:
            logs.sort(key=lambda x: os.path.getmtime(x), reverse=True)
            return logs[0]
        parent = os.path.dirname(directory)
        if parent == directory:
            break
        directory = parent
    return None


def check_step_gaps(steps):
    if not steps:
        return []
    return [s for s in range(1, max(steps) + 1) if s not in steps]


def main():
    try:
        stdin_data = sys.stdin.read()
        if not stdin_data.strip():
            sys.exit(0)
        data = json.loads(stdin_data)
        file_path = data.get('tool_input', {}).get('file_path', '')
        if not file_path:
            sys.exit(0)
    except (json.JSONDecodeError, KeyError, TypeError):
        sys.exit(0)

    normalized = os.path.normpath(file_path).replace('\\', '/')
    basename = os.path.basename(file_path)

    if '/harness/' in normalized or '/.claude/' in normalized:
        sys.exit(0)

    # 1. 진행로그 자체의 단계 순서 검증
    if '진행로그_' in basename and basename.endswith('.md'):
        steps = parse_completed_steps(file_path)
        if not steps:
            sys.exit(0)
        gaps = check_step_gaps(steps)
        if gaps:
            gap_str = ', '.join(f'{s}단계' for s in sorted(gaps))
            print(
                f'[단계 게이트] 진행 로그에 단계 빈칸 발견. '
                f'{gap_str}가 완료 기록 없이 건너뛰었습니다. '
                f'각 단계를 순서대로 수행하고 완료를 기록하십시오.',
                file=sys.stderr
            )
            sys.exit(2)
        sys.exit(0)

    # 2. 법리그래프: 1~5단계 완료 필요
    if '법리그래프_' in basename and basename.endswith('.md'):
        log_path = find_progress_log(file_path)
        if not log_path:
            print(
                '[단계 게이트] 진행 로그가 없습니다. '
                '법리 그래프 작성 전에 1~5단계를 수행하고 '
                '진행로그_{문서명}.md에 각 단계 완료를 기록하십시오.',
                file=sys.stderr
            )
            sys.exit(2)
        steps = parse_completed_steps(log_path)
        required = {1, 2, 3, 4, 5}
        missing = required - steps
        if missing:
            missing_str = ', '.join(f'{s}단계' for s in sorted(missing))
            print(
                f'[단계 게이트] 법리 그래프 작성에 필요한 선행 단계 미완료: '
                f'{missing_str}. 해당 단계를 먼저 수행하고 진행 로그에 '
                f'완료를 기록하십시오.',
                file=sys.stderr
            )
            sys.exit(2)
        sys.exit(0)

    # 3. 법률 문서: 작성_최종/작성_초안의 하네스 생성물만, 1~7단계 완료 필요
    _, ext = os.path.splitext(file_path)
    if ext.lower() not in ('.txt',):
        sys.exit(0)

    if any(skip in basename for skip in SKIP_BASENAMES):
        sys.exit(0)

    if not in_writing_dir(normalized):
        sys.exit(0)

    if not is_legal_document(file_path):
        sys.exit(0)

    log_path = find_progress_log(file_path)
    if not log_path:
        # 진행 로그가 없으면 기존 원본 문서로 간주하고 게이트하지 않는다.
        sys.exit(0)
    steps = parse_completed_steps(log_path)
    required = {1, 2, 3, 4, 5, 6, 7}
    missing = required - steps
    if missing:
        missing_str = ', '.join(f'{s}단계' for s in sorted(missing))
        print(
            f'[단계 게이트] 문서 작성에 필요한 선행 단계 미완료: '
            f'{missing_str}. 해당 단계를 먼저 수행하고 진행 로그에 '
            f'완료를 기록하십시오.',
            file=sys.stderr
        )
        sys.exit(2)

    sys.exit(0)


if __name__ == '__main__':
    main()
