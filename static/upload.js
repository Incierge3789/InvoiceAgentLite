'use strict';

// --- constants (mirror of backend rules) ---
const MAX_FILES = 10;
const MAX_SIZE  = 3 * 1024 * 1024; // 3MB
const ALLOWED_EXT  = new Set(['.pdf']);
const ALLOWED_MIME = new Set(['application/pdf']);

// --- elements ---
const dz    = document.getElementById('dropzone');
const input = document.getElementById('fileInput');
const pick  = document.getElementById('pickBtn');
const cnt   = document.getElementById('selectedCount');
const upBtn = document.getElementById('uploadBtn');
const clr   = document.getElementById('clearBtn');
const box   = document.getElementById('errorBox');
const tbody = document.getElementById('resultsBody');
const jsonBtn = document.getElementById('saveJsonBtn');
const csvBtn  = document.getElementById('saveCsvBtn');

// --- state ---
let selected = [];   // File[]
let lastJson = null; // for export

function extOf(name){
  const i = name.lastIndexOf('.');
  return i >= 0 ? name.slice(i).toLowerCase() : '';
}

function showErrors(messages){
  if (!box) return;
  if (!messages || messages.length === 0){
    box.style.display = 'none';
    box.innerHTML = '';
    return;
  }
  box.style.display = 'block';
  box.innerHTML = '<ul style="margin:0;padding-left:1rem;">' +
    messages.map(m => '<li>'+m+'</li>').join('') + '</ul>';
}

function renderSelected(){
  const n = selected.length;
  if (cnt) cnt.textContent = (n > 0 ? (n + 'ファイル選択') : '');
  upBtn.disabled = (n === 0);
}

function clearAll(){
  selected = [];
  renderSelected();
  showErrors([]);
  if (input) input.value = '';
  if (tbody) tbody.innerHTML = '';
  lastJson = null;
}

function addFilesLike(list){
  const errs = [];
  const toAdd = [];
  // count limit
  if (selected.length + list.length > MAX_FILES){
    errs.push('一度に選択できるのは最大10ファイルです。');
  }
  for (let i = 0; i < list.length; i++){
    if (selected.length + toAdd.length >= MAX_FILES) break;
    const f = list[i];
    const ext = extOf(f.name);
    if (!ALLOWED_EXT.has(ext) && !ALLOWED_MIME.has(f.type)){
      errs.push('PDFファイルのみ対応しています：' + f.name);
      continue;
    }
    if (f.size > MAX_SIZE){
      errs.push('ファイルサイズが上限（3MB）を超えています：' + f.name);
      continue;
    }
    toAdd.push(f);
  }
  // append
  selected = selected.concat(toAdd);
  renderSelected();
  showErrors(errs);
}

// --- wire UI ---
if (pick) pick.addEventListener('click', () => input && input.click());

if (input) input.addEventListener('change', (e) => {
  const files = e.target.files || [];
  if (files.length === 0){
    showErrors(['ファイルが選択されていません。']);
    return;
  }
  addFilesLike(files);
});

if (dz){
  ['dragenter','dragover'].forEach(ev =>
    dz.addEventListener(ev, (e)=>{ e.preventDefault(); e.stopPropagation(); })
  );
  dz.addEventListener('drop', (e)=>{
    e.preventDefault(); e.stopPropagation();
    const files = e.dataTransfer && e.dataTransfer.files ? e.dataTransfer.files : [];
    if (files.length === 0) return;
    addFilesLike(files);
  });
}

if (clr) clr.addEventListener('click', clearAll);

if (upBtn) upBtn.addEventListener('click', async ()=>{
  if (selected.length === 0){
    showErrors(['ファイルが選択されていません。']);
    return;
  }
  const fd = new FormData();
  for (const f of selected){
    fd.append('files', f, f.name);
  }
  upBtn.disabled = true;
  showErrors([]);
  try{
    const res = await fetch('/api/upload', { method:'POST', body: fd });
    const json = await res.json();
    lastJson = json;
    // render table (results only)
    if (tbody){
      tbody.innerHTML = '';
      const rows = (json && json.results) ? json.results : [];
      for (let i=0; i<rows.length; i++){
        const r = rows[i];
        const tr = document.createElement('tr');
        function td(v){ const d=document.createElement('td'); d.textContent = (v==null?'':v); return d; }
        tr.appendChild(td(r.file));
        tr.appendChild(td(r.date));
        tr.appendChild(td(r.amount!=null? (r.amount+'円') : ''));
        tr.appendChild(td(r.vendor));
        tr.appendChild(td(r.confidence));
        tr.appendChild(td(r.needs_review ? 'はい':'いいえ'));
        tbody.appendChild(tr);
      }
    }
    // show API-side validation messages if any
    if (json && json.errors && json.errors.length){
      showErrors(json.errors.map(e => e.message));
    }else{
      showErrors([]);
    }
  }catch(err){
    showErrors(['アップロード中にエラーが発生しました。']);
  }finally{
    upBtn.disabled = (selected.length === 0);
  }
});

// exports
function saveBlob(blob, name){
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  URL.revokeObjectURL(a.href);
}

if (jsonBtn) jsonBtn.addEventListener('click', ()=>{
  if (!lastJson || !lastJson.results) return;
  const b = new Blob([JSON.stringify(lastJson.results, null, 2)], {type:'application/json'});
  const ts = new Date().toISOString().replace(/[:.]/g,'').slice(0,15);
  saveBlob(b, 'invoices_' + ts + '.json');
});

if (csvBtn) csvBtn.addEventListener('click', ()=>{
  if (!lastJson || !lastJson.results) return;
  const rows = lastJson.results;
  const header = ['file','date','amount','vendor','confidence','needs_review','raw_excerpt'];
  const esc = (s)=> {
    if (s==null) return '';
    const t = String(s).replace(/"/g,'""');
    return '"' + t + '"';
  };
  const lines = [header.join(',')];
  for (const r of rows){
    lines.push([
      esc(r.file), esc(r.date), esc(r.amount),
      esc(r.vendor), esc(r.confidence),
      esc(r.needs_review ? 'TRUE':'FALSE'),
      esc(r.raw_excerpt)
    ].join(','));
  }
  const b = new Blob([lines.join('\n')], {type:'text/csv;charset=utf-8'});
  const ts = new Date().toISOString().replace(/[:.]/g,'').slice(0,15);
  saveBlob(b, 'invoices_' + ts + '.csv');
});

// initial
clearAll();