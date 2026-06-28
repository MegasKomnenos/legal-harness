import os
import sys
import gc
import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from io import BytesIO
from xml.etree import ElementTree as ET
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
SCRATCH_DIR = Path(tempfile.mkdtemp(prefix="extract_"))

whisper_model = None

TESSDATA_PREFIX = os.environ.get(
    "TESSDATA_PREFIX",
    str(Path(sys.prefix) / "share" / "tessdata"),
)
if os.path.isdir(TESSDATA_PREFIX):
    os.environ["TESSDATA_PREFIX"] = TESSDATA_PREFIX


def init_whisper():
    global whisper_model
    if whisper_model is None:
        from faster_whisper import WhisperModel
        print("[init] faster-whisper 로딩 중...")
        whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
        print("[init] faster-whisper 준비 완료")
    return whisper_model


def ocr_image_file(path):
    import pytesseract
    from PIL import Image
    img = Image.open(str(path))
    text = pytesseract.image_to_string(img, lang="kor+eng", config="--psm 6")
    return text.strip()


def ocr_image_bytes(img_bytes, ext="png"):
    import pytesseract
    from PIL import Image
    img = Image.open(BytesIO(img_bytes))
    text = pytesseract.image_to_string(img, lang="kor+eng", config="--psm 6")
    return text.strip()


# ── 방법 1: 직접 텍스트 읽기 ──

def extract_text_file(path):
    for enc in ["utf-8", "cp949", "euc-kr", "latin-1"]:
        try:
            return path.read_text(encoding=enc)
        except (UnicodeDecodeError, ValueError):
            continue
    return ""


# ── 좌표 기반 텍스트 재조합 ──

def extract_page_structured(page):
    Y_TOL = 4.0
    d = page.get_text("dict")
    spans_all = []
    for block in d["blocks"]:
        if block["type"] != 0:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                text = span["text"]
                if not text.strip():
                    continue
                y_mid = (span["bbox"][1] + span["bbox"][3]) / 2
                x0, x1 = span["bbox"][0], span["bbox"][2]
                sz = span["size"]
                spans_all.append((y_mid, x0, x1, sz, text.strip()))
    if not spans_all:
        return ""
    spans_all.sort(key=lambda t: (t[0], t[1]))

    result_lines = []
    cur_y = None
    cur_spans = []
    for y, x0, x1, sz, text in spans_all:
        if cur_y is None or abs(y - cur_y) > Y_TOL:
            if cur_spans:
                result_lines.append(_merge_spans(cur_spans))
            cur_y = y
            cur_spans = [(x0, x1, sz, text)]
        else:
            cur_spans.append((x0, x1, sz, text))
    if cur_spans:
        result_lines.append(_merge_spans(cur_spans))
    return "\n".join(result_lines)


def _merge_spans(spans):
    spans.sort(key=lambda t: t[0])
    parts = []
    prev_x1 = None
    for x0, x1, sz, text in spans:
        if prev_x1 is not None:
            gap = x0 - prev_x1
            if gap > sz * 0.3:
                parts.append(" ")
            elif gap < -sz * 0.5:
                pass
        parts.append(text)
        prev_x1 = max(prev_x1 or x1, x1)
    return "".join(parts)


def _postprocess_structured(text):
    text = re.sub(r"제조(\S+?)(\d+)\(", lambda m: f"제{m.group(2)}조({m.group(1)}", text)
    text = re.sub(r"대학원생 포함의\(\)", "대학원생 포함)의", text)
    text = re.sub(r"- \d+ -\n?", "", text)
    return text.strip()


# ── 방법 2 + 3: PDF 추출 (텍스트 레이어 + 페이지별 OCR 판정) ──

def extract_pdf(path):
    import fitz
    doc = fitz.open(str(path))
    methods_used = set()
    all_text = []

    for i, page in enumerate(doc):
        text = page.get_text().strip()
        images = page.get_images()
        drawings = page.get_drawings()

        if len(text) > 50:
            structured = extract_page_structured(page)
            page_text = structured if structured else text
            all_text.append(page_text)
            methods_used.add("pdf_text")
            # 텍스트 레이어 페이지라도 별도 차트/그림 이미지가 있으면 OCR 보충
            for img in images:
                try:
                    bi = doc.extract_image(img[0])
                    w, h = bi["width"], bi["height"]
                    if w * h <= 500000:
                        continue
                    xref = img[0]
                    img_rects = page.get_image_rects(xref)
                    rect = page.rect
                    page_area = rect.width * rect.height
                    is_bg = any((ir.width * ir.height) / page_area > 0.80 for ir in img_rects)
                    if is_bg:
                        continue
                    ocr_text = ocr_image_bytes(bi["image"], bi["ext"])
                    if ocr_text.strip():
                        all_text.append(f"--- 페이지 {i+1} (이미지 {w}x{h} OCR 보충) ---\n{ocr_text}")
                        methods_used.add("image_ocr_supplement")
                except Exception:
                    pass
            continue

        has_content_img = False
        for img in images:
            try:
                bi = doc.extract_image(img[0])
                if bi["width"] * bi["height"] > 160000:
                    has_content_img = True
                    break
            except Exception:
                pass

        if has_content_img:
            page_ocr_parts = []
            for img in images:
                try:
                    bi = doc.extract_image(img[0])
                    if bi["width"] * bi["height"] > 160000:
                        ocr_text = ocr_image_bytes(bi["image"], bi["ext"])
                        if ocr_text.strip():
                            page_ocr_parts.append(ocr_text)
                except Exception:
                    pass
            if page_ocr_parts:
                combined = "\n".join(page_ocr_parts)
                all_text.append(f"--- 페이지 {i+1} (이미지 OCR) ---\n{combined}")
                methods_used.add("image_ocr")
            if text:
                structured = extract_page_structured(page)
                all_text.append(structured if structured else text)
                methods_used.add("pdf_text")

        elif len(drawings) > 100:
            pix = page.get_pixmap(dpi=450)
            tmp_path = SCRATCH_DIR / f"render_p{i+1}.png"
            pix.save(str(tmp_path))
            ocr_text = ocr_image_file(tmp_path)
            tmp_path.unlink(missing_ok=True)
            if ocr_text.strip():
                all_text.append(f"--- 페이지 {i+1} (벡터 렌더링 OCR) ---\n{ocr_text}")
                methods_used.add("vector_ocr")

        elif text:
            structured = extract_page_structured(page)
            all_text.append(structured if structured else text)
            methods_used.add("pdf_text")

    doc.close()
    return "\n\n".join(all_text), list(methods_used)


# ── 방법 4: HWP 변환 추출 ──

def extract_hwp(path):
    out_dir = SCRATCH_DIR / "hwp_out"
    if out_dir.exists():
        shutil.rmtree(out_dir)

    result = subprocess.run(
        ["hwp5html", str(path), "--output", str(out_dir)],
        capture_output=True, text=True, timeout=60,
    )

    xhtml_path = out_dir / "index.xhtml"
    if not xhtml_path.exists():
        for f in out_dir.rglob("*.xhtml"):
            xhtml_path = f
            break
        else:
            for f in out_dir.rglob("*.html"):
                xhtml_path = f
                break

    if not xhtml_path.exists():
        return ""

    content = xhtml_path.read_text(encoding="utf-8", errors="replace")
    content = re.sub(r"<style[^>]*>.*?</style>", "", content, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", content)
    text = re.sub(r"&[a-zA-Z]+;", " ", text)
    text = re.sub(r"&#\d+;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    shutil.rmtree(out_dir, ignore_errors=True)
    return text


# ── 방법 5: DOCX XML 추출 ──

def extract_docx(path):
    with zipfile.ZipFile(str(path)) as z:
        doc_xml = z.read("word/document.xml")
    tree = ET.fromstring(doc_xml)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs = []
    for para in tree.findall(".//w:p", ns):
        texts = para.findall(".//w:t", ns)
        para_text = "".join(t.text or "" for t in texts)
        if para_text.strip():
            paragraphs.append(para_text)
    return "\n".join(paragraphs)


# ── 방법 6: 음성 텍스트 변환 ──

def extract_audio_stt(path):
    model = init_whisper()

    wav_path = SCRATCH_DIR / "audio_tmp.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(path), "-ar", "16000", "-ac", "1",
         "-f", "wav", str(wav_path)],
        capture_output=True, timeout=120,
    )

    if not wav_path.exists():
        return ""

    segments, info = model.transcribe(str(wav_path), language="ko")
    lines = []
    for seg in segments:
        ts = f"[{seg.start:.1f}s-{seg.end:.1f}s]"
        lines.append(f"{ts} {seg.text}")

    wav_path.unlink(missing_ok=True)
    return "\n".join(lines)


# ── 방법 7: 비디오 프레임 추출 + OCR ──

def extract_video_frames_ocr(path):
    frame_dir = SCRATCH_DIR / "frames"
    if frame_dir.exists():
        shutil.rmtree(frame_dir)
    frame_dir.mkdir()

    subprocess.run(
        ["ffmpeg", "-y", "-i", str(path), "-vf", "fps=1", "-q:v", "2",
         str(frame_dir / "frame_%03d.png")],
        capture_output=True, timeout=120,
    )

    frames = sorted(frame_dir.glob("*.png"))
    if not frames:
        return ""

    parts = []
    for f in frames:
        ocr_text = ocr_image_file(f)
        if ocr_text.strip():
            idx = f.stem.split("_")[-1]
            parts.append(f"--- 프레임 {idx} ---\n{ocr_text}")

    shutil.rmtree(frame_dir, ignore_errors=True)
    return "\n\n".join(parts)


# ── 추출 후 아티팩트 정리 ──

_PAGE_MARKER = re.compile(r"^---\s*페이지\s*\d+\s*\(.*?\)\s*---\s*$", re.MULTILINE)
_CASENOTE_URL = re.compile(
    r"^https?[:/l]{2,}casenote[\s.].*$", re.MULTILINE | re.IGNORECASE
)
_CASENOTE_LABEL = re.compile(r"^CaseNote\s*$", re.MULTILINE)
_CASENOTE_TIMESTAMP = re.compile(
    r"^\d{2,4}\.\s*\d{1,2}\.\s*\d{1,2}\.\s*(?:오[전후우]|AM|PM)\s*\d{1,2}[.:]\d{2}\s*$",
    re.MULTILINE,
)
_PAGE_COUNTER = re.compile(r"^\d{1,3}\s*/\s*\d{1,3}\s*$", re.MULTILINE)
_LEGISLATION_HEADER = re.compile(
    r"^법제처\s+\d+\s+국가법령정보센터\s*$", re.MULTILINE
)
_CASENOTE_UI = re.compile(
    r"^(?:키워드[를틀]\s*입력하세요|업무\s*관리|이용\s*중인\s*플랜이|"
    r"Al\s*요약|메모|저장|다운로드|인쇄|공유|하이라이트|본문검색|"
    r"보기설정|오류\s*신고|관련\s*문서|확정)\s*$",
    re.MULTILINE,
)
_MULTI_BLANK = re.compile(r"\n{3,}")


def postprocess_text(text):
    for pat in (
        _PAGE_MARKER,
        _CASENOTE_URL,
        _CASENOTE_LABEL,
        _CASENOTE_TIMESTAMP,
        _PAGE_COUNTER,
        _LEGISLATION_HEADER,
        _CASENOTE_UI,
    ):
        text = pat.sub("", text)
    text = _postprocess_structured(text)
    text = _MULTI_BLANK.sub("\n\n", text)
    return text.strip()


# ── 메인 로직 ──

def process_file(path):
    ext = path.suffix.lower()
    result = {"file": str(path), "methods": [], "text_length": 0, "error": None}

    try:
        if ext in (".txt", ".md"):
            text = extract_text_file(path)
            result["methods"].append("direct_read")
            result["text_length"] = len(text)
            return text, result

        elif ext == ".pdf":
            text, methods = extract_pdf(path)
            text = postprocess_text(text)
            result["methods"] = methods
            result["text_length"] = len(text)
            return text, result

        elif ext in (".png", ".jpg", ".jpeg"):
            text = ocr_image_file(path)
            result["methods"].append("image_ocr")
            result["text_length"] = len(text)
            return text, result

        elif ext == ".hwp":
            text = extract_hwp(path)
            result["methods"].append("hwp_convert")
            result["text_length"] = len(text)
            return text, result

        elif ext == ".docx":
            text = extract_docx(path)
            result["methods"].append("docx_xml")
            result["text_length"] = len(text)
            return text, result

        elif ext == ".m4a":
            text = extract_audio_stt(path)
            result["methods"].append("stt")
            result["text_length"] = len(text)
            return text, result

        elif ext == ".mp4":
            parts = []

            stt_text = extract_audio_stt(path)
            if stt_text.strip():
                parts.append("=== 오디오 음성 인식 ===\n" + stt_text)
                result["methods"].append("stt")

            frame_text = extract_video_frames_ocr(path)
            if frame_text.strip():
                parts.append("=== 비디오 프레임 OCR ===\n" + frame_text)
                result["methods"].append("video_frame_ocr")

            text = "\n\n".join(parts)
            result["text_length"] = len(text)
            return text, result

        else:
            result["error"] = f"지원하지 않는 확장자: {ext}"
            return "", result

    except Exception as e:
        result["error"] = str(e)
        return "", result


def main():
    if len(sys.argv) < 2:
        print("사용법: python3 extract_all.py <파일1> [파일2] ...", file=sys.stderr)
        print("지정된 파일에서 텍스트를 추출하여 stdout에 출력합니다.", file=sys.stderr)
        sys.exit(1)

    paths = [Path(p).resolve() for p in sys.argv[1:]]

    for path in paths:
        if not path.exists():
            print(f"[오류] 파일 없음: {path}", file=sys.stderr)
            continue

        print(f"[추출] {path} ...", file=sys.stderr, flush=True)
        text, meta = process_file(path)

        if meta["error"]:
            print(f"[오류] {meta['error']}", file=sys.stderr)
        else:
            methods_str = ", ".join(meta["methods"])
            print(f"[완료] {meta['text_length']}자 [{methods_str}]", file=sys.stderr)

        if len(paths) > 1:
            print(f"\n{'='*60}")
            print(f"=== {path.name} ===")
            print(f"{'='*60}\n")

        print(text)

        gc.collect()

    shutil.rmtree(SCRATCH_DIR, ignore_errors=True)


if __name__ == "__main__":
    main()
