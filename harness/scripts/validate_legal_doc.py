#!/usr/bin/env python3
"""Layer 1: 법률 문서 패턴 검증 스크립트.

PostToolUse hook (Write|Edit) 으로 자동 실행.
금지 표현, 형식 규칙, AI 작문 특징, 비표준 용어를 탐지하여
stderr로 결과를 출력한다. 모델이 이 피드백을 보고 수정할 수 있다.

사용법:
  python validate_legal_doc.py <file_path>
  echo '{"tool_input":{"file_path":"..."}}' | python validate_legal_doc.py
"""

import sys
import json
import re
import os

# ── 금지 표현 ──────────────────────────────────────────────

BANNED_EXPRESSIONS = [
    # AI 작문 특징 (§14)
    (r'—', 'AI_EM_DASH', 'em dash 사용 금지. 괄호 또는 제목 분리로 대체'),
    (r'그 이유는 다음과 같습니다', 'AI_INTRO', '삭제 후 바로 논증 시작'),
    (r'다음과 같은 이유에서입니다', 'AI_INTRO', '삭제 후 바로 논증 시작'),

    # 절대적·과장적 표현 (§14)
    (r'전혀\s', 'ABSOLUTE', '"전혀" 삭제 또는 완화'),
    (r'불가능합니다', 'ABSOLUTE', '"있다고 보기 어렵습니다"로 대체'),
    (r'결정적', 'ABSOLUTE', '삭제 또는 완화'),
    (r'자명합니다', 'ABSOLUTE', '완화된 표현으로 대체'),

    # 공격적 표현 (§14)
    (r'정확히 읽지 않은', 'AGGRESSIVE', '삭제, 사실 논증으로 대체'),
    (r'제대로 파악하지', 'AGGRESSIVE', '삭제, 사실 논증으로 대체'),
    (r'이해하지 못한 것으로 보입니다', 'AGGRESSIVE', '삭제'),

    # 추정적·유보적 표현 (§14, §7-7)
    (r'대체로\s', 'TENTATIVE', '"대체로" 삭제'),
    (r'대략\s', 'TENTATIVE', '"대략" 삭제'),
    (r'어느 정도', 'TENTATIVE', '"어느 정도" 삭제'),
    (r'일종의\s', 'TENTATIVE', '"일종의" 삭제'),

    # 비표준 용어 (§15)
    (r'가림\s*처리', 'NONSTANDARD', '"비공개 처리" 또는 "마스킹"으로 대체'),
    (r'범주화', 'NONSTANDARD', '"분류별 공개" 또는 "범위별 공개"로 대체'),
    (r'구간화', 'NONSTANDARD', '"금액 구간 표시" 또는 "범위별 공개"로 대체'),
    (r'재결례', 'NONSTANDARD', '"재결 제○○호"로 대체'),

    # 사실관계 오류 (feedback)
    (r'국립대학법인', 'FACT_ERROR',
     '경북대학교는 국립대학법인이 아님. "경북대학교" 또는 "국립대학인 경북대학교"로 대체'),
    (r"이하\s*'경북대학교'라\s*합니다", 'UNNECESSARY_ABBR',
     '경북대학교는 약칭 도입 불필요. 처음부터 "경북대학교"로 사용'),

    # 청구 범위 자발적 제약 (§7-6, feedback)
    (r'에\s*한정되어\s*있습니다', 'SCOPE_RESTRICT', '"한정" 사용 금지. "~ 등"으로 예시'),
    (r'에\s*불과합니다', 'SCOPE_RESTRICT', '"불과" 사용 금지'),
    (r'에\s*그치는', 'SCOPE_RESTRICT', '"그치는" 사용 금지'),
    (r'에\s*그칩니다', 'SCOPE_RESTRICT', '"그칩니다" 사용 금지'),
    (r'뿐입니다', 'SCOPE_RESTRICT', '"뿐입니다" 사용 금지. 범위를 좁히는 표현 회피'),
]

# ── 소극적 논증 탐지 (§7-1, §11) ────────────────────────────

PASSIVE_ARGUMENT = [
    (r'제시하지\s*못하였습니다[.\s]*$', 'PASSIVE_ARG',
     '소극적 논증. 왜 해당 요건이 충족되지 않는지 직접 논증으로 보강'),
    (r'밝히지\s*않았습니다[.\s]*$', 'PASSIVE_ARG',
     '소극적 논증. 왜 해당 요건이 충족되지 않는지 직접 논증으로 보강'),
    (r'제시하지\s*못한\s*것입니다[.\s]*$', 'PASSIVE_ARG',
     '소극적 논증. 직접 논증으로 보강'),
    (r'입증하지\s*못하였습니다[.\s]*$', 'PASSIVE_ARG',
     '소극적 논증. 왜 입증이 불충분한지 구체적 근거 제시'),
]

# ── 역이용 위험 패턴 (§7-3) ──────────────────────────────────

REVERSE_EXPLOIT = [
    (r'이미\s*공개된.*동일한\s*수준', 'REVERSE_EXPLOIT',
     '역이용 위험. "동일한 수준이면 청구 실익 없다"는 반론 가능. §7-3 참조'),
    (r'이미\s*공개된.*같은\s*수준', 'REVERSE_EXPLOIT',
     '역이용 위험. 선행 공개와 이 사건 청구 대상이 다른 정보임을 전제할 것'),
]

# ── 형식 규칙 ──────────────────────────────────────────────

FORMAT_RULES = [
    # 편의적 약식 번호 — (1) (가) 대신 1) 가) 사용 금지 (§10)
    (r'(?<!\()\b(\d)\)\s', 'NUM_FORMAT',
     '편의적 번호 "N)" 사용 금지. "(N)" 양쪽 괄호 필수'),
    (r'(?<!\()(?<![가-힣])([가나다라마바사아자차카타파하])\)\s', 'NUM_FORMAT',
     '편의적 번호 "가)" 사용 금지. "(가)" 양쪽 괄호 필수'),

    # 재결 인용에서 "재결례" 사용 (§10)
    (r'재결례\s*제', 'CITATION_FORMAT',
     '"재결례 제~" → "재결 제~호"'),
]

# ── 문서 머리 형식 규칙 (§14 표현사전) ────────────────────

HEADER_PATTERNS = [
    (r'^(사\s*건|청\s*구\s*인|피청구인)\s*:', 'HEADER_COLON',
     '콜론(:) 사용 금지. 항목명과 값 사이는 공백 2칸'),
    (r'(서명\s*또는\s*인)', 'SIGNATURE_FORMAT',
     '"(서명 또는 인)" 사용 금지. "(인)"만 표기'),
]

# ── 조문·판례 출처 혼동 패턴 (§13) ────────────────────────

SOURCE_CONFUSION = [
    (r'정보공개법이\s*요구하는\s*고도의\s*개연성', 'SOURCE_CONFUSION',
     '"고도의 개연성"은 판례 용어. "정보공개법" 대신 판례를 주어로'),
    (r'정보공개법\s*제\d+조.*고도의\s*개연성', 'SOURCE_CONFUSION',
     '"고도의 개연성"은 조문 문언이 아니라 판례 해석 기준'),
    (r'정보공개법.*비교\s*[·ㆍ]\s*교량', 'SOURCE_CONFUSION',
     '"비교교량"은 판례 용어. 조문에 귀속시키지 않음'),
]


# ── 판례 용어 선행 인용 미검증 (§8, §13) ────────────────────

CASE_TERMS = {
    '고도의 개연성': r'고도의\s*개연성',
    '비교교량': r'비교\s*[·ㆍ]\s*교량',
    '객관적으로 현저하게': r'객관적으로\s*현저하게',
}

FORMAL_CITE_PATTERN = re.compile(
    r'(?:대법원|서울행정법원|대구지방법원|[가-힣]+법원)\s*'
    r'\d{4}\.\s*\d{1,2}\.\s*\d{1,2}\.\s*선고\s*'
    r'\d{4}[가-힣]+\d+\s*판결'
)


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


def validate(text, filename=""):
    """텍스트에 대해 모든 패턴 검증을 실행하고 결과를 반환한다."""
    failures = []
    lines = text.split('\n')

    for i, line in enumerate(lines, 1):
        # 금지 표현
        for pattern, rule, fix in BANNED_EXPRESSIONS:
            for m in re.finditer(pattern, line):
                failures.append({
                    "rule": rule,
                    "line": i,
                    "match": m.group(),
                    "context": line.strip()[:100],
                    "fix": fix,
                })

        # 형식 규칙
        for pattern, rule, fix in FORMAT_RULES:
            for m in re.finditer(pattern, line):
                failures.append({
                    "rule": rule,
                    "line": i,
                    "match": m.group(),
                    "context": line.strip()[:100],
                    "fix": fix,
                })

        # 소극적 논증 (문단 마지막 문장 탐지)
        for pattern, rule, fix in PASSIVE_ARGUMENT:
            for m in re.finditer(pattern, line):
                failures.append({
                    "rule": rule,
                    "line": i,
                    "match": m.group().strip(),
                    "context": line.strip()[:100],
                    "fix": fix,
                })

        # 역이용 위험
        for pattern, rule, fix in REVERSE_EXPLOIT:
            for m in re.finditer(pattern, line):
                failures.append({
                    "rule": rule,
                    "line": i,
                    "match": m.group(),
                    "context": line.strip()[:100],
                    "fix": fix,
                })

        # 조문·판례 출처 혼동
        for pattern, rule, fix in SOURCE_CONFUSION:
            for m in re.finditer(pattern, line):
                failures.append({
                    "rule": rule,
                    "line": i,
                    "match": m.group(),
                    "context": line.strip()[:100],
                    "fix": fix,
                })

    # 문서 머리 형식 (첫 30줄)
    for i, line in enumerate(lines[:30], 1):
        for pattern, rule, fix in HEADER_PATTERNS:
            for m in re.finditer(pattern, line):
                failures.append({
                    "rule": rule,
                    "line": i,
                    "match": m.group(),
                    "context": line.strip()[:100],
                    "fix": fix,
                })

    # 최상위 번호 체계 검증: Ⅰ/Ⅱ/Ⅲ 없이 1./2./3.만 사용하는 문서
    has_roman_top = any(re.match(r'^(Ⅰ|Ⅱ|Ⅲ|Ⅳ|Ⅴ|Ⅵ|Ⅶ|Ⅷ)\.\s', line) for line in lines)
    has_arabic_top = any(re.match(r'^\d+\.\s', line) for line in lines)
    if not has_roman_top and has_arabic_top:
        failures.append({
            "rule": "NUM_HIERARCHY",
            "line": 1,
            "match": "1.",
            "context": "최상위 번호가 아라비아 숫자(1./2./3.)로 시작",
            "fix": "번호 체계를 Ⅰ→1→가→(1)→(가)로 통일. 최상위 섹션에 Ⅰ./Ⅱ./Ⅲ. 사용 필수",
        })

    # 판례 용어 선행 인용 미검증 (§8)
    preceding_text = ''
    for i, line in enumerate(lines, 1):
        for term_name, term_pattern in CASE_TERMS.items():
            if re.search(term_pattern, line):
                if re.search(r'판례에\s*의하면|위\s*판결|판시', line):
                    continue
                if not FORMAL_CITE_PATTERN.search(preceding_text):
                    failures.append({
                        "rule": "CASE_TERM_BEFORE_CITE",
                        "line": i,
                        "match": term_name,
                        "context": line.strip()[:100],
                        "fix": f'"{term_name}"은 판례 용어. 해당 판례를 먼저 인용한 뒤 사용하거나, 조문 문언으로 대체',
                    })
        preceding_text += line + '\n'

    return {
        "pass": len(failures) == 0,
        "failure_count": len(failures),
        "failures": failures,
        "file": filename,
    }


def resolve_file_path():
    """커맨드라인 인수 또는 stdin에서 파일 경로를 가져온다."""
    if len(sys.argv) > 1:
        return sys.argv[1]

    try:
        stdin_data = sys.stdin.read()
        if stdin_data.strip():
            data = json.loads(stdin_data)
            return data.get("tool_input", {}).get("file_path", "")
    except (json.JSONDecodeError, KeyError):
        pass

    return None


def main():
    file_path = resolve_file_path()
    if not file_path:
        sys.exit(0)

    # 법률 문서 텍스트 파일만 검증 (비텍스트 파일은 건너뜀)
    _, ext = os.path.splitext(file_path)
    if ext.lower() not in ('.txt', '.md', '.typ'):
        sys.exit(0)

    # harness/ 내부 파일은 검증 대상이 아님
    normalized = os.path.normpath(file_path).replace('\\', '/')
    if '/harness/' in normalized or '/.claude/' in normalized:
        sys.exit(0)

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            text = f.read()
    except (FileNotFoundError, PermissionError, UnicodeDecodeError):
        sys.exit(0)

    if not is_legal_document(text):
        sys.exit(0)

    result = validate(text, file_path)

    if not result["pass"]:
        report_lines = [
            f"[패턴 검증] {result['failure_count']}건 위반 발견 ({os.path.basename(file_path)}):"
        ]
        for f in result["failures"]:
            report_lines.append(
                f"  L{f['line']}: [{f['rule']}] \"{f['match']}\" → {f['fix']}"
            )
        print('\n'.join(report_lines), file=sys.stderr)

    sys.exit(0)


if __name__ == "__main__":
    main()
