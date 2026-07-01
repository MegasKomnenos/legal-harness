# legal-case-harness

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Built with Claude Code](https://img.shields.io/badge/Built_with-Claude_Code-blueviolet)](https://claude.ai/claude-code)
[![한국어](https://img.shields.io/badge/lang-한국어-blue)](#)
[![English](https://img.shields.io/badge/lang-English-gray)](README.en.md)

한국 행정법 문서를 체계적으로 생성하는 Claude Code 참조 체계(harness)와 스킬.
실제 행정심판 3건에서 추출한 37개 법리, 판례·재결 27건 인용, 6종 문서 템플릿을 포함하며, 11단계 파이프라인과 2-Phase 자동 검증(PostToolUse 훅)을 거쳐 법률 문서를 생성한다.

## 개요

```mermaid
flowchart LR
    A["1. 참조 파일\n읽기"] --> B["2. 사건 문서\n통독"]
    B --> C["3. 판례 전문\n통독"]
    C --> D["4. 사안 파악"]
    D --> E["5. 쟁점 정리"]
    E --> F["6. 법리 연결\n그래프"]
    F --> G["7. 그래프\n검증 루프"]
    G --> H["8. 문서 생성"]
    H --> I["9. 2-Phase\n반복 검증"]
    I --> J["10. PDF 출력"]
    J --> K["11. 정리\n및 보관"]
    I -.->|미충족| H
    G -.->|미충족| F
```

## 핵심 구성요소

### `.claude/skills/legal-harness/` -- 법률 문서 생성 스킬

Claude Code에서 `/legal-harness [사건명] [문서유형]` 명령으로 법률 문서를 생성한다.

- 11단계 생성 파이프라인 (개요 참조)
- 전문 전체 적재 원칙: 모든 판례와 법령을 snippet이 아닌 전문으로 읽어 컨텍스트에 직접 적재
- 2-Phase 반복 품질 검증: 19항목 체크리스트(Phase 1 기계 검증 + Phase 2 서브에이전트 평가)를 전 항목 충족 시까지 반복

### `harness/` -- 법률 문서 생성 참조 체계

| 경로 | 내용 |
|------|------|
| `data/법리_데이터베이스.md` | 37개 법리 (ID, 판례, 판시사항 원문, 논증 구조, 적용 사례) |
| `data/판례_인용_사전.md` | 27건 판례·재결 인용 형식 및 판시사항 원문 (판례 26·재결 1) |
| `data/법조문_인용_사전.md` | 법 조문 원문 (인용 시 대조용) |
| `style/문서_구조_템플릿.md` | 6종 문서 유형별 목차, 번호 체계, 섹션 규칙 |
| `style/표현_사전.md` | 16개 카테고리 정형 표현 패턴 |
| `style/논증_가이드라인.md` | 논증상 오류 예시 및 교정 방법 |
| `quality/검증_체크리스트.md` | 19항목 품질 체크리스트 (Phase 1/2 구분) |
| `quality/PDF_서식.md` | PDF 출력 확정 사양 (A4, Noto Serif CJK KR) |
| `scripts/validate_*.py` | 검증 스크립트 (단계 게이트, 그래프·문서 Phase 1/2) |
| `scripts/generate_pdf.py` | txt→PDF 변환 (WeasyPrint→Playwright 폴백) |
| `디렉토리_지도.md` | 디렉토리 전체 구조 및 사건별 문서 분류 |
| `관리_절차.md` | 판례·case 추가 및 하네스 수정 절차 |

### `.claude/skills/extract-documents/` -- 문서 추출 스킬

PDF, HWP, DOCX, 이미지, 오디오, 영상 등 모든 문서 형식에서 텍스트를 추출한다.

- 7가지 추출 방법: 직접 텍스트 읽기, PDF 텍스트 레이어, OCR, HWP 변환, DOCX XML 파싱, STT, 영상 프레임 OCR
- PDF 페이지별 판정 로직으로 텍스트/이미지/벡터 페이지를 자동 분류

### `.claude/skills/memory-harness/` -- 작업 기억 하네스

git 커밋마다 곧 휘발될 작업 맥락을 결정화한 메모리 노드를 같은 커밋에 생성하고, dream 절차로 누적된 메모리를 종합하여 통찰과 관례로 정제한다.

- 2층 그래프: layer-1 메모리 노드(작업 커밋당 1개) + layer-2 dream 노드(종합 통찰)
- 헌법/세칙 2층 아키텍처: 핵심층(최소 계약)과 가변층(dream이 자율 진화)

## 디렉토리 구조

```
legal-case-harness/
├── .claude/                    # Claude Code 설정 및 스킬
│   ├── CLAUDE.md
│   ├── settings.json
│   └── skills/
│       ├── extract-documents/  # 문서 추출 스킬
│       ├── legal-harness/      # 법률 문서 생성 스킬
│       └── memory-harness/     # 작업 기억·dream 스킬
├── harness/                    # 법률 문서 생성 참조 체계
├── extract_all.py              # 문서 추출 스크립트
├── cases/                      # 사건별 문서 (익명화)
│   ├── 01_공사소음/            # 공사 소음 정보공개 사건
│   ├── 02_대동제/              # 대동제 용역 정보공개 사건
│   └── 03_성희롱/              # 성희롱 사건
├── 법령_판례/                  # 법률, 판례, 내규 (공개 자료)
│   ├── 법률_시행령/
│   ├── 별표_고시/
│   ├── 판례/
│   ├── 재결례/
│   ├── 경북대_내규/
│   └── 기타_참고/
└── legal-workbench/            # 웹 UI (서버·클라이언트)
```

## 사건 개요

| 사건 | 유형 | 피청구기관 | 상태 |
|------|------|-----------|------|
| 공사소음 | 공사 소음/비산먼지 정보공개 | 경북대학교 시설과 | 행정심판 진행 중 |
| 대동제 | 행사 용역 정보공개 | 경북대학교 학생지원과 | 행정심판 진행 중 |
| 성희롱 | 단톡방 성희롱 정보공개 | 경북대학교 인권센터·학생지원과 | 행정심판 진행 중 |

모든 사건 문서는 개인정보가 제거된 익명화 버전이다.

## 법리 범위

| 카테고리 | 법리 수 | 예시 |
|----------|---------|------|
| 정보공개법 | 24개 | 부분공개 원칙, 비공개 사유(제1~7호, 제5호 하위 5개·제6호 하위 3개 포함), 이유제시, 부존재, 전자적 공개, 공개방법, 특정성, 입증책임, 공공기록물법 연계, 처분성, 권리남용 항변 제한 등 |
| 행정심판 | 5개 | 처분성, 부작위, 보완요청 법적 성격, 병합 심리, 처분사유 추가·변경 제한 |
| 민원·징계 | 5개 | 형사-행정 분리, 기속적 의무(재량수축), 피해자 보호, 학적 변동 차단, 학칙 개정 취지 |
| 환경법 | 3개 | 비산먼지 신고, 특정공사 사전신고, 발주처 책임 |

## 사용법

### Claude Code에서 사용

```
/legal-harness 공사소음 보충서면
/legal-harness 대동제 청구이유서
/legal-harness 성희롱 정보공개청구 별지
```

### 수동 참조

1. `style/문서_구조_템플릿.md`에서 문서 유형별 구조 확인
2. `data/법리_데이터베이스.md`에서 적용할 법리 선택
3. `data/판례_인용_사전.md`의 원문으로만 판례 인용
4. `style/표현_사전.md`의 정형 표현 사용
5. `quality/검증_체크리스트.md` 체크리스트로 검증

## 지원 문서 유형

| 유형 | 출력 형태 |
|------|----------|
| 국민신문고 민원 | 텍스트 |
| 정보공개청구서 본문 | 텍스트 |
| 정보공개청구 별지 | PDF |
| 법리보충 참고자료 | PDF |
| 정보공개청구 요약본 | PDF |
| 행정심판 청구이유서 | PDF |
| 보충서면 | PDF |

> 목차 템플릿(`style/문서_구조_템플릿.md`)은 6종이며, '정보공개청구서 본문'은 별도 목차 템플릿 없이 `style/표현_사전.md` 기반으로 생성되므로 지원 유형은 7종이다.

## 문서 추출 도구

`extract_all.py`는 지정된 파일에서 텍스트를 추출하여 stdout에 출력한다.

```bash
python extract_all.py "법령_판례/archive/판례/대법원_2003두8050.pdf"
python extract_all.py "법령_판례/archive/경북대_내규/경북대학교_학칙(규정_제2856호).pdf"
```

### 의존성

- PyMuPDF (`fitz`)
- pytesseract + Tesseract OCR 엔진 (한국어+영어)
- pyhwp (`hwp5html`)
- faster-whisper
- ffmpeg
- Pillow

## 데이터 출처

3건의 행정심판 사건(2026. 4.~6.)에서 생성된 76개 핵심 문서를 분석하여 법리, 판례 인용, 문서 구조, 표현 패턴을 추출하였다.

## Contributing

이슈와 풀 리퀘스트를 환영한다. `harness/` 디렉토리 수정 시 `harness/관리_절차.md`의 절차를 따르고, `python harness/scripts/validate_harness.py`로 정합성을 점검한다.

## 라이선스

MIT License
