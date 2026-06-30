#!/usr/bin/env python3
"""하네스 정합성 self-test.

여러 문서·코드·데이터에 중복 기재된 정보가 서로 어긋나지 않는지 점검한다.
PostToolUse 훅이 아니라 단독 실행하며, 불일치가 있으면 목록을 출력하고 exit 1.

  python harness/scripts/validate_harness.py [project_dir]

KNOWN_GAPS: 사용자 결정으로 의도적으로 보류된 알려진 공백. 경보에서 제외한다.
공백이 해소되면(원문 확보, 사전 등록 등) 해당 항목을 KNOWN_GAPS에서 제거한다.
"""

import sys
import os
import re
import glob


KNOWN_GAPS = {
    # 인용사전·법리DB에 수록되었으나 전문 파일을 확보하지 못한 판례.
    # 2020두31415는 2020두31408과 병합된 부속 사건번호로, 대표번호(2020두31408)
    # 파일로 전문이 확보되므로 정상이다.
    'case_in_dict_no_file': {'2013누251', '2020두31415'},
    # 파일은 보유하나 인용사전에 미등록인 판례(참고용)
    'file_not_in_dict': {'2016다202947', '2021구합23550'},
    # 법조문 사전에 항목은 있으나 원문을 미확보한 법령
    'statute_text_missing': {'경북대학교 인권센터 규정'},
}

CASE_NUM_RE = re.compile(
    r'\d{4}(?:두|다|구합|구단|누|나|노|도|루|마|부|카|타|허|후|추|초|재|고합|고단)\d+'
)


def read(path):
    try:
        with open(path, encoding='utf-8') as f:
            return f.read()
    except (FileNotFoundError, UnicodeDecodeError):
        return ''


def check_phase2_parsing(proj):
    """검증_체크리스트 [Phase 2] 파싱이 비어있지 않고 §17·§18을 포함하는지."""
    issues = []
    sd = os.path.join(proj, 'harness', 'scripts')
    if sd not in sys.path:
        sys.path.insert(0, sd)
    try:
        import validate_doc_eval as ve
        crit = ve.load_phase2_criteria(os.path.join(proj, 'harness'))
        if not crit:
            issues.append('검증_체크리스트.md [Phase 2] 파싱 결과가 비어 있음 (절 제목·태그 형식 확인)')
        else:
            refs = [r for r, _, _ in crit]
            for must in ('§17', '§18'):
                if must not in refs:
                    issues.append(f'Phase 2 파싱에 {must} 누락')
    except Exception as e:
        issues.append(f'validate_doc_eval 로드/파싱 실패: {e}')
    return issues


def check_case_dict_vs_files(proj):
    """판례_인용_사전 ↔ 법령_판례/판례/ 파일 양방향 대조."""
    issues = []
    dict_cases = set(CASE_NUM_RE.findall(
        read(os.path.join(proj, 'harness', 'data', '판례_인용_사전.md'))))
    file_cases = set()
    for fp in glob.glob(os.path.join(proj, '법령_판례', '판례', '*.txt')):
        file_cases.update(CASE_NUM_RE.findall(os.path.basename(fp)))
    for c in sorted(dict_cases - file_cases):
        if c in KNOWN_GAPS['case_in_dict_no_file']:
            continue
        issues.append(f'판례 사전 수록 but 전문 파일 없음: {c}')
    for c in sorted(file_cases - dict_cases):
        if c in KNOWN_GAPS['file_not_in_dict']:
            continue
        issues.append(f'판례 파일 있으나 사전 미등록: {c}')
    return issues


def check_statute_missing(proj):
    """법조문_인용_사전의 '원문 미수록/미보유' 표시가 KNOWN_GAPS와 일치하는지."""
    issues = []
    text = read(os.path.join(proj, 'harness', 'data', '법조문_인용_사전.md'))
    for m in re.finditer(r'##\s+[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩa-zA-Z]+\.\s+(.+)', text):
        name = m.group(1).strip()
        seg = text[m.start():m.start() + 400]
        if '미수록' in seg or '미보유' in seg:
            if name not in KNOWN_GAPS['statute_text_missing']:
                issues.append(f'법조문 원문 미보유(미등록 공백): {name}')
    return issues


def check_doctrine_cases(proj):
    """법리DB가 인용하는 판례가 인용사전에 등재되어 있는지."""
    issues = []
    db_cases = set(CASE_NUM_RE.findall(
        read(os.path.join(proj, 'harness', 'data', '법리_데이터베이스.md'))))
    dict_cases = set(CASE_NUM_RE.findall(
        read(os.path.join(proj, 'harness', 'data', '판례_인용_사전.md'))))
    for c in sorted(db_cases - dict_cases):
        if c in KNOWN_GAPS['case_in_dict_no_file'] or c in KNOWN_GAPS['file_not_in_dict']:
            continue
        issues.append(f'법리DB 인용 판례가 인용사전에 미등재: {c}')
    return issues


def check_readme_harness_paths(proj):
    """README가 언급한 harness/ 하위 파일이 실재하는지."""
    issues = []
    for rm in ('README.md', 'README.en.md'):
        text = read(os.path.join(proj, rm))
        for m in re.finditer(r'`(data|style|quality|scripts)/([^`*]+?\.(?:md|py))`', text):
            rel = os.path.join('harness', m.group(1), m.group(2))
            if not os.path.exists(os.path.join(proj, rel)):
                issues.append(f'{rm}: 없는 harness 파일 참조: {rel}')
    return issues


def check_extract_skill(proj):
    """extract-documents SKILL의 OCR 엔진 기재가 extract_all.py와 일치하는지."""
    issues = []
    skill = read(os.path.join(proj, '.claude', 'skills', 'extract-documents', 'SKILL.md'))
    code = read(os.path.join(proj, 'extract_all.py'))
    if 'pytesseract' in code:
        if 'EasyOCR' in skill:
            issues.append('extract SKILL이 EasyOCR을 명시하나 코드는 pytesseract 사용')
        if 'no tesseract' in skill.lower():
            issues.append('extract SKILL "no tesseract" 기재가 코드(Tesseract 사용)와 모순')
    return issues


CHECKS = [
    ('Phase 2 파싱(§17·§18 포함)', check_phase2_parsing),
    ('판례 사전 ↔ 판례 파일', check_case_dict_vs_files),
    ('법조문 원문 미보유 ↔ KNOWN_GAPS', check_statute_missing),
    ('법리DB 판례 ↔ 인용사전', check_doctrine_cases),
    ('README harness 경로 실재', check_readme_harness_paths),
    ('extract SKILL OCR ↔ 코드', check_extract_skill),
]


def main():
    proj = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    total = 0
    for name, fn in CHECKS:
        try:
            issues = fn(proj)
        except Exception as e:
            issues = [f'점검 중 오류: {e}']
        if issues:
            print(f'[불일치] {name}:')
            for it in issues:
                print(f'  - {it}')
            total += len(issues)
        else:
            print(f'[정합] {name}')
    print()
    if total:
        print(f'총 {total}건의 정합성 불일치 발견 (KNOWN_GAPS 제외).')
        sys.exit(1)
    print('모든 정합성 점검 통과 (KNOWN_GAPS 보류분 제외).')
    sys.exit(0)


if __name__ == '__main__':
    main()
