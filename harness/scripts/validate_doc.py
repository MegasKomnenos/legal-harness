#!/usr/bin/env python3
"""Phase 1: 법률 문서 결정론적 검증.

regex 패턴 매칭과 인용 사전 대조만 수행한다.
문맥 판단이 필요한 검증(IRAC 완전성, 사실관계 비교 충분성,
논증 흐름 등)은 Phase 2(Agent)에서 수행한다.

PostToolUse hook (Write|Edit)으로 자동 실행.

사용법:
  python validate_doc.py <project_dir> [target_file]
"""

import sys
import os
import re
import json
import glob
import hashlib


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 공통
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

LEGAL_DOC_MARKERS = [
    r'보\s*충\s*서\s*면', r'청\s*구\s*이\s*유', r'별\s*지',
    r'법리보충\s*참고자료', r'요\s*약\s*본', r'청\s*구\s*취\s*지',
    r'피청구인', r'청\s*구\s*인', r'정보공개', r'행정심판',
]


def is_legal_document(text):
    count = sum(1 for p in LEGAL_DOC_MARKERS if re.search(p, text[:2000]))
    return count >= 2


def get_line_number(text, position):
    return text[:position].count('\n') + 1


def find_target_file(project_dir):
    candidates = []
    for ext in ('*.txt', '*.typ'):
        for fpath in glob.glob(os.path.join(project_dir, '**', ext), recursive=True):
            n = os.path.normpath(fpath).replace('\\', '/')
            if any(s in n for s in ['/harness/', '/output/', '/.claude/', '/scripts/']):
                continue
            try:
                candidates.append((os.path.getmtime(fpath), fpath))
            except OSError:
                continue
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# A. 패턴 검증
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

BANNED = [
    (r'—', 'em dash 사용 금지'),
    (r'그 이유는 다음과 같습니다', '삭제 후 바로 논증 시작'),
    (r'다음과 같은 이유에서입니다', '삭제 후 바로 논증 시작'),
    (r'전혀\s', '"전혀" 삭제 또는 완화'),
    (r'불가능합니다', '"있다고 보기 어렵습니다"로 대체'),
    (r'결정적', '삭제 또는 완화'),
    (r'자명합니다', '완화된 표현으로 대체'),
    (r'정확히 읽지 않은', '삭제, 사실 논증으로 대체'),
    (r'제대로 파악하지', '삭제, 사실 논증으로 대체'),
    (r'이해하지 못한 것으로 보입니다', '삭제'),
    (r'대체로\s', '"대체로" 삭제'),
    (r'대략\s', '"대략" 삭제'),
    (r'어느 정도', '"어느 정도" 삭제'),
    (r'일종의\s', '"일종의" 삭제'),
    (r'가림\s*처리', '"비공개 처리" 또는 "마스킹"으로 대체'),
    (r'범주화', '"분류별 공개"로 대체'),
    (r'구간화', '"금액 구간 표시"로 대체'),
    (r'재결례', '"재결 제○○호"로 대체'),
    (r'국립대학법인', '경북대학교는 국립대학법인이 아님'),
    (r"이하\s*'경북대학교'라\s*합니다", '약칭 도입 불필요'),
    (r'에\s*한정되어\s*있습니다', '"한정" 사용 금지'),
    (r'에\s*불과합니다', '"불과" 사용 금지'),
    (r'에\s*그치는', '"그치는" 사용 금지'),
    (r'에\s*그칩니다', '"그칩니다" 사용 금지'),
    (r'뿐입니다', '"뿐입니다" 사용 금지'),
    (r'이미\s*공개된.*동일한\s*수준', '역이용 위험'),
    (r'이미\s*공개된.*같은\s*수준', '역이용 위험'),
    (r'제시하지\s*못하였습니다\s*$', '소극적 논증'),
    (r'밝히지\s*않았습니다\s*$', '소극적 논증'),
    (r'입증하지\s*못하였습니다\s*$', '소극적 논증'),
    (r'정보공개법이\s*요구하는\s*고도의\s*개연성', '"고도의 개연성"은 판례 용어'),
    (r'정보공개법\s*제\d+조.*고도의\s*개연성', '"고도의 개연성"은 조문 문언이 아님'),
    (r'정보공개법.*비교\s*[·ㆍ]\s*교량', '"비교교량"은 판례 용어'),
]

FORMAT_RULES = [
    (r'(?<!\()\b(\d)\)\s', '편의적 번호 "N)" → "(N)"'),
    (r'(?<!\()(?<![가-힣])([가나다라마바사아자차카타파하])\)\s', '편의적 번호 "가)" → "(가)"'),
    (r'재결례\s*제', '"재결례 제~" → "재결 제~호"'),
]

HEADER_RULES = [
    (r'^(사\s*건|청\s*구\s*인|피청구인)\s*:', '콜론 사용 금지. 공백 2칸'),
    (r'(서명\s*또는\s*인)', '"(서명 또는 인)" → "(인)"만 표기'),
]

CASE_TERMS = {
    '고도의 개연성': r'고도의\s*개연성',
    '비교교량': r'비교\s*[·ㆍ]\s*교량',
    '객관적으로 현저하게': r'객관적으로\s*현저하게',
}

FORMAL_CITE_RE = re.compile(
    r'(?:대법원|서울행정법원|대구지방법원|[가-힣]+법원)\s*'
    r'\d{4}\.\s*\d{1,2}\.\s*\d{1,2}\.\s*선고\s*'
    r'\d{4}[가-힣]+\d+\s*판결'
)


def check_patterns(text):
    failures = []
    lines = text.split('\n')

    for i, line in enumerate(lines, 1):
        for pattern, fix in BANNED:
            if re.search(pattern, line):
                failures.append(f"  L{i}: {fix}")

        for pattern, fix in FORMAT_RULES:
            if re.search(pattern, line):
                failures.append(f"  L{i}: {fix}")

    for i, line in enumerate(lines[:30], 1):
        for pattern, fix in HEADER_RULES:
            if re.search(pattern, line):
                failures.append(f"  L{i}: {fix}")

    preceding = ''
    for i, line in enumerate(lines, 1):
        for term_name, term_pattern in CASE_TERMS.items():
            if re.search(term_pattern, line):
                if re.search(r'판례에\s*의하면|위\s*판결|판시', line):
                    continue
                if not FORMAL_CITE_RE.search(preceding):
                    failures.append(
                        f'  L{i}: "{term_name}" 사용 전 해당 판례 미인용'
                    )
        preceding += line + '\n'

    return failures


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# B. 인용 정합성
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def parse_case_dictionary(harness_dir):
    path = os.path.join(harness_dir, 'data/판례_인용_사전.md')
    cases = set()
    try:
        with open(path, 'r', encoding='utf-8') as f:
            text = f.read()
    except FileNotFoundError:
        return cases
    for m in re.finditer(r'###\s+P-\d+\s+.+?(\d{4}[가-힣]+\d+)', text):
        cases.add(m.group(1))
    return cases


def parse_statute_dictionary(harness_dir):
    path = os.path.join(harness_dir, 'data/법조문_인용_사전.md')
    keys = set()
    law_names = set()
    try:
        with open(path, 'r', encoding='utf-8') as f:
            text = f.read()
    except FileNotFoundError:
        return keys, law_names
    current_law = None
    for line in text.split('\n'):
        lm = re.match(r'^##\s+[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩa-zA-Z]+\.\s+(.+?)$', line)
        if lm:
            current_law = lm.group(1).strip()
            law_names.add(current_law)
            continue
        am = re.match(r'###\s+S-\d+\s+제(\d+)조', line)
        if am and current_law:
            keys.add((current_law, int(am.group(1))))
    return keys, law_names


def case_exists_on_disk(project_dir, case_num):
    law_dir = os.path.join(project_dir, '법령_판례')
    if not os.path.isdir(law_dir):
        return False
    for fp in glob.glob(os.path.join(law_dir, '**', f'*{case_num}*'), recursive=True):
        if os.path.isfile(fp):
            return True
    return False


def check_citations(text, project_dir):
    failures = []
    harness_dir = os.path.join(project_dir, 'harness')

    case_dict = parse_case_dictionary(harness_dir)
    cite_re = re.compile(
        r'(?:대법원|서울행정법원|대구지방법원|의정부지방법원|[가-힣]+법원)\s*'
        r'\d{4}\.\s*\d{1,2}\.\s*\d{1,2}\.\s*선고\s*'
        r'(\d{4}[가-힣]+\d+)\s*판결'
    )
    for m in cite_re.finditer(text):
        cn = m.group(1)
        if cn not in case_dict and not case_exists_on_disk(project_dir, cn):
            ln = get_line_number(text, m.start())
            surr = text[max(0, m.start()-200):m.start()+200]
            if '[미검증 판례]' in surr or '[미검증판례]' in surr:
                continue
            pre = text[max(0, m.start()-200):m.start()]
            if re.search(r'피청구인[은이가]?\s*.{0,60}원용', pre):
                continue
            failures.append(f"  L{ln}: 미수록 판례 {cn}")

    stat_keys, law_names = parse_statute_dictionary(harness_dir)
    if stat_keys:
        for m in re.finditer(r'「([^」]+)」[^제]*제(\d+)조', text):
            law = m.group(1).strip()
            art = int(m.group(2))
            if law in law_names and (law, art) not in stat_keys:
                ln = get_line_number(text, m.start())
                surr = text[max(0, m.start()-200):m.start()+200]
                if '[미검증 조문]' not in surr:
                    failures.append(f"  L{ln}: 미수록 조문 {law} 제{art}조")

    for m in re.finditer(r'재결\s+제(\S+)호', text):
        dn = m.group(1)
        if not re.match(r'\d{4}-\d+', dn):
            ln = get_line_number(text, m.start())
            failures.append(f"  L{ln}: 재결 호수 형식 불일치 '{dn}'")

    return failures


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 진입점
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def validate(text, file_path, project_dir="."):
    """Phase 1 전체 검증. 실패 메시지 리스트를 반환."""
    failures = []
    failures.extend(check_patterns(text))
    failures.extend(check_citations(text, project_dir))
    return failures


def main():
    target_from_stdin = None
    try:
        stdin_data = sys.stdin.read()
        if stdin_data.strip():
            data = json.loads(stdin_data)
            fp = data.get('tool_input', {}).get('file_path', '')
            _, ext = os.path.splitext(fp)
            n = os.path.normpath(fp).replace('\\', '/')
            if ext.lower() not in ('.txt', '.typ'):
                sys.exit(0)
            if any(s in n for s in ['/harness/', '/.claude/', '/scripts/']):
                sys.exit(0)
            target_from_stdin = fp
    except (json.JSONDecodeError, KeyError, TypeError):
        pass

    project_dir = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    target_file = sys.argv[2] if len(sys.argv) > 2 else None

    file_path = target_from_stdin or target_file or find_target_file(project_dir)
    if not file_path:
        sys.exit(0)

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            text = f.read()
    except (FileNotFoundError, PermissionError, UnicodeDecodeError):
        sys.exit(0)

    if not is_legal_document(text):
        sys.exit(0)

    failures = validate(text, file_path, project_dir)

    if failures:
        basename = os.path.basename(file_path)
        header = f"[Phase 1] {len(failures)}건 ({basename}):"
        print('\n'.join([header] + failures), file=sys.stderr)
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
