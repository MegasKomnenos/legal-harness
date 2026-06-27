#!/usr/bin/env python3
"""Layer 2: 인용 정합성 검증 스크립트.

PostToolUse hook으로 cases/ 아래 법률 문서(.txt)가
Write/Edit될 때 자동 실행. stdin의 tool_input.file_path가
cases/ 아래 .txt 파일인 경우에만 실행하고 아니면 즉시 exit 0.

사용법:
  python validate_citations.py <project_dir> [target_file]
"""

import sys
import os
import re
import json
import glob


def parse_case_dictionary(harness_dir):
    """02_판례_인용_사전.md에서 수록 판례 목록을 파싱한다."""
    dict_path = os.path.join(harness_dir, '02_판례_인용_사전.md')
    cases = {}

    try:
        with open(dict_path, 'r', encoding='utf-8') as f:
            text = f.read()
    except FileNotFoundError:
        return cases

    # 정식 인용 패턴: - **정식 인용**: 대법원 YYYY. M. D. 선고 XXXX두YYYY 판결
    formal_pattern = re.compile(
        r'\*\*정식 인용\*\*:\s*(.+?)\s*$', re.MULTILINE
    )
    # 사건번호 패턴: ### P-XXX 대법원 YYYYYYY 또는 유사
    id_pattern = re.compile(
        r'###\s+P-\d+\s+.+?(\d{4}[가-힣]+\d+)', re.MULTILINE
    )

    for m in id_pattern.finditer(text):
        case_num = m.group(1)
        cases[case_num] = {"id_tag": m.group(0).strip()}

    for m in formal_pattern.finditer(text):
        formal = m.group(1).strip()
        # 정식 인용에서 사건번호 추출
        num_match = re.search(r'(\d{4}[가-힣]+\d+)', formal)
        if num_match:
            case_num = num_match.group(1)
            if case_num in cases:
                cases[case_num]["formal"] = formal
            else:
                cases[case_num] = {"formal": formal}

    return cases


def parse_statute_dictionary(harness_dir):
    """02-1_법조문_인용_사전.md에서 수록 조문 목록을 파싱한다.

    Returns:
        (statute_keys, law_names) where statute_keys is a set of
        (law_section_title, article_number) tuples and law_names is a set of
        law title strings.
    """
    dict_path = os.path.join(harness_dir, '02-1_법조문_인용_사전.md')
    statute_keys = set()

    try:
        with open(dict_path, 'r', encoding='utf-8') as f:
            text = f.read()
    except FileNotFoundError:
        return statute_keys, set()

    law_pattern = re.compile(r'^##\s+[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩa-zA-Z]+\.\s+(.+?)$', re.MULTILINE)
    law_names = set()
    for m in law_pattern.finditer(text):
        law_names.add(m.group(1).strip())

    current_law = None
    for line in text.split('\n'):
        law_m = re.match(r'^##\s+[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩa-zA-Z]+\.\s+(.+?)$', line)
        if law_m:
            current_law = law_m.group(1).strip()
            continue
        art_m = re.match(r'###\s+S-\d+\s+제(\d+)조', line)
        if art_m and current_law:
            statute_keys.add((current_law, int(art_m.group(1))))

    return statute_keys, law_names


def extract_case_citations(text):
    """문서에서 판례 인용을 추출한다."""
    citations = []

    # 정식 인용: 대법원 YYYY. M. D. 선고 XXXX두YYYY 판결
    formal = re.compile(
        r'(?:대법원|서울행정법원|대구지방법원|의정부지방법원|[가-힣]+법원)\s*'
        r'\d{4}\.\s*\d{1,2}\.\s*\d{1,2}\.\s*선고\s*'
        r'(\d{4}[가-힣]+\d+)\s*판결'
    )
    for m in formal.finditer(text):
        citations.append({
            "case_num": m.group(1),
            "full_match": m.group(0),
            "position": m.start(),
        })

    # 약식 후속 인용: 위 XXXX두YYYY 판결
    informal = re.compile(r'위\s+(\d{4}[가-힣]+\d+)\s*판결')
    for m in informal.finditer(text):
        citations.append({
            "case_num": m.group(1),
            "full_match": m.group(0),
            "position": m.start(),
        })

    # 괄호 부기: (대법원 ... 판결 참조)
    paren = re.compile(
        r'\((?:대법원|서울행정법원|[가-힣]+법원)\s*'
        r'\d{4}\.\s*\d{1,2}\.\s*\d{1,2}\.\s*선고\s*'
        r'(\d{4}[가-힣]+\d+)\s*판결\s*참조\)'
    )
    for m in paren.finditer(text):
        citations.append({
            "case_num": m.group(1),
            "full_match": m.group(0),
            "position": m.start(),
        })

    # 중복 제거 (사건번호 기준)
    seen = set()
    unique = []
    for c in citations:
        if c["case_num"] not in seen:
            seen.add(c["case_num"])
            unique.append(c)

    return unique


def find_target_file(project_dir):
    """가장 최근 수정된 법률 문서 파일을 찾는다."""
    candidates = []

    for ext in ('*.txt', '*.typ'):
        for fpath in glob.glob(os.path.join(project_dir, '**', ext), recursive=True):
            normalized = os.path.normpath(fpath).replace('\\', '/')
            # harness, output, .claude 디렉토리 제외
            if any(skip in normalized for skip in ['/harness/', '/output/', '/.claude/', '/scripts/']):
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


def get_line_number(text, position):
    """텍스트 내 위치(offset)의 줄 번호를 반환한다."""
    return text[:position].count('\n') + 1


LEGAL_DOC_MARKERS = [
    r'보\s*충\s*서\s*면',
    r'청\s*구\s*이\s*유',
    r'별\s*지',
    r'법리보충\s*참고자료',
    r'요\s*약\s*본',
    r'청\s*구\s*취\s*지',
    r'피청구인',
    r'청\s*구\s*인',
    r'정보공개',
    r'행정심판',
]


def is_legal_document(text):
    """법률 문서 여부를 판별한다. 마커 2개 이상 발견 시 법률 문서로 판정."""
    count = sum(1 for p in LEGAL_DOC_MARKERS if re.search(p, text[:2000]))
    return count >= 2


def case_exists_in_output(project_dir, case_num):
    """법령_판례/ 디렉토리에서 해당 사건번호의 판례 원문(PDF 등)이 존재하는지 확인한다."""
    law_dir = os.path.join(project_dir, '법령_판례')
    if not os.path.isdir(law_dir):
        return False
    for fpath in glob.glob(os.path.join(law_dir, '**', f'*{case_num}*'), recursive=True):
        if os.path.isfile(fpath):
            return True
    return False


def extract_statute_citations(text):
    """문서에서 법 조문 인용을 추출한다."""
    citations = []
    statute_cite = re.compile(
        r'「([^」]+)」[^제]*제(\d+)조'
    )
    for m in statute_cite.finditer(text):
        citations.append({
            "law_name": m.group(1).strip(),
            "article": int(m.group(2)),
            "full_match": m.group(0),
            "position": m.start(),
        })
    return citations


def validate(text, case_dict, filename="", project_dir="."):
    """인용 정합성을 검증한다."""
    failures = []
    harness_dir = os.path.join(project_dir, 'harness')

    # 판례 인용 검증
    citations = extract_case_citations(text)
    for cite in citations:
        case_num = cite["case_num"]
        line_num = get_line_number(text, cite["position"])

        if case_num not in case_dict:
            if case_exists_in_output(project_dir, case_num):
                continue

            surrounding = text[max(0, cite["position"]-200):cite["position"]+200]
            preceding = text[max(0, cite["position"]-200):cite["position"]]
            if ('[미검증 판례]' in surrounding or '[미검증판례]' in surrounding
                    or '[피청구인 원용]' in surrounding
                    or re.search(r'피청구인[은이가]?\s*.{0,60}원용', preceding)
                    or re.search(r'답변서에서\s*.{0,60}(?:원용|인용)', preceding)):
                continue
            failures.append({
                "rule": "CASE_NOT_IN_DICT",
                "line": line_num,
                "case_num": case_num,
                "context": cite["full_match"],
                "fix": f"'{case_num}'은 02_판례_인용_사전.md에 미수록이고 "
                       f"법령_판례/에 원문도 없음. 인용 철회 필요",
            })

    # 법조문 인용 검증
    statute_keys, law_names = parse_statute_dictionary(harness_dir)
    if statute_keys:
        statute_cites = extract_statute_citations(text)
        for cite in statute_cites:
            key = (cite["law_name"], cite["article"])
            line_num = get_line_number(text, cite["position"])
            matched_law = cite["law_name"] in law_names
            if matched_law and key not in statute_keys:
                surrounding = text[max(0, cite["position"]-200):cite["position"]+200]
                if '[미검증 조문]' not in surrounding and '[미검증조문]' not in surrounding:
                    failures.append({
                        "rule": "STATUTE_NOT_IN_DICT",
                        "line": line_num,
                        "context": cite["full_match"],
                        "fix": f"'{cite['law_name']}' 제{cite['article']}조는 "
                               f"02-1_법조문_인용_사전.md에 미수록. [미검증 조문] 표시 필요",
                    })

    # 재결 인용 형식 검증
    decree_pattern = re.compile(r'재결\s+제(\S+)호')
    for m in decree_pattern.finditer(text):
        line_num = get_line_number(text, m.start())
        decree_num = m.group(1)
        if not re.match(r'\d{4}-\d+', decree_num):
            failures.append({
                "rule": "DECREE_FORMAT",
                "line": line_num,
                "context": m.group(0),
                "fix": f"재결 호수 형식 불일치. '재결 제YYYY-NNNNN호' 형식 필요 (현재: '{decree_num}')",
            })

    # 교차참조 정합성 (참고자료 제○항 ○목)
    xref_pattern = re.compile(r'참고자료\s*제(\d+)항\s*([가-힣])목')
    for m in xref_pattern.finditer(text):
        line_num = get_line_number(text, m.start())
        failures.append({
            "rule": "XREF_UNVERIFIED",
            "line": line_num,
            "context": m.group(0),
            "fix": "교차참조 번호가 실제 참고자료와 일치하는지 수동 확인 필요",
        })

    return {
        "pass": len(failures) == 0,
        "failure_count": len(failures),
        "failures": failures,
        "citations_found": len(citations),
        "file": filename,
    }


def main():
    # PostToolUse hook: stdin에서 file_path를 확인하여
    # cases/ 아래 .txt 파일이 아니면 즉시 종료
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

    project_dir = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    target_file = sys.argv[2] if len(sys.argv) > 2 else None
    harness_dir = os.path.join(project_dir, 'harness')

    # 인용 사전 파싱
    case_dict = parse_case_dictionary(harness_dir)

    if not case_dict:
        sys.exit(0)

    # 대상 파일 결정
    if target_from_stdin:
        file_path = target_from_stdin
    elif target_file:
        file_path = target_file
    else:
        file_path = find_target_file(project_dir)

    if not file_path:
        sys.exit(0)

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            text = f.read()
    except (FileNotFoundError, PermissionError, UnicodeDecodeError):
        sys.exit(0)

    if not is_legal_document(text):
        sys.exit(0)

    result = validate(text, case_dict, file_path, project_dir)

    if not result["pass"]:
        report_lines = [
            f"[인용 검증] {result['failure_count']}건 불일치 "
            f"({os.path.basename(file_path)}, 인용 {result['citations_found']}건 탐지):"
        ]
        for f in result["failures"]:
            if f["rule"] == "CASE_NOT_IN_DICT":
                report_lines.append(
                    f"  L{f['line']}: [{f['rule']}] {f['case_num']} — {f['fix']}"
                )
            elif f["rule"] == "XREF_UNVERIFIED":
                report_lines.append(
                    f"  L{f['line']}: [{f['rule']}] {f['context']} — {f['fix']}"
                )
            else:
                report_lines.append(
                    f"  L{f['line']}: [{f['rule']}] {f.get('context', '')} — {f['fix']}"
                )

        reason = '\n'.join(report_lines)

        counter_dir = os.path.join(project_dir, '.claude', 'hook_state')
        os.makedirs(counter_dir, exist_ok=True)
        counter_file = os.path.join(
            counter_dir,
            f"citations_block_{os.path.basename(file_path)}.count"
        )
        block_count = 0
        try:
            with open(counter_file, 'r') as cf:
                block_count = int(cf.read().strip())
        except (FileNotFoundError, ValueError):
            pass

        block_count += 1

        if block_count >= 8:
            print(
                f"{reason}\n\n[자동 override] 8회 연속 차단. "
                "수동 확인 후 진행하십시오.",
                file=sys.stderr
            )
            try:
                os.remove(counter_file)
            except OSError:
                pass
            sys.exit(0)

        with open(counter_file, 'w') as cf:
            cf.write(str(block_count))

        print(reason, file=sys.stderr)
        sys.exit(2)

    counter_dir = os.path.join(project_dir, '.claude', 'hook_state')
    counter_file = os.path.join(
        counter_dir,
        f"citations_block_{os.path.basename(file_path)}.count"
    )
    try:
        os.remove(counter_file)
    except (FileNotFoundError, OSError):
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()
