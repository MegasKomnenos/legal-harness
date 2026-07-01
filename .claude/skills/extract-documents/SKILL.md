---
name: extract-documents
description: Extract text from specific documents (PDF, HWP, DOCX, images, audio, video). Run extract_all.py with file paths as arguments to get extracted text on stdout.
argument-hint: <file paths to extract>
---

# Document extraction

`extract_all.py` extracts text from specified files and prints results to stdout.

## Usage

```bash
python extract_all.py <file1> [file2] ...
```

Extracted text goes to stdout. Progress/errors go to stderr.

### Examples

```bash
# Single file
python extract_all.py "법령_판례/archive/판례/대법원_2003두8050.pdf"

# Multiple files
python extract_all.py "cases/02_대동제/절차_04_2차심판_2026-10986/피청구인_증거/*.pdf" "법령_판례/archive/경북대_내규/*.pdf"
```

## Methods

| # | Method | Tool | Targets |
|---|--------|------|---------|
| 1 | Direct text read | `open().read()` | `.txt`, `.md` |
| 2 | PDF text layer | PyMuPDF `page.get_text()` | `.pdf` with text layer |
| 3 | Image OCR | Tesseract (pytesseract, `lang="kor+eng"`) | `.png`, `.jpg`, image-only PDF pages, vector-drawing PDF pages (rendered at 450dpi), chart/diagram images inside text-layer pages |
| 4 | HWP conversion | `hwp5html` → strip HTML tags | `.hwp` |
| 5 | DOCX XML parsing | `zipfile` → `w:t` tags | `.docx` |
| 6 | Speech-to-text | faster-whisper (`base`, `ko`) | `.m4a`, `.mp4` audio track |
| 7 | Video frame extraction | ffmpeg 1fps → OCR | `.mp4` visual content |

## PDF page-level decision logic

Each PDF page is classified independently:

1. **Text > 50 chars** → use text layer. Additionally, any embedded image > 500K pixels that covers < 80% of page area gets supplementary OCR (catches charts/diagrams alongside text).
2. **Text ≤ 50 chars + image > 160K pixels** → extract image, OCR it.
3. **Text ≤ 50 chars + drawings > 100** → render page at 600dpi and OCR with Tesseract psm 6 (single uniform block; avoids the column mis-segmentation that the default psm 3 causes on multi-column web captures), for vector-path PDFs like Gmail/CaseNote printouts. Full-page scan pages are rendered at 300dpi (psm 4).
4. **Otherwise** → empty page (section dividers, page numbers).

## Dependencies

- PyMuPDF (`fitz`) — PDF text/image extraction, page rendering
- pytesseract + Tesseract OCR engine: Korean+English OCR (`lang="kor+eng"`). Requires the Tesseract binary (Windows: `C:\Program Files\Tesseract-OCR\tesseract.exe`)
- pyhwp (`hwp5html`) — HWP to XHTML conversion
- faster-whisper — speech recognition
- ffmpeg — audio extraction, video frame extraction
- Pillow — image processing
