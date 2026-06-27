#!/usr/bin/env python3
"""Layer 3: 논증 품질 검증 스크립트 (핵심 3종).

PostToolUse hook으로 cases/ 아래 법률 문서(.txt)가
Write/Edit될 때 자동 실행. stdin의 tool_input.file_path가
cases/ 아래 .txt 파일인 경우에만 실행하고 아니면 즉시 exit 0.

탐지 항목:
  (a) 소극적 논증으로 끝나는 문단 (줄 단위가 아닌 문단 단위)
  (b) IRAC 3단 구조 불완전 (대전제만 있고 소전제/결론이 없는 섹션)
  (c) 판례 인용 시 사실관계 비교 누락

사용법:
  python validate_layer3.py <project_dir> [target_file]
"""

import sys
import os
import re
import json
import glob


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
    count = sum(1 for p in LEGAL_DOC_MARKERS if re.search(p, text[:2000]))
    return count >= 2


def find_target_file(project_dir):
    candidates = []
    for ext in ('*.txt', '*.typ'):
        for fpath in glob.glob(os.path.join(project_dir, '**', ext), recursive=True):
            normalized = os.path.normpath(fpath).replace('\\', '/')
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


def split_paragraphs(text):
    """텍스트를 문단 단위로 분할한다. 빈 줄로 구분."""
    paragraphs = []
    current = []
    start_line = 1
    for i, line in enumerate(text.split('\n'), 1):
        if line.strip() == '':
            if current:
                paragraphs.append({
                    'text': '\n'.join(current),
                    'start_line': start_line,
                    'end_line': i - 1,
                })
                current = []
            start_line = i + 1
        else:
            if not current:
                start_line = i
            current.append(line)
    if current:
        paragraphs.append({
            'text': '\n'.join(current),
            'start_line': start_line,
            'end_line': start_line + len(current) - 1,
        })
    return paragraphs


def check_passive_paragraphs(text):
    """(a) 소극적 논증으로 끝나는 문단을 탐지한다.

    validate_legal_doc.py는 줄 단위로 탐지하지만,
    이 스크립트는 문단 단위로 검사한다.
    문단의 마지막 문장이 소극적 논증으로 끝나고,
    그 문단 내에 적극적 논증(왜 충족되지 않는지)이 없는 경우를 탐지.
    """
    failures = []
    paragraphs = split_paragraphs(text)

    passive_endings = [
        r'제시하지\s*못하였습니다[.\s]*$',
        r'밝히지\s*않았습니다[.\s]*$',
        r'제시하지\s*못한\s*것입니다[.\s]*$',
        r'입증하지\s*못하였습니다[.\s]*$',
        r'설명하지\s*않았습니다[.\s]*$',
        r'소명하지\s*못하였습니다[.\s]*$',
    ]

    active_indicators = [
        r'왜냐하면',
        r'그\s*이유는',
        r'구체적으로(?!\s*\n?\s*(?:제시|밝히|입증|소명|설명)\s*(?:하지|못))',
        r'청구인이\s*요청한\s*정보는',
        r'청구\s*대상.*성격',
        r'비식별',
        r'제외되어\s*있',
        r'절차의\s*외형',
        r'범주적\s*정보',
    ]

    for para in paragraphs:
        last_line = para['text'].rstrip().split('\n')[-1].strip()

        is_passive = False
        matched_pattern = ''
        for pattern in passive_endings:
            if re.search(pattern, last_line):
                is_passive = True
                matched_pattern = re.search(pattern, last_line).group()
                break

        if not is_passive:
            continue

        has_active = any(
            re.search(p, para['text']) for p in active_indicators
        )

        if not has_active:
            failures.append({
                'rule': 'PASSIVE_PARAGRAPH',
                'start_line': para['start_line'],
                'end_line': para['end_line'],
                'match': matched_pattern,
                'context': last_line[:100],
                'fix': (
                    f"L{para['start_line']}~{para['end_line']} 문단이 "
                    f"'{matched_pattern}'로 끝나며, 문단 내에 적극적 논증이 없습니다. "
                    "왜 해당 요건이 충족되지 않는지를 정보의 성격, 비식별 범위 등에 "
                    "기반하여 직접 논증으로 보강하십시오."
                ),
            })

    return failures


def check_irac_completeness(text):
    """(b) IRAC 3단 구조 불완전을 탐지한다.

    Ⅰ./Ⅱ./Ⅲ. 수준의 섹션에서:
    - 법리/판례 인용(대전제)은 있으나
    - 사안 적용(소전제: "이 사건", "본 사안", "청구인이 요청한")이 없거나
    - 결론("따라서", "위법합니다", "해당하지 않습니다")이 없는 경우
    """
    failures = []

    section_pattern = re.compile(r'^(Ⅰ|Ⅱ|Ⅲ|Ⅳ|Ⅴ|Ⅵ)\.\s+(.+)$', re.MULTILINE)
    sections = []
    matches = list(section_pattern.finditer(text))

    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        section_text = text[start:end]
        section_title = m.group(2).strip()
        line_num = text[:start].count('\n') + 1
        sections.append({
            'title': section_title,
            'text': section_text,
            'line': line_num,
        })

    for sec in sections:
        has_premise = bool(re.search(
            r'(판시|판결|선고|제\d+조|제\d+항)', sec['text']
        ))
        if not has_premise:
            continue

        has_application = bool(re.search(
            r'(이\s*사건|본\s*사안|청구인이\s*요청|청구\s*대상|피청구인은\s*답변서)',
            sec['text']
        ))

        has_conclusion = bool(re.search(
            r'(따라서|위법합니다|위반됩니다|해당하지\s*않습니다|인정되지\s*않습니다|'
            r'할\s*수\s*없습니다|보기\s*어렵습니다|'
            r'수용합니다|나뉩니다|살핍니다|검토합니다|처분에\s*해당합니다|'
            r'주시기\s*바랍니다|동의합니다|무방합니다|안내하여)',
            sec['text']
        ))

        if has_premise and not has_application:
            failures.append({
                'rule': 'IRAC_NO_APPLICATION',
                'line': sec['line'],
                'match': sec['title'],
                'context': f"섹션 '{sec['title']}'",
                'fix': (
                    f"L{sec['line']} 섹션 '{sec['title']}'에 법리(대전제)는 "
                    "있으나 이 사건에의 적용(소전제)이 누락되었습니다. "
                    "\"이 사건에서 ~ \" 등으로 사안에 적용하십시오."
                ),
            })

        if has_application and not has_conclusion:
            failures.append({
                'rule': 'IRAC_INCOMPLETE',
                'line': sec['line'],
                'match': sec['title'],
                'context': f"섹션 '{sec['title']}'",
                'fix': (
                    f"L{sec['line']} 섹션 '{sec['title']}'에 법리(대전제)와 "
                    "사안 적용(소전제)은 있으나 결론이 누락되었습니다. "
                    "\"따라서 ~ 위법합니다\" 등의 결론을 추가하십시오."
                ),
            })

    return failures


def check_case_fact_comparison(text):
    """(c) 판례 인용 시 사실관계 비교 누락을 탐지한다.

    정식 인용("대법원 YYYY. M. D. 선고 XXXX두YYYY 판결") 후
    15줄 이내에 사실관계 비교 표현이 없는 경우를 탐지.

    단, 괄호 부기("~ 판결 참조") 형태의 약식 인용은 제외.
    간접 인용("~는 취지로 판시하였습니다")도 비교 대상에서 제외.
    """
    failures = []
    lines = text.split('\n')

    formal_cite = re.compile(
        r'(?:대법원|서울행정법원|대구지방법원|의정부지방법원|[가-힣]+법원)\s*'
        r'\d{4}\.\s*\d{1,2}\.\s*\d{1,2}\.\s*선고\s*'
        r'(\d{4}[가-힣]+\d+)\s*판결'
    )

    paren_cite = re.compile(r'\(.*판결\s*참조\)')

    fact_comparison = re.compile(
        r'(사실관계|이\s*사건에서|이\s*사건의\s*경우|본\s*사안에서|'
        r'본\s*사안의\s*경우|이\s*사건.*비교|위\s*판결.*사안|'
        r'위\s*판결의\s*사안|해당\s*판결.*달리|구별됩니다|'
        r'사안이\s*다르|사안을\s*달리)'
    )

    for i, line in enumerate(lines):
        for m in formal_cite.finditer(line):
            if paren_cite.search(line):
                continue

            case_num = m.group(1)
            search_end = min(i + 15, len(lines))
            subsequent = '\n'.join(lines[i:search_end])

            if fact_comparison.search(subsequent):
                continue

            if re.search(r'취지로\s*판시', line) or re.search(r'판시한\s*바\s*있', line):
                continue

            failures.append({
                'rule': 'CASE_FACT_MISSING',
                'line': i + 1,
                'match': case_num,
                'context': line.strip()[:100],
                'fix': (
                    f"L{i + 1} 판례 {case_num} 인용 후 15줄 이내에 "
                    "이 사건 사실관계와의 비교가 없습니다. "
                    "해당 판결의 사실관계를 파악한 위에서, 이 사건과 "
                    "어떻게 비교되는지를 밝히십시오."
                ),
            })

    return failures


def check_first_cite_has_quote(text):
    """(d) 판례 첫 인용 시 판시사항 원문 기재 누락을 탐지한다.

    각 판례의 첫 정식 인용에서 10줄 이내에
    직접 인용("~"라고 판시) 또는 간접 인용(~는 취지로 판시)이
    없으면 축약 인용으로 판단하여 경고한다.
    동일 사건번호의 후속 인용은 검사하지 않는다.
    """
    failures = []
    lines = text.split('\n')

    formal_cite = re.compile(
        r'(?:대법원|서울행정법원|대구지방법원|의정부지방법원|[가-힣]+법원)\s*'
        r'\d{4}\.\s*\d{1,2}\.\s*\d{1,2}\.\s*선고\s*'
        r'(\d{4}[가-힣]+\d+)\s*판결'
    )

    paren_cite = re.compile(r'\(.*판결\s*참조\)')

    quote_indicator = re.compile(
        r'(판시하였습니다|판시한\s*바|판단하였습니다|'
        r'판시하였고|[이라]고\s*보았습니다|"[^"]{10,}")'
    )

    seen_cases = set()

    for i, line in enumerate(lines):
        for m in formal_cite.finditer(line):
            if paren_cite.search(line):
                continue

            case_num = m.group(1)
            if case_num in seen_cases:
                continue
            seen_cases.add(case_num)

            search_end = min(i + 10, len(lines))
            subsequent = '\n'.join(lines[i:search_end])

            if quote_indicator.search(subsequent):
                continue

            failures.append({
                'rule': 'FIRST_CITE_NO_QUOTE',
                'line': i + 1,
                'match': case_num,
                'context': line.strip()[:100],
                'fix': (
                    f"L{i + 1} 판례 {case_num}의 첫 인용에서 판시사항 원문이 "
                    "기재되지 않았습니다. 첫 인용 시에는 직접 인용(\"~\"라고 "
                    "판시하였습니다) 또는 간접 인용(~는 취지로 판시하였습니다) "
                    "형태로 판시사항 원문을 밝히십시오."
                ),
            })

    return failures


def validate(text, filename=""):
    failures = []
    failures.extend(check_passive_paragraphs(text))
    failures.extend(check_irac_completeness(text))
    failures.extend(check_case_fact_comparison(text))
    failures.extend(check_first_cite_has_quote(text))
    return {
        'pass': len(failures) == 0,
        'failure_count': len(failures),
        'failures': failures,
        'file': filename,
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

    result = validate(text, file_path)

    all_failures = list(result['failures'])

    try:
        from validate_graph import parse_graph_json, find_graph_file, \
            load_doctrine_codes, validate as validate_graph
        graph_path = find_graph_file(file_path)
        if graph_path:
            graph_data = parse_graph_json(graph_path)
            if graph_data is not None:
                harness_dir = os.path.join(project_dir, 'harness')
                valid_codes = load_doctrine_codes(harness_dir)
                graph_result = validate_graph(
                    graph_data, text, valid_codes, graph_path, file_path
                )
                all_failures.extend(graph_result['failures'])
    except ImportError:
        pass

    if all_failures:
        report_lines = [
            f"[논증 품질 검증] {len(all_failures)}건 발견 "
            f"({os.path.basename(file_path)}):"
        ]
        for f in all_failures:
            report_lines.append(f"  {f['fix']}")

        print('\n'.join(report_lines), file=sys.stderr)
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
