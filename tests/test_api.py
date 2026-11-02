import io
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import main, ocr

FIXTURE = Path(__file__).parent / "fixtures" / "sample.png"


@pytest.fixture(scope="module")
def client():
    with TestClient(main.app) as c:
        yield c


def post(client, data: bytes, name="a.png", **form):
    return client.post("/api/ocr", files={"file": (name, data)}, data=form)


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_index_page(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "文字辨識" in r.text


def test_image_ocr(client):
    r = post(client, FIXTURE.read_bytes())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "image"
    assert "線上文字辨識系統" in body["text"]
    assert "Hello World" in body["text"]
    page = body["pages"][0]
    assert page["preview"] is None
    assert len(page["lines"]) == 3
    assert len(page["lines"][0]["box"]) == 4


def test_pdf_ocr(client):
    doc = pymupdf.open()
    for _ in range(2):
        page = doc.new_page(width=600, height=200)
        page.insert_image(page.rect, filename=str(FIXTURE))
    r = post(client, doc.tobytes(), name="a.pdf")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "pdf"
    assert len(body["pages"]) == 2
    assert body["pages"][1]["preview"].startswith("data:image/jpeg;base64,")
    assert body["text"].count("Hello World") == 2


def test_transparent_png_gets_white_background(client):
    img = Image.open(FIXTURE).convert("RGBA")
    # 把白色背景變透明
    img.putdata([(r, g, b, 0) if r > 240 else (r, g, b, 255) for r, g, b, _ in img.getdata()])
    buf = io.BytesIO()
    img.save(buf, "PNG")
    r = post(client, buf.getvalue())
    assert r.status_code == 200
    assert "Hello World" in r.json()["text"]


def test_rejects_non_image(client):
    r = post(client, b"hello, not an image", name="a.txt")
    assert r.status_code == 422
    assert "無法讀取" in r.json()["detail"]


def test_rejects_empty(client):
    assert post(client, b"").status_code == 422


def test_rejects_too_large(client, monkeypatch):
    monkeypatch.setattr(main, "MAX_BYTES", 1000)
    r = post(client, FIXTURE.read_bytes())
    assert r.status_code == 413


def test_rejects_too_many_pdf_pages(client, monkeypatch):
    monkeypatch.setattr(main, "MAX_PDF_PAGES", 1)
    doc = pymupdf.open()
    doc.new_page()
    doc.new_page()
    r = post(client, doc.tobytes(), name="a.pdf")
    assert r.status_code == 422
    assert "最多 1 頁" in r.json()["detail"]


def test_reading_order_groups_rows():
    def L(t, x, y, w=50, h=20):
        return ocr.Line(t, 1.0, [[x, y], [x + w, y], [x + w, y + h], [x, y + h]])

    lines = [L("右", 200, 12), L("第二行", 0, 60), L("左", 0, 10)]
    assert [l.text for l in ocr.sort_reading_order(lines)] == ["左", "右", "第二行"]
    assert ocr.lines_to_text(lines) == "左 右\n第二行"
