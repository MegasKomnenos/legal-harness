#!/usr/bin/env python3
"""문서 내용 평가 스크립트 (PostToolUse hook).

cases/ 아래 .txt/.typ 법률 문서가 Write/Edit될 때 자동 실행.
Phase 1(validate_doc.py)이 통과한 경우에만
서브에이전트 독립 평가를 트리거한다.

Phase 1 통과 여부를 내부적으로 확인하여, 미통과 시 exit 0
(해당 스크립트의 전용 hook이 차단을 처리). 전 통과 시
서브에이전트 프롬프트를 stderr에 출력하고 exit 2.

검증이력 파일에 "Phase 2 (내용 평가): pass (hash: XXXX)"가
있고 해시가 일치하면 즉시 종료(토큰 소모 0).

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


PHASE2_CRITERIA = [
    ("§3", "소극적 논증 문맥",
     "문단 전체가 적극적 논증으로 구성되었는가. "
     "'제시하지 못하였습니다', '밝히지 않았습니다', '입증하지 못하였습니다' 등 "
     "소극적 종결 후 적극 논증이 부족한 문단이 없는가"),
    ("§5", "논증 흐름",
     "대전제→소전제→결론의 전개가 자연스러운가. "
     "섹션 간 전환이 논리적인가"),
    ("§6", "포섭 구체성",
     "사실관계 적용이 구체적이며 과대·과소 서술이 없는가. "
     "'한정되어 있습니다', '불과합니다', '그칩니다', '뿐입니다' 등으로 "
     "청구 범위를 자발적으로 제약하지 않았는가"),
    ("§7", "표현 적절성",
     "정형 표현 패턴이 준수되었는가. 톤·레지스터가 문서 유형에 맞는가. "
     "'대체로', '대략', '어느 정도', '일종의' 등 추정적·유보적 표현이 없는가"),
    ("§8", "판례 인용 깊이",
     "판례 인용 시 해당 판결의 사실관계와 이 사건의 비교가 충분한가"),
    ("§11", "IRAC 완전성",
     "각 쟁점에 대전제-소전제-결론 3단 구조가 문맥상 완전한가"),
    ("§12", "그래프-문서 정합성",
     "그래프의 모든 법리가 반영되고 논증 순서가 그래프와 일치하는가"),
    ("§13", "출처 구별",
     "조문 문언과 판례 해석 기준이 구별되었는가. "
     "'고도의 개연성', '비교교량', '객관적으로 현저하게' 등 판례 용어를 "
     "사용할 때 해당 판례를 먼저 인용하고 주어를 판례로 명시하였는가. "
     "판례 미인용 맥락에서 조문 문언 대신 판례 용어를 사용하지 않았는가"),
    ("§14", "역이용 위험",
     "'이미 공개된 정보와 동일/같은 수준' 등 상대방이 역이용할 수 있는 "
     "논지가 없는가"),
    ("§15", "그래프 대조",
     "그래프 JSON의 포섭·결론과 문서 논증이 일치하는가"),
    ("§16", "작문 밀도",
     "판례 과잉 나열 없음, 인용→포섭→결론 전환이 자연스러움, "
     "중복 논증 없음, 분량이 논점 복잡도에 비례"),
]

LEGAL_DOC_MARKERS = [
    r'보\s*충\s*서\s*면', r'청\s*구\s*이\s*유', r'별\s*지',
    r'법리보충\s*참고자료', r'요\s*약\s*본', r'청\s*구\s*취\s*지',
    r'피청구인', r'청\s*구\s*인', r'정보공개', r'행정심판',
]


def is_legal_document(text):
    count = sum(1 for p in LEGAL_DOC_MARKERS if re.search(p, text[:2000]))
    return count >= 2


def compute_doc_hash(text):
    return hashlib.sha256(text.strip().encode()).hexdigest()[:12]


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


def build_evaluation_prompt(file_path, graph_path, doc_hash):
    lines = [
        '[문서 내용 평가 — 서브에이전트 독립 검증 필요]',
        '',
        'Phase 1 스크립트 검증을 모두 통과하였습니다.',
        'Agent 도구로 서브에이전트를 생성하여 독립 평가를 수행하십시오.',
        '',
        '서브에이전트 프롬프트:',
        '---',
        f'법률 문서의 논증 품질을 독립 평가하라.',
        '',
        f'1. Read 도구로 문서 전체를 읽어라: {file_path}',
    ]
    if graph_path:
        lines.append(f'2. Read 도구로 법리 그래프를 읽어라: {graph_path}')

    lines.append('')
    lines.append('아래 기준으로 평가하라 (미충족 시 구체적으로 지적):')
    for ref, name, detail in PHASE2_CRITERIA:
        lines.append(f'  {ref} {name}: {detail}')

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
        f'"- Phase 2 (내용 평가): fail → [수정 내용]"을 기록하십시오.',
        f'pass 시 반드시 hash를 포함해야 이후 턴에서 재평가를 스킵합니다.',
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
            if 'cases/' not in fp or ext.lower() not in ('.txt', '.typ'):
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

    graph_path = find_file_in_ancestors(file_path, '법리그래프_*.md')
    prompt = build_evaluation_prompt(file_path, graph_path, doc_hash)

    print(prompt, file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
