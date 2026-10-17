// ==UserScript==
// @name         lofi-studio · DistroKid 表單偵察（唯讀）
// @namespace    lofi-studio
// @version      0.1.0
// @description  掃描 DistroKid 上傳表單的 DOM 結構並輸出 JSON，用來產生填表腳本。唯讀，不會送出、修改或上傳任何資料。
// @author       lofi-studio
// @match        https://distrokid.com/*
// @match        https://*.distrokid.com/*
// @run-at       document-idle
// @grant        none
// ==/UserScript==

/*
 * 用途：把 DistroKid 上傳頁的真實 DOM 結構 dump 出來，交給 agent 產生選擇器。
 *
 * 為什麼需要：DistroKid 沒有公開 API，填表只能靠 DOM 選擇器；選擇器一旦用猜的就必壞。
 * 這支腳本只讀不寫：不點按鈕、不改欄位、不送出表單、不上傳檔案。
 *
 * 使用：
 *   1. 在 DistroKid 上傳頁（表單展開到你平常填的狀態）點右上角「掃描」
 *   2. 按「複製」把 JSON 貼回對話，或「下載」存成檔案
 *   3. 開一次 AI Credits 對話框，再掃一次（階段標籤填 ai-credits）
 */

(function () {
  'use strict';

  const VER = '0.1.0';

  // ---------- 小工具 ----------
  const trunc = (s, n = 300) => {
    s = String(s == null ? '' : s);
    return s.length > n ? s.slice(0, n) + '…' : s;
  };
  const norm = (s) => trunc(String(s == null ? '' : s).replace(/\s+/g, ' ').trim(), 120);
  const clsOf = (el) => norm(typeof el.className === 'string' ? el.className : '');
  const isVisible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  // React 會把 fiber 掛在 DOM 節點上；有這個代表是受控元件，填值要用 native setter。
  const fiberCount = (el) => Object.keys(el).filter((k) => k.startsWith('__react')).length;

  const labelOf = (el) => {
    try {
      if (el.id) {
        const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
        if (l) return norm(l.textContent);
      }
      const wrap = el.closest('label');
      if (wrap) return norm(wrap.textContent);
      const block = el.closest('[class*="field"],[class*="form"],[class*="input"],[class*="Field"]');
      if (block) {
        const l = block.querySelector('label,[class*="label"]');
        if (l) return norm(l.textContent);
      }
    } catch (e) { /* ignore */ }
    return '';
  };

  // 產生可用的選擇器字串（給填表腳本用）
  const cssPath = (el) => {
    if (!el || el.nodeType !== 1) return '';
    if (el.id) return '#' + CSS.escape(el.id);
    const parts = [];
    let cur = el;
    for (let i = 0; i < 5 && cur && cur.nodeType === 1 && cur !== document.body; i++) {
      let seg = cur.tagName.toLowerCase();
      if (cur.getAttribute && cur.getAttribute('name')) {
        seg += '[name="' + cur.getAttribute('name') + '"]';
      } else if (typeof cur.className === 'string' && cur.className.trim()) {
        const first = cur.className.trim().split(/\s+/)[0];
        if (/^[A-Za-z][\w-]*$/.test(first)) seg += '.' + first;
      }
      const parent = cur.parentElement;
      if (parent) {
        const sibs = Array.from(parent.children).filter((c) => c.tagName === cur.tagName);
        if (sibs.length > 1) seg += ':nth-of-type(' + (sibs.indexOf(cur) + 1) + ')';
      }
      parts.unshift(seg);
      cur = cur.parentElement;
    }
    return parts.join(' > ');
  };

  const describe = (el) => {
    const o = {
      tag: el.tagName.toLowerCase(),
      type: el.tagName === 'INPUT' ? (el.type || 'text') : undefined,
      name: el.getAttribute('name') || undefined,
      id: el.id || undefined,
      role: el.getAttribute('role') || undefined,
      placeholder: el.getAttribute('placeholder') || undefined,
      ariaLabel: el.getAttribute('aria-label') || undefined,
      autoComplete: el.getAttribute('autocomplete') || undefined,
      required: el.required === true ? true : undefined,
      disabled: el.disabled === true ? true : undefined,
      visible: isVisible(el),
      cls: clsOf(el) || undefined,
      label: labelOf(el) || undefined,
      selector: cssPath(el),
      react: fiberCount(el) || undefined,
    };
    if (el.tagName === 'INPUT') {
      if (el.type === 'password') o.value = el.value ? '<非空，已略過>' : '';
      else if (el.value) o.value = trunc(el.value, 80);
      if (el.type === 'checkbox' || el.type === 'radio') o.checked = el.checked;
      if (el.type === 'file') { o.accept = el.accept || undefined; o.multiple = el.multiple; }
    }
    if (el.tagName === 'TEXTAREA' && el.value) o.value = trunc(el.value, 80);
    if (el.tagName === 'SELECT') {
      o.value = el.value;
      o.options = Array.from(el.options).slice(0, 60).map((op) => (op.selected ? '[x] ' : '') + trunc(op.textContent, 60));
      if (el.multiple) o.multiple = true;
    }
    return o;
  };

  const textOf = (el) => norm(el.textContent || el.value || '');

  // 以關鍵字找出錨點（AI Credits / Songwriter 等步驟會出現在哪裡）
  const KEYWORDS = ['AI', 'Credit', 'Songwriter', 'Album', 'Artist', 'Genre', 'Explicit',
    'ISRC', 'Language', 'Label', 'Track', 'Save', 'Submit', 'Continue', 'Next',
    'Produced', 'Performer', 'previously released', 'cover', 'artwork'];

  const findAnchors = () => {
    const out = {};
    const all = Array.from(document.querySelectorAll('button,a,label,h1,h2,h3,h4,h5,legend,div,span,p'));
    for (const kw of KEYWORDS) {
      const hits = [];
      for (const el of all) {
        if (hits.length >= 6) break;
        if (!isVisible(el)) continue;
        if (el.children.length > 1) continue; // 只要葉節點，避免抓到整個 app
        const t = textOf(el);
        if (!t || t.length > 80) continue;
        if (t.toLowerCase().indexOf(kw.toLowerCase()) === -1) continue;
        hits.push({ text: t, tag: el.tagName.toLowerCase(), cls: clsOf(el) || undefined, selector: cssPath(el) });
      }
      if (hits.length) out[kw] = hits;
    }
    return out;
  };

  // ---------- 掃描 ----------
  function scan(stage) {
    const q = (s) => Array.from(document.querySelectorAll(s));
    const report = {
      recon_version: VER,
      scanned_at: new Date().toISOString(),
      stage: stage || '(未標記)',
      page: {
        url: location.href,
        title: document.title,
        in_iframe: window.top !== window.self,
        user_agent: navigator.userAgent,
      },
      // 這個區塊決定「授權資料夾」的路能不能走
      env: {
        showDirectoryPicker_in_page: typeof window.showDirectoryPicker === 'function',
        showOpenFilePicker_in_page: typeof window.showOpenFilePicker === 'function',
        dataTransfer_supported: typeof DataTransfer === 'function',
        drag_event_supported: typeof DragEvent === 'function',
      },
      headings: q('h1,h2,h3,h4,h5,h6,legend,[role="heading"]').filter(isVisible)
        .slice(0, 60).map((el) => ({ text: textOf(el), cls: clsOf(el) || undefined })),
      inputs: q('input').slice(0, 2000).map(describe),
      selects: q('select').slice(0, 300).map(describe),
      textareas: q('textarea').slice(0, 60).map(describe),
      file_inputs: q('input[type="file"]').slice(0, 200).map(describe),
      buttons: q('button,[role="button"],input[type="submit"],input[type="button"],a[class*="btn"]')
        .slice(0, 400).map((el) => ({
          text: textOf(el), tag: el.tagName.toLowerCase(), type: el.type || undefined,
          disabled: el.disabled === true ? true : undefined, visible: isVisible(el),
          cls: clsOf(el) || undefined, selector: cssPath(el),
        })),
      checkbox_groups: q('input[type="checkbox"],input[type="radio"]').slice(0, 200).map((el) => ({
        name: el.getAttribute('name') || undefined, id: el.id || undefined,
        checked: el.checked, label: labelOf(el) || undefined, selector: cssPath(el),
      })),
      labels: Array.from(new Set(q('label').filter(isVisible).map(textOf).filter(Boolean))).slice(0, 200),
      anchors: findAnchors(),
      frame_count: window.frames.length,
    };
    return report;
  }

  // ---------- UI ----------
  let stageLabel = '';

  const panel = document.createElement('div');
  panel.style.cssText = [
    'position:fixed', 'top:12px', 'right:12px', 'z-index:2147483647',
    'width:520px', 'max-height:80vh', 'display:flex', 'flex-direction:column',
    'background:#101418', 'color:#e8edf2', 'font:12px/1.5 ui-monospace,Menlo,monospace',
    'border:1px solid #2a3540', 'border-radius:10px', 'box-shadow:0 12px 40px rgba(0,0,0,.5)',
    'overflow:hidden',
  ].join(';');

  panel.innerHTML = [
    '<div style="display:flex;align-items:center;gap:8px;padding:8px 10px;background:#161d24;border-bottom:1px solid #2a3540">',
    '  <strong style="flex:1">DistroKid 表單偵察 v' + VER + '</strong>',
    '  <button id="dsk-toggle" style="cursor:pointer">收合</button>',
    '</div>',
    '<div id="dsk-body" style="padding:10px;display:flex;flex-direction:column;gap:8px;overflow:auto">',
    '  <div style="color:#9fb0c0">唯讀：不點按鈕、不改欄位、不送出、不上傳。</div>',
    '  <div style="display:flex;gap:6px;align-items:center">',
    '    <span>階段標籤</span>',
    '    <input id="dsk-stage" placeholder="例如 upload-form / ai-credits" style="flex:1;padding:4px 6px;background:#0b0f13;color:#e8edf2;border:1px solid #2a3540;border-radius:4px">',
    '  </div>',
    '  <div style="display:flex;gap:6px;flex-wrap:wrap">',
    '    <button id="dsk-scan" style="cursor:pointer;font-weight:bold">掃描</button>',
    '    <button id="dsk-copy" style="cursor:pointer">複製 JSON</button>',
    '    <button id="dsk-download" style="cursor:pointer">下載 JSON</button>',
    '    <button id="dsk-clear" style="cursor:pointer">清空</button>',
    '  </div>',
    '  <div id="dsk-status" style="color:#7fd1a8;min-height:16px"></div>',
    '  <textarea id="dsk-out" spellcheck="false" placeholder="按「掃描」開始" style="width:100%;height:340px;padding:8px;background:#0b0f13;color:#cfe3f5;border:1px solid #2a3540;border-radius:4px;resize:vertical"></textarea>',
    '  <div id="dsk-summary" style="color:#9fb0c0"></div>',
    '</div>',
  ].join('');

  const $ = (id) => panel.querySelector('#' + id);
  const setStatus = (msg) => { $('dsk-status').textContent = msg; };

  let reports = [];

  $('dsk-toggle').onclick = () => {
    const b = $('dsk-body');
    const hidden = b.style.display === 'none';
    b.style.display = hidden ? 'flex' : 'none';
    $('dsk-toggle').textContent = hidden ? '收合' : '展開';
  };

  $('dsk-scan').onclick = () => {
    stageLabel = $('dsk-stage').value.trim();
    let rep;
    try {
      rep = scan(stageLabel);
    } catch (e) {
      setStatus('掃描失敗：' + e.message);
      return;
    }
    reports.push(rep);
    $('dsk-out').value = JSON.stringify(reports.length === 1 ? rep : reports, null, 2);
    const n = rep;
    $('dsk-summary').textContent = [
      'inputs=' + n.inputs.length,
      'file_inputs=' + n.file_inputs.length,
      'selects=' + n.selects.length,
      'buttons=' + n.buttons.length,
      'checkboxes=' + n.checkbox_groups.length,
      '｜資料夾授權 API：' + (n.env.showDirectoryPicker_in_page ? '可用 ✅' : '不可用 ❌'),
    ].join(' ');
    setStatus('已掃描（第 ' + reports.length + ' 份，階段：' + (rep.stage) + '）');
    // 也印一份到 console，方便備援
    console.log('[lofi distrokid recon]', rep);
  };

  $('dsk-copy').onclick = async () => {
    const txt = $('dsk-out').value;
    if (!txt) { setStatus('還沒有內容'); return; }
    try {
      await navigator.clipboard.writeText(txt);
      setStatus('已複製到剪貼簿 ✅');
    } catch (e) {
      $('dsk-out').select();
      setStatus('自動複製被擋，已幫你全選，請按 Cmd/Ctrl+C');
    }
  };

  $('dsk-download').onclick = () => {
    const txt = $('dsk-out').value;
    if (!txt) { setStatus('還沒有內容'); return; }
    const blob = new Blob([txt], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'distrokid-recon-' + (stageLabel || 'scan') + '-' + Date.now() + '.json';
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    setStatus('已下載 ✅');
  };

  $('dsk-clear').onclick = () => {
    reports = [];
    $('dsk-out').value = '';
    $('dsk-summary').textContent = '';
    setStatus('已清空');
  };

  // Ctrl/Cmd+Shift+D 收合／展開，避免擋住頁面
  window.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key.toLowerCase() === 'd') {
      e.preventDefault();
      $('dsk-toggle').onclick();
    }
  }, true);

  document.body.appendChild(panel);
  setStatus('待命。展開表單後按「掃描」。');

  // ---------- 偵錯入口 ----------
  // 在 DevTools Console 可以直接呼叫，不必碰面板：
  //   __lofiRecon.scan('ai-credits')     掃描並回傳物件
  //   copy(__lofiRecon.scan())           掃描後直接複製到剪貼簿
  //   __lofiRecon.describe($0)           看 Elements 面板選取中的元素（找選擇器用）
  //   __lofiRecon.pathFor($0)            同上，只要選擇器字串
  //   __lofiRecon.panel()                把面板叫回來（被關掉時用）
  window.__lofiRecon = {
    version: VER,
    scan: (stage) => scan(stage || ''),
    describe,
    pathFor: cssPath,
    panel: () => { if (!panel.isConnected) document.body.appendChild(panel); return panel; },
    env: () => scan('env-check').env,
  };
  console.log('[lofi distrokid recon] 已載入 v' + VER + '｜可用 __lofiRecon.scan() 直接掃描');
})();
