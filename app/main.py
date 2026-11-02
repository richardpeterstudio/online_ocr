"""線上 OCR 服務：上傳圖片或 PDF，回傳辨識出的文字與位置。

啟動：uv run uvicorn app.main:app --reload
"""

from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import ocr

# ---------- 設定（可用環境變數調整） ----------
MAX_FILE_MB = float(os.getenv("OCR_MAX_FILE_MB", "20"))
MAX_PDF_PAGES = int(os.getenv("OCR_MAX_PDF_PAGES", "20"))
CONCURRENCY = int(os.getenv("OCR_CONCURRENCY", "2"))  # 同時辨識的檔案數，依 CPU 核心數調整
PRELOAD = os.getenv("OCR_PRELOAD", "1") == "1"  # 啟動時先載入模型，第一個請求就不用等

STATIC_DIR = Path(__file__).parent / "static"
MAX_BYTES = int(MAX_FILE_MB * 1024 * 1024)

_slots: asyncio.Semaphore | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _slots
    _slots = asyncio.Semaphore(CONCURRENCY)
    if PRELOAD:
        await run_in_threadpool(ocr.get_engine)
    yield


app = FastAPI(title="線上 OCR", version="1.0.0", lifespan=lifespan, docs_url="/api/docs", redoc_url=None)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    return resp


@app.exception_handler(ocr.OCRInputError)
async def input_error(_request: Request, exc: ocr.OCRInputError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/config")
async def config():
    return {"max_file_mb": MAX_FILE_MB, "max_pdf_pages": MAX_PDF_PAGES}


@app.post("/api/ocr")
async def run_ocr(
    file: UploadFile = File(..., description="圖片（PNG/JPG/WebP/BMP/TIFF）或 PDF"),
    min_score: float = Form(0.5, ge=0.0, le=1.0, description="信心分數低於這個值的文字會被略過"),
):
    data = await _read_limited(file)
    if not data:
        raise HTTPException(status_code=422, detail="檔案是空的。")

    assert _slots is not None
    async with _slots:
        started = time.perf_counter()
        pages = await run_in_threadpool(_process, data, min_score)
        elapsed = time.perf_counter() - started

    return {
        "filename": file.filename,
        "kind": "pdf" if ocr.is_pdf(data) else "image",
        "elapsed_ms": round(elapsed * 1000),
        "text": "\n\n".join(p.text for p in pages),
        "pages": [{**asdict(p), "text": p.text} for p in pages],
    }


def _process(data: bytes, min_score: float) -> list[ocr.Page]:
    images = ocr.load_pages(data, MAX_PDF_PAGES)
    return [ocr.ocr_image(img, i, with_preview=from_pdf, min_score=min_score) for i, (img, from_pdf) in enumerate(images)]


async def _read_limited(file: UploadFile) -> bytes:
    """邊讀邊檢查大小，超過上限就停止，不會把整個大檔案讀進記憶體。"""
    chunks, total = [], 0
    while chunk := await file.read(1024 * 1024):
        total += len(chunk)
        if total > MAX_BYTES:
            raise HTTPException(status_code=413, detail=f"檔案太大了，上限是 {MAX_FILE_MB:g} MB。")
        chunks.append(chunk)
    return b"".join(chunks)


# ---------- 前端 ----------
@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
