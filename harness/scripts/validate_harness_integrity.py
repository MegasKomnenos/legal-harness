#!/usr/bin/env python3
"""하네스 정합성 자동 검증 스크립트.

10_유지보수_가이드.md §5의 5개 검증 항목을 자동화한다.
harness/ 파일 수정 후 수동 실행하여 정합성을 확인한다.

사용법:
  python validate_harness_integrity.py [harness_dir]

검증 항목:
  1. 수치 검증: README.md의 수치와 실제 파일 내용 대조
  2. 교차참조 검증: 파일 간 §번호 참조가 실제와 일치하는지
  3. 용어 검증: 04 §15 비표준 용어가 다른 파일에서 사용되지 않는지
  4. 번호 순차성: 각 파일의 ## N. 헤더가 순차적인지
"""

import sys
import os
import re
import json


def read_file(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    except (FileNotFoundError, UnicodeDecodeError):
        return None


def check_counts(harness_dir):
    """수치 검증: README.md의 수치와 실제 내용 대조."""
    failures = []
    readme_path = os.path.join(harness_dir, 'README.md')
    readme = read_file(readme_path)
    if not readme:
        failures.append("README.md를 읽을 수 없습니다")
        return failures

    db_path = os.path.join(harness_dir, '01_법리_데이터베이스.md')
    db = read_file(db_path)
    if db:
        doctrine_ids = re.findall(r'\*\*법리 ID\*\*:\s*([\w-]+)', db)
        actual_count = len(doctrine_ids)
        readme_match = re.search(r'(\d+)개 법리', readme)
        if readme_match:
            stated = int(readme_match.group(1))
            if stated != actual_count:
                failures.append(
                    f"README '법리 수': {stated}개 (실제: {actual_count}개)"
                )

    cite_path = os.path.join(harness_dir, '02_판례_인용_사전.md')
    cite = read_file(cite_path)
    if cite:
        case_ids = re.findall(r'###\s+P-\d+', cite)
        actual_count = len(case_ids)
        readme_match = re.search(r'(\d+)건 판례', readme)
        if readme_match:
            stated = int(readme_match.group(1))
            if stated != actual_count:
                failures.append(
                    f"README '판례 수': {stated}건 (실제: {actual_count}건)"
                )

    expr_path = os.path.join(harness_dir, '04_표현_사전.md')
    expr = read_file(expr_path)
    if expr:
        categories = re.findall(r'^## \d+\.', expr, re.MULTILINE)
        actual_count = len(categories)
        readme_match = re.search(r'(\d+)개 카테고리', readme)
        if readme_match:
            stated = int(readme_match.group(1))
            if stated != actual_count:
                failures.append(
                    f"README '카테고리 수': {stated}개 (실제: {actual_count}개)"
                )

    quality_path = os.path.join(harness_dir, '05_품질_기준_및_검증.md')
    quality = read_file(quality_path)
    if quality:
        checks = re.findall(r'^## \d+\.', quality, re.MULTILINE)
        actual_count = len(checks)
        readme_match = re.search(r'(\d+)항목', readme)
        if readme_match:
            stated = int(readme_match.group(1))
            if stated != actual_count:
                failures.append(
                    f"README '체크리스트 항목 수': {stated}항목 (실제: {actual_count}항목)"
                )

    return failures


def check_nonstandard_terms(harness_dir):
    """용어 검증: 04 §15 비표준 용어가 다른 파일에서 사용되지 않는지."""
    failures = []
    expr_path = os.path.join(harness_dir, '04_표현_사전.md')
    expr = read_file(expr_path)
    if not expr:
        return failures

    section15 = re.search(
        r'## 15\. 비표준 용어.*?\n(.*?)(?=\n## |\Z)',
        expr, re.DOTALL
    )
    if not section15:
        return failures

    nonstandard_terms = []
    for line in section15.group(1).split('\n'):
        m = re.match(r'\|\s*(.+?)\s*\|.*\|.*\|', line)
        if m:
            term = m.group(1).strip()
            if term and term != '비표준 (사용 금지)' and not term.startswith('-'):
                nonstandard_terms.append(term)

    md_files = [
        f for f in os.listdir(harness_dir)
        if f.endswith('.md') and f != '04_표현_사전.md'
    ]

    meta_context = re.compile(
        r'(금지|비표준|탐지|대체|사용하지|미사용|교정|표준|Layer|자동|패턴|검증|목록|예시|수정 전|핵심 판례)'
    )

    for term in nonstandard_terms:
        for md_file in md_files:
            content = read_file(os.path.join(harness_dir, md_file))
            if content and term in content:
                lines = content.split('\n')
                for i, line in enumerate(lines, 1):
                    if term in line:
                        if line.strip().startswith('|'):
                            continue
                        if line.strip().startswith('#'):
                            continue
                        if meta_context.search(line):
                            continue
                        failures.append(
                            f"{md_file} L{i}: 비표준 용어 '{term}' 사용"
                        )

    return failures


def check_sequential_numbers(harness_dir):
    """번호 순차성: ## N. 헤더가 순차적인지."""
    failures = []
    target_files = [
        '04_표현_사전.md',
        '05_품질_기준_및_검증.md',
        '07_논증_가이드라인.md',
    ]

    for fname in target_files:
        content = read_file(os.path.join(harness_dir, fname))
        if not content:
            continue

        headers = re.findall(r'^## (\d+)\.', content, re.MULTILINE)
        numbers = [int(h) for h in headers]

        for i, num in enumerate(numbers):
            expected = i + 1
            if num != expected:
                failures.append(
                    f"{fname}: ## {num}. (예상: {expected}.) 순차 불일치"
                )
                break

    return failures


def check_cross_references(harness_dir):
    """교차참조 검증: 주요 파일 간 §번호 참조가 존재하는지."""
    failures = []

    file07 = read_file(os.path.join(harness_dir, '07_논증_가이드라인.md'))
    file04 = read_file(os.path.join(harness_dir, '04_표현_사전.md'))

    if file07 and file04:
        ref = re.search(r'§(\d+)', file07)
        if ref:
            section_num = ref.group(1)
            if not re.search(rf'^## {section_num}\.', file04, re.MULTILINE):
                failures.append(
                    f"07 → 04: §{section_num} 참조하나 04에 해당 섹션 없음"
                )

    return failures


def main():
    tool_input = os.environ.get('TOOL_INPUT')
    if tool_input:
        try:
            data = json.loads(tool_input)
            file_path = data.get('file_path', '')
            if 'harness/' not in file_path:
                sys.exit(0)
        except (json.JSONDecodeError, KeyError, TypeError):
            sys.exit(0)

    harness_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.getcwd(), 'harness')

    if not os.path.isdir(harness_dir):
        print(f"harness 디렉토리를 찾을 수 없습니다: {harness_dir}", file=sys.stderr)
        sys.exit(1)

    all_failures = []

    print("[1/4] 수치 검증...", file=sys.stderr)
    all_failures.extend(check_counts(harness_dir))

    print("[2/4] 용어 검증...", file=sys.stderr)
    all_failures.extend(check_nonstandard_terms(harness_dir))

    print("[3/4] 번호 순차성 검증...", file=sys.stderr)
    all_failures.extend(check_sequential_numbers(harness_dir))

    print("[4/4] 교차참조 검증...", file=sys.stderr)
    all_failures.extend(check_cross_references(harness_dir))

    if all_failures:
        print(f"\n[정합성 검증] {len(all_failures)}건 불일치:", file=sys.stderr)
        for f in all_failures:
            print(f"  - {f}", file=sys.stderr)
        sys.exit(1)
    else:
        print("\n[정합성 검증] 전 항목 통과", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
