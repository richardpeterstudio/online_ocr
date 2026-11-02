'use strict';

const $ = (id) => document.getElementById(id);
const LOW_SCORE = 0.8; // 低於這個信心分數的框線用橘色標示
const SVG_NS = 'http://www.w3.org/2000/svg';

let current = null; // { name, objectUrl, result, offsets }
let maxFileMb = 20;

// ---------- 小工具 ----------
let toastTimer;
function toast(msg) {
  const t = $('toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), 2000);
}

function show(view) {
  for (const v of ['uploadView', 'busyView', 'resultView']) $(v).hidden = v !== view;
  $('newBtn').hidden = view !== 'resultView';
}

function showError(msg) {
  $('error').textContent = msg;
  $('error').hidden = !msg;
}

// ---------- 上傳與辨識 ----------
async function handleFile(file) {
  if (!file) return;
  showError('');
  if (file.size > maxFileMb * 1024 * 1024) {
    showError(`檔案太大了，上限是 ${maxFileMb} MB。`);
    return;
  }
  show('busyView');
  $('busyName').textContent = file.name || '貼上的圖片';

  const form = new FormData();
  form.append('file', file, file.name || 'paste.png');
  try {
    const res = await fetch('/api/ocr', { method: 'POST', body: form });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof body.detail === 'string' ? body.detail : '辨識失敗，請稍後再試。');
    if (current && current.objectUrl) URL.revokeObjectURL(current.objectUrl);
    current = {
      name: file.name || '貼上的圖片',
      objectUrl: body.kind === 'image' ? URL.createObjectURL(file) : null,
      result: body,
    };
    renderResult();
  } catch (err) {
    show('uploadView');
    showError(err.message === 'Failed to fetch' ? '無法連線到伺服器，請檢查網路後再試。' : err.message);
  } finally {
    $('file').value = '';
  }
}

// ---------- 結果 ----------
function renderResult() {
  const r = current.result;
  $('fileName').textContent = current.name;
  $('fileName').title = current.name;
  $('text').value = r.text;

  const lineCount = r.pages.reduce((n, p) => n + p.lines.length, 0);
  const pageInfo = r.pages.length > 1 ? `${r.pages.length} 頁 · ` : '';
  $('stats').textContent = `${pageInfo}${lineCount} 段文字 · ${(r.elapsed_ms / 1000).toFixed(1)} 秒`;

  current.offsets = computeOffsets(r);

  const pages = $('pages');
  pages.innerHTML = '';
  r.pages.forEach((p, pi) => {
    const wrap = document.createElement('div');
    wrap.className = 'page';

    const img = document.createElement('img');
    img.alt = `第 ${pi + 1} 頁`;
    img.src = p.preview || current.objectUrl;
    wrap.appendChild(img);

    const svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('viewBox', `0 0 ${p.width} ${p.height}`);
    svg.setAttribute('preserveAspectRatio', 'none');
    const g = document.createElementNS(SVG_NS, 'g');
    g.setAttribute('class', 'boxes');
    p.lines.forEach((line, li) => {
      const poly = document.createElementNS(SVG_NS, 'polygon');
      poly.setAttribute('points', line.box.map((pt) => pt.join(',')).join(' '));
      if (line.score < LOW_SCORE) poly.classList.add('low');
      poly.dataset.page = pi;
      poly.dataset.line = li;
      const title = document.createElementNS(SVG_NS, 'title');
      title.textContent = `${line.text}（信心 ${Math.round(line.score * 100)}%）`;
      poly.appendChild(title);
      g.appendChild(poly);
    });
    svg.appendChild(g);
    wrap.appendChild(svg);

    if (r.pages.length > 1) {
      const label = document.createElement('span');
      label.className = 'page-label';
      label.textContent = `第 ${pi + 1} 頁`;
      wrap.appendChild(label);
    }
    pages.appendChild(wrap);
  });

  show('resultView');
  window.scrollTo({ top: 0 });
}

/** 找出每段文字在整份結果文字中的位置，點框線時用來選取對應文字。 */
function computeOffsets(r) {
  const offsets = [];
  let base = 0;
  r.pages.forEach((p) => {
    let pos = 0;
    offsets.push(p.lines.map((line) => {
      const i = p.text.indexOf(line.text, pos);
      if (i < 0) return null;
      pos = i + line.text.length;
      return [base + i, base + pos];
    }));
    base += p.text.length + 2; // 頁與頁之間是兩個換行
  });
  return offsets;
}

function selectLine(pi, li) {
  document.querySelectorAll('.boxes polygon.active').forEach((el) => el.classList.remove('active'));
  const poly = document.querySelector(`.boxes polygon[data-page="${pi}"][data-line="${li}"]`);
  if (poly) poly.classList.add('active');

  const range = current.offsets[pi] && current.offsets[pi][li];
  const ta = $('text');
  // 使用者改過文字後位置可能對不上，只在原文還在原位時選取
  if (!range || ta.value.slice(range[0], range[1]) !== current.result.pages[pi].lines[li].text) return;
  ta.focus({ preventScroll: true });
  ta.setSelectionRange(range[0], range[1]);
  // 把選取的位置捲到可視範圍
  const before = ta.value.slice(0, range[0]).split('\n').length - 1;
  const lineHeight = parseFloat(getComputedStyle(ta).lineHeight) || 28;
  ta.scrollTop = Math.max(0, before * lineHeight - ta.clientHeight / 3);
}

// ---------- 事件 ----------
const drop = $('drop');
$('file').addEventListener('change', (e) => handleFile(e.target.files[0]));
drop.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $('file').click(); }
});

let depth = 0;
window.addEventListener('dragenter', (e) => { e.preventDefault(); depth++; drop.classList.add('over'); });
window.addEventListener('dragleave', () => { if (--depth <= 0) { depth = 0; drop.classList.remove('over'); } });
window.addEventListener('dragover', (e) => e.preventDefault());
window.addEventListener('drop', (e) => {
  e.preventDefault();
  depth = 0;
  drop.classList.remove('over');
  if (!$('busyView').hidden) return;
  handleFile(e.dataTransfer.files[0]);
});

window.addEventListener('paste', (e) => {
  if (document.activeElement === $('text') || !$('busyView').hidden) return; // 在文字框裡貼上就是一般貼上
  const item = Array.from(e.clipboardData.items).find((i) => i.kind === 'file');
  if (item) {
    e.preventDefault();
    handleFile(item.getAsFile());
  }
});

$('pages').addEventListener('click', (e) => {
  const poly = e.target.closest('polygon');
  if (poly) selectLine(+poly.dataset.page, +poly.dataset.line);
});

$('showBoxes').addEventListener('change', (e) => $('pages').classList.toggle('hide-boxes', !e.target.checked));

$('copyBtn').addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText($('text').value);
  } catch {
    $('text').select();
    document.execCommand('copy');
  }
  toast('已複製文字');
});

$('dlBtn').addEventListener('click', () => {
  const blob = new Blob([$('text').value], { type: 'text/plain;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = (current.name.replace(/\.[^.]+$/, '') || 'ocr') + '.txt';
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
});

$('newBtn').addEventListener('click', () => {
  showError('');
  show('uploadView');
});

fetch('/api/config')
  .then((r) => r.json())
  .then((c) => {
    maxFileMb = c.max_file_mb;
    $('limits').textContent = `PNG、JPG、WebP、BMP、TIFF、PDF · 檔案上限 ${c.max_file_mb} MB · PDF 最多 ${c.max_pdf_pages} 頁`;
  })
  .catch(() => {});
