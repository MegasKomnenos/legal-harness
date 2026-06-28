#!/usr/bin/env python3
"""그래프 정합성 검증 스크립트.

법리그래프 파일(.md)의 JSON 데이터 블록을 파싱하여
구조적 정합성을 자동 검증한다.

PostToolUse hook으로 자동 실행되거나 단독 실행으로 7단계 그래프 검증을 보조한다.

탐지 항목:
  (a) 그래프 코드가 data/법리_데이터베이스.md에 미수록
  (b) 그래프 판례가 문서에 미반영 (문서가 있는 경우)
  (c) 문서의 판례가 그래프에 미등록 (문서가 있는 경우)
  (d) 쟁점에 주 법리 미배치

사용법:
  PostToolUse hook:
    stdin으로 hook JSON을 받는다.
    python validate_graph.py <project_dir>

  단독 실행:
    python validate_graph.py <project_dir> <doc_file> [graph_file]
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


LEGAL_DOC_MARKERS = [
    r'보\s*충\s*서\s*면', r'청\s*구\s*이\s*유', r'별\s*지',
    r'법리보충\s*참고자료', r'요\s*약\s*본',
    r'피청구인', r'청\s*구\s*인', r'정보공개', r'행정심판',
]


def find_legal_doc(graph_path):
    """그래프 인근에서 법률 문서를 찾는다."""
    doc_dir = os.path.dirname(graph_path)
    search_dirs = [doc_dir]
    parent = os.path.dirname(doc_dir)
    if parent != doc_dir:
        search_dirs.append(parent)
    for d in search_dirs:
        candidates = (
            glob.glob(os.path.join(d, '**', '*.txt'), recursive=True) +
            glob.glob(os.path.join(d, '**', '*.typ'), recursive=True)
        )
        for p in candidates:
            n = os.path.normpath(p).replace('\\', '/')
            if any(s in n for s in ['/harness/', '/.claude/', '/scripts/']):
                continue
            bn = os.path.basename(p)
            if '진행로그_' in bn or '검증이력_' in bn:
                continue
            try:
                with open(p, 'r', encoding='utf-8') as f:
                    head = f.read(2000)
                count = sum(1 for pat in LEGAL_DOC_MARKERS
                            if re.search(pat, head))
                if count >= 2:
                    return p
            except (FileNotFoundError, UnicodeDecodeError):
                continue
    return None


CASE_NUM_RE = re.compile(
    r'(\d{4}(?:두|다|구합|구단|누|나|노|도|루|마|부|카|타|허|후|추|초|재|고합|고단)\d+)'
)

OPPONENT_CITE_RE = re.compile(
    r'피청구인[은이가]?\s*.{0,80}(?:원용|인용|제출|주장)',
    re.DOTALL,
)


def extract_case_numbers_from_text(text):
    return set(CASE_NUM_RE.findall(text))


def is_opponent_citation(text, match_start):
    pre = text[max(0, match_start - 200):match_start]
    return bool(OPPONENT_CITE_RE.search(pre))


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

    if doc_text:
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
                for m in CASE_NUM_RE.finditer(doc_text):
                    if m.group(1) == case_num:
                        if is_opponent_citation(doc_text, m.start()):
                            break
                else:
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


def _report_failures(result):
    if not result['pass']:
        lines = [f"[그래프 검증] {result['failure_count']}건 발견:"]
        for f in result['failures']:
            lines.append(f"  {f['fix']}")
        print('\n'.join(lines), file=sys.stderr)
        sys.exit(2)
    sys.exit(0)


def main():
    target_from_stdin = None
    try:
        stdin_data = sys.stdin.read()
        if stdin_data.strip():
            data = json.loads(stdin_data)
            fp = data.get('tool_input', {}).get('file_path', '')
            if fp:
                target_from_stdin = fp
    except (json.JSONDecodeError, KeyError, TypeError):
        pass

    project_dir = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()

    # CLI 직접 호출 (인수 3개 이상): 기존 동작 유지
    if len(sys.argv) >= 3:
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
        _report_failures(result)

    # PostToolUse hook 실행
    if not target_from_stdin:
        sys.exit(0)

    file_path = target_from_stdin
    normalized = os.path.normpath(file_path).replace('\\', '/')

    if '/harness/' in normalized or '/.claude/' in normalized:
        sys.exit(0)

    basename = os.path.basename(file_path)
    harness_dir = os.path.join(project_dir, 'harness')
    valid_codes = load_doctrine_codes(harness_dir)

    if '법리그래프_' in basename and basename.endswith('.md'):
        graph_data = parse_graph_json(file_path)
        if graph_data is None:
            print(f"[그래프 검증] JSON 파싱 실패: {basename}",
                  file=sys.stderr)
            sys.exit(2)
        result = validate(graph_data, '', valid_codes, file_path)
    else:
        _, ext = os.path.splitext(file_path)
        if ext.lower() not in ('.txt', '.typ'):
            sys.exit(0)
        graph_path = find_graph_file(file_path)
        if not graph_path:
            sys.exit(0)
        graph_data = parse_graph_json(graph_path)
        if graph_data is None:
            sys.exit(0)
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                doc_text = f.read()
        except (FileNotFoundError, UnicodeDecodeError):
            sys.exit(0)
        result = validate(graph_data, doc_text, valid_codes,
                          graph_path, file_path)

    _report_failures(result)


if __name__ == "__main__":
    main()
