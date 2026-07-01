#!/usr/bin/env python3
"""문서 내용 평가 스크립트 (PostToolUse hook).

cases/*/작성_최종/·작성_초안/의 .txt 하네스 생성물(대응 진행로그·검증이력
보유)이 Write/Edit될 때 자동 실행. Phase 1(validate_doc.py)이 통과한 경우에만
서브에이전트 독립 평가를 트리거한다.

평가 기준은 harness/quality/검증_체크리스트.md의 [Phase 2] 절을 직접 파싱하여
구성한다. 평가 기준의 정본은 체크리스트 하나이며, 스크립트에 항목을 중복
하드코딩하지 않는다(드리프트 방지).

대상 판정:
  - 경로를 정규화하여 작성_최종/작성_초안 디렉토리의 .txt만 후보로 한다
    (Windows 백슬래시 경로 포함).
  - 후보 중 동일 사건 폴더에 진행로그/검증이력 짝이 있는 하네스 생성물만 검증한다.
    짝이 없으면 기존 원본 문서로 보고 검증에서 제외한다.

Phase 1 미통과 시 exit 0. 전 통과 시 서브에이전트 프롬프트를 stderr에 출력하고 exit 2.
검증이력에 "Phase 2 (내용 평가): pass (hash: XXXX)"가 있고 해시가 일치하면 즉시 종료.

사용법:
  stdin으로 PostToolUse hook JSON을 받는다.
  python validate_doc_eval.py <project_dir>
"""

import sys
import os
import re
import json
import glob
import hashlib


LEGAL_DOC_MARKERS = [
    r'보\s*충\s*서\s*면', r'청\s*구\s*이\s*유', r'별\s*지',
    r'법리보충\s*참고자료', r'요\s*약\s*본', r'청\s*구\s*취\s*지',
    r'피청구인', r'청\s*구\s*인', r'정보공개', r'행정심판',
]

WRITING_DIRS = ('작성_최종', '작성_초안')


def in_writing_dir(normalized_path):
    """cases/ 아래 작성_최종/작성_초안 경로인지 판정 (정규화된 경로)."""
    if '/cases/' not in normalized_path and not normalized_path.startswith('cases/'):
        return False
    return any(f'/{d}/' in normalized_path for d in WRITING_DIRS)


def has_harness_pair(file_path):
    """동일 사건 폴더(상위 3단계)에 진행로그/검증이력이 있으면
    하네스 생성물로 간주한다. 없으면 기존 원본으로 보고 검증에서 제외."""
    directory = os.path.dirname(os.path.abspath(file_path))
    for _ in range(3):
        if (glob.glob(os.path.join(directory, '진행로그_*.md')) or
                glob.glob(os.path.join(directory, '검증이력_*.md'))):
            return True
        parent = os.path.dirname(directory)
        if parent == directory:
            break
        directory = parent
    return False


def is_legal_document(text):
    count = sum(1 for p in LEGAL_DOC_MARKERS if re.search(p, text[:2000]))
    return count >= 2


def compute_doc_hash(text):
    return hashlib.sha256(text.strip().encode()).hexdigest()[:12]


def load_phase2_criteria(harness_dir):
    """검증_체크리스트.md에서 [Phase 2]가 포함된 절(§)을 파싱한다.

    반환: [(ref, title, [checkbox 문구, ...]), ...]
    절 제목과 `[Phase N]` 태그 형식에 의존하므로, 체크리스트의 형식이
    유지되어야 한다(validate_harness.py가 이 정합성을 점검).
    """
    path = os.path.join(harness_dir, 'quality/검증_체크리스트.md')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            text = f.read()
    except FileNotFoundError:
        return []
    criteria = []
    sections = re.split(r'\n(?=##\s+\d+\.)', text)
    for sec in sections:
        m = re.match(r'##\s+(\d+)\.\s+(.+?)\s*`\[([^\]]+)\]`', sec)
        if not m:
            continue
        num, title, tag = m.group(1), m.group(2).strip(), m.group(3)
        if 'Phase 2' not in tag:
            continue
        items = re.findall(r'^\s*-\s*\[\s*\]\s*(.+?)\s*$', sec, re.MULTILINE)
        criteria.append((f'§{num}', title, items))
    return criteria


def find_file_in_ancestors(file_path, pattern, max_levels=3):
    directory = os.path.dirname(os.path.abspath(file_path))
    for _ in range(max_levels):
        for p in glob.glob(os.path.join(directory, pattern)):
            if os.path.isfile(p):
                return p
        for p in glob.glob(os.path.join(directory, '**', pattern),
                           recursive=True):
            if os.path.isfile(p):
                return p
        parent = os.path.dirname(directory)
        if parent == directory:
            break
        directory = parent
    return None


def check_already_passed(file_path, doc_hash):
    history_path = find_file_in_ancestors(file_path, '검증이력_*.md')
    if not history_path:
        return False
    try:
        with open(history_path, 'r', encoding='utf-8') as f:
            text = f.read()
    except (FileNotFoundError, UnicodeDecodeError):
        return False

    for pass_match in re.finditer(
        r'-\s*Phase\s*2[^:]*:\s*pass\s*\(hash:\s*([a-f0-9]+)\)',
        text
    ):
        if pass_match.group(1) == doc_hash:
            return True
    return False


def scripts_would_pass(text, file_path, project_dir):
    script_dir = os.path.join(project_dir, 'harness', 'scripts')
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    try:
        from validate_doc import validate as validate_phase1
        failures = validate_phase1(text, file_path, project_dir)
        return len(failures) == 0
    except ImportError:
        return True


def build_evaluation_prompt(file_path, graph_path, doc_hash, criteria):
    lines = [
        '[문서 내용 평가 - 서브에이전트 독립 검증 필요]',
        '',
        'Phase 1 스크립트 검증을 모두 통과하였습니다.',
        'Agent 도구로 서브에이전트를 생성하여 독립 평가를 수행하십시오.',
        '',
        '서브에이전트 프롬프트:',
        '---',
        '법률 문서의 논증 품질을 독립·적대적으로 검증하라. 이 평가는 체크리스트를',
        '순회 확인하는 절차가 아니다. 숙련된 법률가인 사용자가 이 문서를 받는다면',
        '어디를 첨삭·반려할지 능동적으로 탐색하는 것이 목표다. 각 기준을 "통과"로',
        '추정하며 훑지 말고, 결함을 찾아내려는 반증 태세로 적용하라.',
        '',
        f'1. Read 도구로 문서 전체를 읽어라: {file_path}',
    ]
    if graph_path:
        lines.append(f'2. Read 도구로 법리 그래프를 읽어라: {graph_path}')

    lines.extend([
        '',
        '문서가 정보공개 거부처분 청구인(신청인) 측 보충서면이면, '
        'harness/style/논증_가이드라인.md의 해당 절(특히 §14 공격 표면 최소화)을 '
        'Read로 읽고, 그 고려사항이 이 사안의 맥락(입증책임 구조·청구취지 범위·해당 '
        '비공개 호의 성격)에 비추어 적절히 반영·판단됐는지 적대적으로 평가하라. 이는 '
        '고정 규칙을 기계적으로 대조하는 것이 아니라 사안 적합성을 판단하는 것이다. '
        '전략적 선택(무엇을 다투고 무엇을 넘길지, 입증 주체·요건을 어떻게 서술할지, '
        '보충 범위를 어디까지로 잡을지)은 사안마다 다르므로, "언제나 X"가 아니라 '
        '"이 사안에서 X가 적절한가"를 묻는다.',
    ])

    lines.append('')
    lines.append('아래 기준(harness/quality/검증_체크리스트.md의 [Phase 2] 항목)으로 '
                 '평가하라. 각 기준을 반증 태세로 적용하고, 미충족·의심 지점을 구체적으로 지적하라:')
    for ref, title, items in criteria:
        lines.append(f'  {ref} {title}')
        for it in items:
            lines.append(f'    - {it}')

    lines.extend([
        '',
        '결과를 JSON으로 반환하라:',
        '{"pass": true/false, "findings": ['
        '{"criterion": "§N", "status": "pass"/"fail", "detail": "..."}]}',
        '---',
        '',
    ])

    basename = os.path.splitext(os.path.basename(file_path))[0]
    history_name = f'검증이력_{basename}.md'

    lines.extend([
        f'서브에이전트 결과에 따라 {history_name}에',
        f'"- Phase 2 (내용 평가): pass (hash: {doc_hash})" 또는',
        f'"- Phase 2 (내용 평가): fail -> [수정 내용]"을 기록하십시오.',
        'pass 시 반드시 hash를 포함해야 이후 턴에서 재평가를 스킵합니다.',
    ])

    return '\n'.join(lines)


def main():
    target_from_stdin = None
    try:
        stdin_data = sys.stdin.read()
        if stdin_data.strip():
            data = json.loads(stdin_data)
            fp = data.get('tool_input', {}).get('file_path', '')
            _, ext = os.path.splitext(fp)
            normalized = os.path.normpath(fp).replace('\\', '/')
            if ext.lower() not in ('.txt',):
                sys.exit(0)
            if not in_writing_dir(normalized):
                sys.exit(0)
            if not has_harness_pair(fp):
                sys.exit(0)
            target_from_stdin = fp
    except (json.JSONDecodeError, KeyError, TypeError):
        sys.exit(0)

    if not target_from_stdin:
        sys.exit(0)

    project_dir = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    file_path = target_from_stdin

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            text = f.read()
    except (FileNotFoundError, PermissionError, UnicodeDecodeError):
        sys.exit(0)

    if not is_legal_document(text):
        sys.exit(0)

    doc_hash = compute_doc_hash(text)

    if check_already_passed(file_path, doc_hash):
        sys.exit(0)

    if not scripts_would_pass(text, file_path, project_dir):
        sys.exit(0)

    harness_dir = os.path.join(project_dir, 'harness')
    criteria = load_phase2_criteria(harness_dir)
    if not criteria:
        sys.exit(0)

    graph_path = find_file_in_ancestors(file_path, '법리그래프_*.md')
    prompt = build_evaluation_prompt(file_path, graph_path, doc_hash, criteria)

    print(prompt, file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
