#!/usr/bin/env python3
"""그래프 내용 평가 스크립트 (PostToolUse hook).

법리그래프_*.md 파일이 Write/Edit될 때 PostToolUse hook으로
실행된다. 그래프 파일의 JSON을 파싱하고,
data/법리_데이터베이스.md에서 각 법리의 연관 법리·적용 사례를
추출하여 독립 평가 프롬프트를 구성한다.

exit 2로 모델 재진입을 유발하되, 메인 에이전트에게 Agent 도구로
서브에이전트를 생성하여 독립 평가를 수행하도록 지시한다.
서브에이전트는 메인 대화의 컨텍스트를 공유하지 않으므로
확증 편향 없는 평가가 가능하다.

already_passed 판정은 엄격한 정규식과 JSON 해시로 수행한다.
그래프 JSON이 변경되면 기존 pass 기록을 무시하고 재평가한다.

PostToolUse hook이므로, stdin의 tool_input.file_path에
'법리그래프_'가 포함된 경우에만 실행하고 아니면 즉시 exit 0.

사용법:
  python validate_graph_eval.py <project_dir>
"""

import sys
import os
import re
import json
import glob
import hashlib


def find_recent_graph(project_dir):
    candidates = []
    for fpath in glob.glob(
        os.path.join(project_dir, '**', '법리그래프_*.md'),
        recursive=True
    ):
        normalized = os.path.normpath(fpath).replace('\\', '/')
        if '/harness/' in normalized or '/.claude/' in normalized:
            continue
        try:
            mtime = os.path.getmtime(fpath)
            candidates.append((mtime, fpath))
        except OSError:
            continue
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def compute_json_hash(json_text):
    try:
        data = json.loads(json_text)
        normalized = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
        return hashlib.sha256(normalized.encode()).hexdigest()[:12]
    except json.JSONDecodeError:
        return hashlib.sha256(json_text.strip().encode()).hexdigest()[:12]


def parse_graph_json(graph_path):
    try:
        with open(graph_path, 'r', encoding='utf-8') as f:
            text = f.read()
    except (FileNotFoundError, UnicodeDecodeError):
        return None, None, None

    m = re.search(r'```json\s*\n(.*?)\n```', text, re.DOTALL)
    if not m:
        return None, None, None

    json_text = m.group(1)
    current_hash = compute_json_hash(json_text)

    for pass_match in re.finditer(
        r'-\s*(?:내용\s*평가\(C\)|Phase\s*2[^:]*?):\s*pass\s*\(hash:\s*([a-f0-9]+)\)',
        text
    ):
        if pass_match.group(1) == current_hash:
            return None, 'already_passed', None

    try:
        return json.loads(json_text), None, current_hash
    except json.JSONDecodeError:
        return None, None, None


def extract_doctrine_context(harness_dir, codes):
    db_path = os.path.join(harness_dir, 'data/법리_데이터베이스.md')
    try:
        with open(db_path, 'r', encoding='utf-8') as f:
            text = f.read()
    except FileNotFoundError:
        return {}

    result = {}
    sections = re.split(r'\n---\n', text)

    for section in sections:
        id_match = re.search(r'\*\*법리 ID\*\*:\s*(\S+)', section)
        if not id_match:
            continue
        code = id_match.group(1)
        if code not in codes:
            continue

        entry = {'code': code}

        name_match = re.search(r'\*\*법리명\*\*:\s*(.+)', section)
        if name_match:
            entry['name'] = name_match.group(1).strip()

        related_match = re.search(r'\*\*연관 법리\*\*:\s*(.+)', section)
        if related_match:
            entry['related'] = related_match.group(1).strip()

        usage_match = re.search(r'\*\*적용 사례\*\*:\s*(.+)', section)
        if usage_match:
            entry['usage'] = usage_match.group(1).strip()

        basis_match = re.search(r'\*\*근거 법조항\*\*:\s*(.+)', section)
        if basis_match:
            entry['basis'] = basis_match.group(1).strip()

        result[code] = entry

    return result


def find_progress_log(graph_path):
    directory = os.path.dirname(os.path.abspath(graph_path))
    for _ in range(3):
        for p in glob.glob(os.path.join(directory, '진행로그_*.md')):
            return p
        parent = os.path.dirname(directory)
        if parent == directory:
            break
        directory = parent
    return None


def build_evaluation_prompt(graph_data, doctrine_ctx, graph_path, json_hash):
    progress_log = find_progress_log(graph_path)

    lines = [
        '[법리 그래프 내용 평가 — 서브에이전트 독립 검증 필요]',
        '',
        'Phase 1 스크립트 검증을 통과하였습니다.',
        'Agent 도구로 서브에이전트를 생성하여 독립 평가를 수행하십시오.',
        '',
        '서브에이전트 프롬프트:',
        '---',
        f'법리 그래프의 법리 선택·포섭·전략을 독립 평가하라.',
        '',
        f'1. Read 도구로 그래프 파일을 읽어라: {graph_path}',
    ]
    if progress_log:
        lines.append(
            f'2. Read 도구로 진행 로그(5단계 쟁점 목록 포함)를 읽어라: '
            f'{progress_log}'
        )

    lines.append('')
    lines.append('그래프 요약:')
    for issue in graph_data.get('issues', []):
        title = issue.get('title', '?')
        doctrines = issue.get('doctrines', [])
        codes_str = ', '.join(
            f"{d['code']}({d.get('role', '?')})"
            for d in doctrines
        )
        lines.append(f"  쟁점 {issue.get('id', '?')}: {title} -> {codes_str}")
        for d in doctrines:
            sub = d.get('subsumption', '')
            if sub:
                lines.append(f"    {d['code']} 포섭: {sub}")

    lines.append('')
    lines.append('법리DB 참조:')
    for code, ctx in sorted(doctrine_ctx.items()):
        name = ctx.get('name', '')
        related = ctx.get('related', '-')
        usage = ctx.get('usage', '-')
        basis = ctx.get('basis', '-')
        lines.append(f"  {code} ({name})")
        lines.append(f"    근거 법조항: {basis}")
        lines.append(f"    연관 법리: {related}")
        lines.append(f"    적용 사례: {usage}")

    lines.extend([
        '',
        '평가 기준 (미충족 시 구체적으로 지적):',
        '  (1) 쟁점-법리 대응 완전성: 모든 쟁점에 주 법리 배치. '
        '답변서 각 주장에 대응 법리 존재',
        '  (2) 법리 선택 적합성: 주 법리가 핵심 논점에 가장 적합한가',
        '  (3) 법리 연결 정합성: 주→보강→기반 계층 적절. '
        '법리DB 연관 법리와 모순 없음',
        '  (4) 보강·기반 실질적 강화: 주 법리를 실질적으로 강화하는가',
        '  (5) 포섭 적합성: 사실관계 정확 반영. 과소·과대 서술 없음',
        '  (6) 논증 순서: 처분성/적법→본안→절차→부분공개. '
        '읽는 흐름 자연스러움',
        '  (7) 역이용 위험: 상대방 역이용 가능 구조 없음',
        '  (8) 법리DB 누락: 연관 법리 중 적용 가능하나 미포함 법리',
        '',
        '결과를 JSON으로 반환하라:',
        '{"pass": true/false, "findings": ['
        '{"criterion": 1~8, "status": "pass"/"fail", "detail": "..."}]}',
        '---',
        '',
        f'서브에이전트 결과에 따라 {os.path.basename(graph_path)}의 검증 이력에',
        f'"- Phase 2 (내용 평가): pass (hash: {json_hash})" 또는',
        f'"- Phase 2 (내용 평가): fail → [수정 내용]"을 기록하십시오.',
        'pass 시 반드시 hash를 포함해야 이후 턴에서 재평가를 스킵합니다.',
    ])

    return '\n'.join(lines)


def main():
    # PostToolUse hook: stdin에서 tool_input.file_path를 확인하여
    # 법리그래프_ 파일이 아니면 즉시 종료
    try:
        stdin_data = sys.stdin.read()
        if stdin_data.strip():
            data = json.loads(stdin_data)
            file_path = data.get('tool_input', {}).get('file_path', '')
            if '법리그래프_' not in os.path.basename(file_path):
                sys.exit(0)
    except (json.JSONDecodeError, KeyError, TypeError):
        sys.exit(0)

    project_dir = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()

    graph_path = find_recent_graph(project_dir)
    if not graph_path:
        sys.exit(0)

    graph_data, status, json_hash = parse_graph_json(graph_path)
    if status == 'already_passed':
        sys.exit(0)
    if graph_data is None:
        sys.exit(0)

    codes = set()
    for issue in graph_data.get('issues', []):
        for d in issue.get('doctrines', []):
            c = d.get('code', '')
            if c:
                codes.add(c)
    for e in graph_data.get('edges', []):
        for key in ('from', 'to'):
            c = e.get(key, '')
            if c:
                codes.add(c)

    if not codes:
        sys.exit(0)

    harness_dir = os.path.join(project_dir, 'harness')
    doctrine_ctx = extract_doctrine_context(harness_dir, codes)

    prompt = build_evaluation_prompt(graph_data, doctrine_ctx, graph_path, json_hash)

    print(prompt, file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
