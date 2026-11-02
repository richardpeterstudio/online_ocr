# 線上文字辨識 OCR

上傳圖片或 PDF，辨識出中文、英文與數字，可以直接修改、複製或下載成 .txt。

- **後端**：Python + FastAPI
- **OCR 引擎**：[RapidOCR](https://github.com/RapidAI/RapidOCR)（PaddleOCR 模型 + onnxruntime，只用 CPU，模型已內建在套件裡）
- **PDF**：PyMuPDF 轉成圖片後辨識
- **前端**：純 HTML / CSS / JS，放在 `app/static/`，由後端直接提供，不需要另外建置

## 功能

- 拖放、點選或 **Ctrl / ⌘ + V 貼上截圖**
- 支援 PNG、JPG、WebP、BMP、TIFF、GIF、PDF（多頁）
- 在原圖上標示每段文字的位置，點框線會選取對應文字；不確定的文字用橘色標示
- 辨識結果可以直接修改、複製、下載
- 手機照片會自動轉正（EXIF），透明背景會補白
- 檔案只在記憶體中處理，不會存到硬碟

## 本機開發

需要 [uv](https://docs.astral.sh/uv/)。

```bash
uv sync
uv run uvicorn app.main:app --reload
```

打開 http://localhost:8000 。API 文件在 http://localhost:8000/api/docs 。

```bash
uv run pytest      # 測試
```

## API

`POST /api/ocr`（multipart/form-data）

| 欄位 | 說明 |
|---|---|
| `file` | 圖片或 PDF |
| `min_score` | 選填，0～1，信心分數低於這個值的文字會被略過（預設 0.5） |

```bash
curl -F file=@photo.jpg http://localhost:8000/api/ocr
```

回傳：

```json
{
  "filename": "photo.jpg",
  "kind": "image",
  "elapsed_ms": 812,
  "text": "全部文字（依閱讀順序，頁與頁之間空一行）",
  "pages": [
    {
      "index": 0, "width": 1200, "height": 900, "preview": null, "text": "這一頁的文字",
      "lines": [{ "text": "一段文字", "score": 0.98, "box": [[x, y], [x, y], [x, y], [x, y]] }]
    }
  ]
}
```

PDF 的每一頁會附上 `preview`（JPEG data URL），`box` 座標以 `width` × `height` 為準。

錯誤時回傳 `{"detail": "說明"}`：`413` 檔案太大、`422` 格式不支援／PDF 頁數太多／有密碼。

## 設定（環境變數）

| 變數 | 預設 | 說明 |
|---|---|---|
| `OCR_MAX_FILE_MB` | `20` | 檔案大小上限 |
| `OCR_MAX_PDF_PAGES` | `20` | PDF 頁數上限 |
| `OCR_CONCURRENCY` | `2` | 同時辨識的檔案數，超過的會排隊。建議設成 CPU 核心數的一半 |
| `OCR_PRELOAD` | `1` | 啟動時先載入模型 |

## 部署（Docker）

```bash
docker build -t online-ocr .
docker run -d -p 8000:8000 --name ocr online-ocr
```

建議至少 1 GB 記憶體、2 核心 CPU。對外開放時放在 Nginx / Caddy / Cloudflare 後面加上 HTTPS，並在反向代理設定上傳大小上限（例如 Nginx 的 `client_max_body_size 25m;`）。

Docker 映像可以部署到任何支援容器的平台，例如 Google Cloud Run、Fly.io、Render、Railway，或自己的 VPS。

## 結構

| 檔案 | 說明 |
|---|---|
| `app/main.py` | API 與網頁路由、上傳大小限制、同時辨識數量控制 |
| `app/ocr.py` | 讀取圖片／PDF、呼叫 OCR、整理閱讀順序 |
| `app/static/` | 前端 |
| `tests/` | 測試 |
