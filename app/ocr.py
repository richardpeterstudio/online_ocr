"""OCR 核心：讀取圖片／PDF、辨識文字、整理成閱讀順序。"""

from __future__ import annotations

import base64
import io
import threading
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

Image.MAX_IMAGE_PIXELS = 80_000_000  # 防止超大圖片（decompression bomb）

MAX_SIDE = 4000  # 超過就縮小，避免吃光記憶體
PDF_DPI = 200
PREVIEW_MAX_SIDE = 1600


class OCRInputError(ValueError):
    """使用者上傳的檔案有問題（格式不支援、PDF 頁數太多…）。訊息會直接顯示給使用者。"""


@dataclass
class Line:
    text: str
    score: float
    box: list[list[float]]  # 四個角的座標 [[x,y], ...]，以頁面像素為單位


@dataclass
class Page:
    index: int
    width: int
    height: int
    lines: list[Line] = field(default_factory=list)
    preview: str | None = None  # PDF 頁面的預覽圖（data URL）；圖片檔由前端自己顯示

    @property
    def text(self) -> str:
        return lines_to_text(self.lines)


# ---------- 引擎 ----------
_engine = None
_engine_lock = threading.Lock()


def get_engine():
    """延遲載入 RapidOCR（第一次辨識時才載入模型）。onnxruntime 的推論可以多執行緒同時呼叫。"""
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                from rapidocr import RapidOCR

                _engine = RapidOCR(params={"Global.log_level": "warning"})
    return _engine


# ---------- 讀檔 ----------
def is_pdf(data: bytes) -> bool:
    return data[:5] == b"%PDF-"


def load_pages(data: bytes, max_pdf_pages: int) -> list[tuple[Image.Image, bool]]:
    """把上傳的檔案轉成圖片清單。回傳 (圖片, 是否來自 PDF)。"""
    if is_pdf(data):
        return [(img, True) for img in _pdf_to_images(data, max_pdf_pages)]
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError) as e:
        raise OCRInputError("無法讀取這個檔案，請上傳 PNG、JPG、WebP、BMP、TIFF 圖片或 PDF。") from e
    except Image.DecompressionBombError as e:
        raise OCRInputError("圖片太大了，請縮小後再上傳。") from e

    # 多頁 TIFF / GIF 只取第一頁
    img = ImageOps.exif_transpose(img)  # 手機照片的旋轉方向
    return [(_normalize(img), False)]


def _pdf_to_images(data: bytes, max_pages: int) -> list[Image.Image]:
    import pymupdf

    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as e:  # pymupdf 會丟各種錯誤
        raise OCRInputError("無法讀取這個 PDF，檔案可能損壞或有密碼保護。") from e
    with doc:
        if doc.needs_pass:
            raise OCRInputError("這個 PDF 有密碼保護，請先解除密碼再上傳。")
        if doc.page_count == 0:
            raise OCRInputError("這個 PDF 沒有任何頁面。")
        if doc.page_count > max_pages:
            raise OCRInputError(f"PDF 最多 {max_pages} 頁，這個檔案有 {doc.page_count} 頁。請拆開後再上傳。")
        images = []
        for page in doc:
            # 依頁面大小調整解析度，避免大圖紙頁面產生超大圖片
            long_side_pt = max(page.rect.width, page.rect.height) or 1
            zoom = min(PDF_DPI / 72, MAX_SIDE / long_side_pt)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            images.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
        return images


def _normalize(img: Image.Image) -> Image.Image:
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, "white")  # 透明背景補白，文字才看得清楚
        bg.paste(img, mask=img.split()[-1])
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")
    if max(img.size) > MAX_SIDE:
        img.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
    return img


def preview_data_url(img: Image.Image) -> tuple[str, float]:
    """縮小成預覽圖，回傳 (data URL, 縮放比例)。"""
    scale = min(1.0, PREVIEW_MAX_SIDE / max(img.size))
    small = img if scale == 1.0 else img.resize((round(img.width * scale), round(img.height * scale)), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    small.save(buf, format="JPEG", quality=82)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode(), scale


# ---------- 辨識 ----------
def recognize(img: Image.Image, min_score: float = 0.5) -> list[Line]:
    result = get_engine()(np.asarray(img)[:, :, ::-1], text_score=min_score)  # RGB → BGR
    if result.boxes is None or result.txts is None:
        return []
    lines = [
        Line(text=t, score=round(float(s), 4), box=[[round(float(x), 1), round(float(y), 1)] for x, y in b])
        for b, t, s in zip(result.boxes, result.txts, result.scores)
        if t.strip()
    ]
    return sort_reading_order(lines)


def ocr_image(img: Image.Image, index: int, with_preview: bool, min_score: float = 0.5) -> Page:
    page = Page(index=index, width=img.width, height=img.height, lines=recognize(img, min_score))
    if with_preview:
        page.preview, _ = preview_data_url(img)
    return page


# ---------- 排版 ----------
def _bounds(line: Line) -> tuple[float, float, float, float]:
    xs = [p[0] for p in line.box]
    ys = [p[1] for p in line.box]
    return min(xs), min(ys), max(xs), max(ys)


def group_rows(lines: list[Line]) -> list[list[Line]]:
    """把垂直位置重疊一半以上的文字框視為同一行，行內由左到右排列。"""
    rows: list[list[Line]] = []
    row_bounds: list[tuple[float, float]] = []
    for line in sorted(lines, key=lambda l: (_bounds(l)[1] + _bounds(l)[3]) / 2):
        _, top, _, bottom = _bounds(line)
        h = max(bottom - top, 1)
        for i, (rt, rb) in enumerate(row_bounds):
            overlap = min(bottom, rb) - max(top, rt)
            if overlap >= 0.5 * min(h, rb - rt):
                rows[i].append(line)
                row_bounds[i] = (min(rt, top), max(rb, bottom))
                break
        else:
            rows.append([line])
            row_bounds.append((top, bottom))
    return [sorted(r, key=lambda l: _bounds(l)[0]) for r in rows]


def sort_reading_order(lines: list[Line]) -> list[Line]:
    return [l for row in group_rows(lines) for l in row]


def lines_to_text(lines: list[Line]) -> str:
    # 同一行裡分開的文字框（例如表格的不同欄）用一個空格隔開
    return "\n".join(" ".join(l.text for l in row) for row in group_rows(lines))
