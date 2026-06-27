#!/usr/bin/env python3
"""그래프 내용 평가 스크립트 (Stop hook).

법리 그래프 파일의 JSON을 파싱하고, 01_법리_데이터베이스.md에서
각 법리의 연관 법리·적용 사례를 추출하여 독립 평가 프롬프트를
stderr에 출력한다. exit 2로 모델 재진입을 유발하면, 재진입한
모델이 신선한 컨텍스트에서 그래프의 법리 선택·포섭·전략을
독립적으로 평가한다.

그래프 파일에 '내용 평가: pass'가 있으면 이미 평가 완료로
간주하여 즉시 종료한다(토큰 소모 0).

사용법:
  python validate_graph_content.py <project_dir>
"""

import sys
import os
import re
import json
import glob


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


def parse_graph_json(graph_path):
    try:
        with open(graph_path, 'r', encoding='utf-8') as f:
            text = f.read()
    except (FileNotFoundError, UnicodeDecodeError):
        return None, None

    if '내용 평가' in text and 'pass' in text.split('내용 평가')[-1].split('\n')[0]:
        return None, 'already_passed'

    m = re.search(r'```json\s*\n(.*?)\n```', text, re.DOTALL)
    if not m:
        return None, None

    try:
        return json.loads(m.group(1)), None
    except json.JSONDecodeError:
        return None, None


def extract_doctrine_context(harness_dir, codes):
    db_path = os.path.join(harness_dir, '01_법리_데이터베이스.md')
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


def build_evaluation_prompt(graph_data, doctrine_ctx, graph_path):
    lines = []
    lines.append('[법리 그래프 내용 평가] 아래 그래프의 법리 선택·포섭·전략을 독립 평가하십시오.')
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
        lines.append(f"  {code} ({name})")
        lines.append(f"    연관 법리: {related}")
        lines.append(f"    적용 사례: {usage}")

    all_codes = set()
    for issue in graph_data.get('issues', []):
        for d in issue.get('doctrines', []):
            all_codes.add(d.get('code', ''))
            related_str = doctrine_ctx.get(d.get('code', ''), {}).get('related', '')
            if related_str:
                for r in re.findall(r'[A-Z]+-\d+(?:-[A-Z])?', related_str):
                    if r not in all_codes and r in doctrine_ctx:
                        pass

    lines.append('')
    lines.append('평가 기준 (미충족 시 구체적으로 지적):')
    lines.append('  (1) 각 쟁점의 주 법리가 해당 쟁점의 핵심 논점에 가장 적합한가')
    lines.append('  (2) 보강·기반 법리가 주 법리의 논증을 실질적으로 강화하는가')
    lines.append('  (3) 포섭이 사실관계를 정확히 반영하며 과소·과대 서술이 없는가')
    lines.append('  (4) 법리DB에서 이 사건에 적용 가능하나 그래프에 누락된 법리가 있는가')
    lines.append(f'     (연관 법리 필드를 참조하여 확인)')
    lines.append('  (5) 적용 사례 필드의 선례와 비교하여 이 사건에서의 적용이 적절한가')
    lines.append('')
    lines.append(f'평가 완료 후 {os.path.basename(graph_path)}의 검증 이력에')
    lines.append('"내용 평가: pass" 또는 "내용 평가: fail → [수정 내용]"을 기록하십시오.')

    return '\n'.join(lines)


def main():
    try:
        stdin_data = sys.stdin.read()
        if stdin_data.strip():
            data = json.loads(stdin_data)
            if data.get('stop_hook_active', False):
                sys.exit(0)
    except (json.JSONDecodeError, KeyError):
        pass

    project_dir = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()

    graph_path = find_recent_graph(project_dir)
    if not graph_path:
        sys.exit(0)

    graph_data, status = parse_graph_json(graph_path)
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

    prompt = build_evaluation_prompt(graph_data, doctrine_ctx, graph_path)

    print(prompt, file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
