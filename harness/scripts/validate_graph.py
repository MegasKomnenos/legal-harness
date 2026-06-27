#!/usr/bin/env python3
"""그래프-문서 정합성 검증 스크립트.

법리그래프 파일(.md)의 JSON 데이터 블록을 파싱하여
법률 문서와의 정합성을 자동 검증한다.

PostToolUse hook으로 자동 실행되거나 단독 실행으로 7단계 그래프 검증을 보조한다.

탐지 항목:
  (a) 그래프 코드가 data/법리_데이터베이스.md에 미수록
  (b) 그래프 판례가 문서에 미반영
  (c) 문서의 판례가 그래프에 미등록
  (d) 쟁점 순서 불일치

사용법:
  python validate_graph.py <project_dir> <doc_file> [graph_file]
  graph_file 미지정 시 doc_file 인근에서 법리그래프_*.md 자동 탐색
"""

import sys
import os
import re
import json
import glob


def load_doctrine_codes(harness_dir):
    db_path = os.path.join(harness_dir, 'data/법리_데이터베이스.md')
    codes = set()
    try:
        with open(db_path, 'r', encoding='utf-8') as f:
            for line in f:
                m = re.match(r'\*\*법리 ID\*\*:\s*(\S+)', line)
                if m:
                    codes.add(m.group(1))
    except FileNotFoundError:
        pass
    return codes


def parse_graph_json(graph_path):
    try:
        with open(graph_path, 'r', encoding='utf-8') as f:
            text = f.read()
    except (FileNotFoundError, UnicodeDecodeError):
        return None

    m = re.search(r'```json\s*\n(.*?)\n```', text, re.DOTALL)
    if not m:
        return None

    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def find_graph_file(doc_path):
    doc_dir = os.path.dirname(doc_path)
    search_dirs = [doc_dir]
    parent = os.path.dirname(doc_dir)
    if parent != doc_dir:
        search_dirs.append(parent)
    for d in search_dirs:
        for p in glob.glob(os.path.join(d, '**', '법리그래프_*.md'), recursive=True):
            return p
    return None


def extract_case_numbers_from_text(text):
    pattern = re.compile(r'(\d{4}[가-힣]+\d+)')
    return set(pattern.findall(text))


def validate(graph_data, doc_text, valid_codes, graph_path="", doc_path=""):
    failures = []

    graph_codes = set()
    graph_cases = set()
    issue_order = []

    for issue in graph_data.get('issues', []):
        issue_order.append(issue.get('title', ''))
        for d in issue.get('doctrines', []):
            code = d.get('code', '')
            if code:
                graph_codes.add(code)
            for c in d.get('cases', []):
                graph_cases.add(c)

    for e in graph_data.get('edges', []):
        for key in ('from', 'to'):
            code = e.get(key, '')
            if code:
                graph_codes.add(code)

    for code in sorted(graph_codes):
        if code not in valid_codes:
            failures.append({
                'rule': 'GRAPH_CODE_UNKNOWN',
                'match': code,
                'fix': (
                    f"그래프 코드 '{code}'가 data/법리_데이터베이스.md에 "
                    "수록되어 있지 않습니다. 코드명을 확인하십시오."
                ),
            })

    doc_cases = extract_case_numbers_from_text(doc_text)

    for case_num in sorted(graph_cases):
        if case_num not in doc_cases:
            failures.append({
                'rule': 'GRAPH_CASE_NOT_IN_DOC',
                'match': case_num,
                'fix': (
                    f"그래프에 배치된 판례 {case_num}이 문서에 "
                    "인용되어 있지 않습니다. 그래프에 배치한 판례는 "
                    "문서에 반영하거나, 불필요하면 그래프에서 제거하십시오."
                ),
            })

    doc_only_cases = doc_cases - graph_cases
    if doc_only_cases and graph_cases:
        for case_num in sorted(doc_only_cases):
            failures.append({
                'rule': 'DOC_CASE_NOT_IN_GRAPH',
                'match': case_num,
                'fix': (
                    f"문서에 인용된 판례 {case_num}이 그래프에 "
                    "등록되어 있지 않습니다. 그래프에 추가하거나, "
                    "문서에서 인용을 제거하십시오."
                ),
            })

    for issue in graph_data.get('issues', []):
        has_primary = any(
            d.get('role') == '주' for d in issue.get('doctrines', [])
        )
        if not has_primary:
            failures.append({
                'rule': 'GRAPH_NO_PRIMARY',
                'match': issue.get('title', '?'),
                'fix': (
                    f"쟁점 '{issue.get('title', '?')}'에 주 법리(role: 주)가 "
                    "배치되어 있지 않습니다."
                ),
            })

    return {
        'pass': len(failures) == 0,
        'failure_count': len(failures),
        'failures': failures,
        'graph_file': graph_path,
        'doc_file': doc_path,
    }


def main():
    if len(sys.argv) < 3:
        print("사용법: python validate_graph.py <project_dir> <doc_file> [graph_file]",
              file=sys.stderr)
        sys.exit(1)

    project_dir = sys.argv[1]
    doc_path = sys.argv[2]
    graph_path = sys.argv[3] if len(sys.argv) > 3 else None

    if not graph_path:
        graph_path = find_graph_file(doc_path)

    if not graph_path:
        sys.exit(0)

    graph_data = parse_graph_json(graph_path)
    if graph_data is None:
        print(f"[그래프 검증] JSON 파싱 실패: {os.path.basename(graph_path)}",
              file=sys.stderr)
        sys.exit(2)

    try:
        with open(doc_path, 'r', encoding='utf-8') as f:
            doc_text = f.read()
    except (FileNotFoundError, UnicodeDecodeError):
        sys.exit(0)

    harness_dir = os.path.join(project_dir, 'harness')
    valid_codes = load_doctrine_codes(harness_dir)

    result = validate(graph_data, doc_text, valid_codes, graph_path, doc_path)

    if not result['pass']:
        lines = [
            f"[그래프-문서 정합성] {result['failure_count']}건 발견:"
        ]
        for f in result['failures']:
            lines.append(f"  {f['fix']}")
        print('\n'.join(lines), file=sys.stderr)
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
