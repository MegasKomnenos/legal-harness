#!/usr/bin/env python3
"""법률 문서 .txt → PDF 변환 (WeasyPrint)

Usage:
    python3 generate_pdf.py <입력.txt> [출력.pdf]
    python3 generate_pdf.py <입력.txt> --html   # 중간 HTML 확인

입력 파일 규약:
  - 첫 비공란 행: 문서 제목 (18pt Bold 중앙정렬)
  - "사 건"/"청 구 인"/"피청구인" 시작 행: 머리글 필드
  - Ⅰ./1./가./(1)/(가)/① 시작 행: 각 수준 제목
  - 나머지: 본문 단락 (빈 줄로 구분)
  - 말미 YYYY. M. D. + 서명 + 귀중/귀하: 서명·수신

서식: harness/quality/PDF_서식.md 확정 사양
엔진: WeasyPrint 66+, Noto Serif CJK KR
"""
import sys
import re
import html as _h
from pathlib import Path

# ── CSS: quality/PDF_서식.md 확정 사양 ─────────────────────
CSS = """\
@page {
  size: A4;
  margin: 25mm 25mm 15mm 25mm;
  @bottom-center {
    content: "- " counter(page) " -";
    font-family: "Noto Serif CJK KR", serif;
    font-size: 9pt;
  }
}
body {
  font-family: "Noto Serif CJK KR", serif;
  font-size: 11pt;
  line-height: 2.0;
  margin: 0;
  padding: 0;
}
h1 {
  font-size: 18pt;
  font-weight: bold;
  text-align: center;
  line-height: 2.0;
  margin: 0 0 22pt 0;
  page-break-after: avoid;
}
h2 {
  font-size: 13pt;
  font-weight: bold;
  line-height: 2.0;
  margin: 22pt 0 0 0;
  page-break-after: avoid;
}
h3 {
  font-size: 11.5pt;
  font-weight: bold;
  line-height: 2.0;
  margin: 11pt 0 0 0;
  page-break-after: avoid;
}
p {
  margin: 0 0 11pt 0;
  text-indent: 0;
  line-height: 2.0;
  orphans: 2;
  widows: 2;
}
.sub {
  text-align: center;
  margin: 0 0 11pt 0;
  font-size: 11pt;
}
.i1 { margin-left: 10mm; }
.i2 { margin-left: 20mm; }
table.hdr {
  margin-bottom: 22pt;
  border-collapse: collapse;
}
table.hdr td {
  vertical-align: top;
  padding: 0;
  line-height: 2.0;
  font-size: 11pt;
}
table.hdr .l {
  width: 55mm;
  letter-spacing: 0.15em;
}
.foot {
  margin-top: 22pt;
  page-break-inside: avoid;
}
.foot p {
  text-align: right;
  margin: 0;
  line-height: 1.8;
}
.foot .addr {
  text-align: center;
  margin-top: 6pt;
}
"""

# ── regex ──────────────────────────────────────────────────
_RO = re.compile(r'^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]\.\s+')
_AR = re.compile(r'^(\d{1,2})\.\s+')
_GA = re.compile(r'^(가|나|다|라|마|바|사|아|자|차|카|타|파|하)\.\s+')
_PN = re.compile(r'^\(\d+\)\s+')
_PG = re.compile(r'^\((가|나|다|라|마|바|사|아|자|차|카|타|파|하)\)\s+')
_CI = re.compile(r'^[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]\s+')
_HD = re.compile(r'^(사\s*건|청\s*구\s*인|피\s*청\s*구\s*인)\s{2,}(.+)')
_DT = re.compile(r'^\d{4}\.\s*\d{1,2}\.\s*\d{1,2}\.\s*$')
_AD = re.compile(r'.+(귀중|귀하)\s*$')
_SG = re.compile(r'^(위\s+)?청\s*구\s*인')

SECT_TITLES = {
    '청 구 취 지', '청구취지',
    '청 구 이 유', '청구이유',
    '증 거 서 류', '증거서류',
}


def _tag(s, roman):
    """본문 행의 구조적 역할을 판별한다."""
    if s in SECT_TITLES:
        return 'h1'
    if _RO.match(s):
        return 'h2'
    if _PN.match(s) or _PG.match(s):
        return 'i1'
    if _CI.match(s):
        return 'i2'
    if _AR.match(s):
        return 'h3' if roman else 'h2'
    if _GA.match(s):
        return 'h3'
    return None


def parse(text):
    lines = text.split('\n')
    roman = any(_RO.match(l.strip()) for l in lines if l.strip())
    i, n = 0, len(lines)

    def skip():
        nonlocal i
        while i < n and not lines[i].strip():
            i += 1

    # ── 제목 ──
    skip()
    title = []
    while i < n:
        s = lines[i].strip()
        if not s or _HD.match(s):
            break
        title.append(s)
        i += 1
    title = ' '.join(title) or None
    skip()

    # ── 부제 (괄호로 시작하는 행) ──
    subtitle = None
    if i < n:
        s = lines[i].strip()
        if s.startswith('(') and not _HD.match(s):
            subtitle = s
            i += 1
            skip()

    # ── 머리글 필드 ──
    hdrs = []
    while i < n:
        s = lines[i].strip()
        m = _HD.match(s)
        if m:
            label = re.sub(r'\s+', ' ', m.group(1))
            hdrs.append((label, m.group(2).strip()))
            i += 1
        elif not s:
            i += 1
        else:
            break
    skip()

    # ── 꼬리 영역 (역방향 탐색, 최대 15행) ──
    fs = n
    for j in range(n - 1, max(i, n - 15) - 1, -1):
        if _AD.match(lines[j].strip()):
            fs = j
            k = j - 1
            while k >= i:
                s2 = lines[k].strip()
                if not s2:
                    k -= 1
                    continue
                if _DT.match(s2) or _SG.match(s2) or s2 == '(인)':
                    fs = k
                    k -= 1
                else:
                    break
            break

    # ── 본문 ──
    body, para = [], []
    in_evidence = False

    def flush():
        if para:
            body.append(('p', ' '.join(para)))
            para.clear()

    while i < fs:
        s = lines[i].strip()
        if not s:
            flush()
        else:
            t = _tag(s, roman)
            if t == 'h1' and ('증' in s and '서' in s):
                in_evidence = True
                flush()
                body.append((t, s))
            elif in_evidence and t in ('h2', 'h3'):
                flush()
                body.append(('p', s))
            elif t:
                in_evidence = False
                flush()
                body.append((t, s))
            else:
                para.append(s)
        i += 1
    flush()

    # ── 꼬리 파싱 ──
    foot = {'date': None, 'sig': [], 'addr': None}
    for ln in lines[fs:]:
        s = ln.strip()
        if not s:
            continue
        if _DT.match(s):
            foot['date'] = s
        elif _AD.match(s):
            foot['addr'] = s
        else:
            foot['sig'].append(s)

    return title, subtitle, hdrs, body, foot


def render(title, subtitle, hdrs, body, foot):
    e = _h.escape
    o = [
        '<!DOCTYPE html>',
        '<html lang="ko"><head><meta charset="utf-8">',
        f'<style>{CSS}</style>',
        '</head><body>',
    ]

    if title:
        o.append(f'<h1>{e(title)}</h1>')
    if subtitle:
        o.append(f'<p class="sub">{e(subtitle)}</p>')

    if hdrs:
        o.append('<table class="hdr">')
        for lb, v in hdrs:
            o.append(f'<tr><td class="l">{e(lb)}</td><td>{e(v)}</td></tr>')
        o.append('</table>')

    for tag, txt in body:
        et = e(txt)
        if tag == 'h1':
            o.append(f'<h1>{et}</h1>')
        elif tag == 'h2':
            o.append(f'<h2>{et}</h2>')
        elif tag == 'h3':
            o.append(f'<h3>{et}</h3>')
        elif tag == 'i1':
            o.append(f'<p class="i1">{et}</p>')
        elif tag == 'i2':
            o.append(f'<p class="i2">{et}</p>')
        else:
            o.append(f'<p>{et}</p>')

    if foot['date'] or foot['sig'] or foot['addr']:
        o.append('<div class="foot">')
        if foot['date']:
            o.append(f'<p>{e(foot["date"])}</p>')
        for s in foot['sig']:
            o.append(f'<p>{e(s)}</p>')
        if foot['addr']:
            o.append(f'<p class="addr">{e(foot["addr"])}</p>')
        o.append('</div>')

    o.append('</body></html>')
    return '\n'.join(o)


def main():
    if len(sys.argv) < 2:
        print(f'Usage: {sys.argv[0]} <입력.txt> [출력.pdf | --html]',
              file=sys.stderr)
        sys.exit(1)

    inp = Path(sys.argv[1])
    if not inp.exists():
        print(f'파일 없음: {inp}', file=sys.stderr)
        sys.exit(1)

    title, subtitle, hdrs, body, foot = parse(inp.read_text('utf-8'))
    html_str = render(title, subtitle, hdrs, body, foot)

    if '--html' in sys.argv:
        print(html_str)
        return

    out = None
    for a in sys.argv[2:]:
        if a != '--html':
            out = Path(a)
            break
    if out is None:
        out = inp.with_suffix('.pdf')

    from weasyprint import HTML
    HTML(string=html_str).write_pdf(str(out))
    print(f'PDF 생성: {out}')


if __name__ == '__main__':
    main()
