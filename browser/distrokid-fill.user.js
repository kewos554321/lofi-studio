// ==UserScript==
// @name         lofi-studio · DistroKid 填表助手
// @namespace    lofi-studio
// @version      0.4.1
// @description  讀取 output/releases/<run>/ 上架包（release.json + 封面 + audio/），自動填入 DistroKid 上傳表單。停在 Continue 之前，不送出。
// @author       lofi-studio
// @match        https://distrokid.com/new/*
// @match        https://distrokid.com/new/
// @run-at       document-idle
// @grant        none
// ==/UserScript==

/*
 * 設計原則
 * ---------
 * 1. 分段執行，不給「一鍵全填」。每一步都是獨立按鈕，跑完看 log 再跑下一步。
 *    例外：「2. 填專輯層＋逐首」把兩個純文字步驟串在一起（專輯層失敗就不往下填逐首）；
 *    音檔上傳仍獨立成第 3 步，因為要觀察 DistroKid 的 S3 上傳有沒有真的開始跑。
 *    理由：「把音檔塞進上傳欄位」要靠 DistroKid 自己的 JS 接手（它走 S3 表單上傳），
 *    這步無法離線驗證，必須能單獨觀察。
 *    「2. 填專輯層＋逐首」的逐首段結尾會自動驗一次 explicit／instrumental／cleaned 並就地修正
 *    （DistroKid 重繪會把設定洗掉），所以沒有獨立的「修復 explicit」按鈕。
 *    「檢查狀態」是一次列出 1-7 各步結果的總檢查，不必回頭翻 log。
 * 2. 絕不按 #doneButton（Continue）。程式只負責填，送出由人決定。
 * 3. 選擇器來自真實 recon（browser/distrokid-recon-*.json），不是猜的。
 *    逐首欄位的 uuid 每次進站都不同 → 執行時從 input[name^="tracknum_"] 動態解出。
 * 4. 音檔路徑用資料夾裡的實際檔名解，不用 release.json 的 tracks[].audio
 *    （該欄位在拆卷改名後會失效，見 docs/DISTRIBUTION.md）。
 * 5. 付費加購（「Extras (optional but awesome)」）永遠不勾。必勾區塊與 Extras 在 DOM 上
 *    常是同一個容器的相鄰兄弟，靠「往上找容器」很容易把加購一起勾起來 → 多收錢。
 *    recon（browser/distrokid-recon-main-12tracks-*.json）證實加購是 input[name="extras"].extras，
 *    共 6 項：Social Media Pack ($14.95/yr)、Leave a Legacy ($49)、Discovery Pack ($11.88/yr)、
 *    Store Maximizer ($7.95/yr)、DistroVid ($99/yr)、Loudness Normalization ($2.99×12)。
 *    免費商店則是 input[name="store"]（Spotify/Apple/…），兩者 name 不同，可精準分辨。
 *    勾必勾時要排除加購，勾完還要再主動取消一次（見 fillMandatory／uncheckExtras）。
 */

(function () {
  'use strict';

  const VER = '0.4.1';

  // ---------- 狀態 ----------
  const state = {
    dir: null,        // 上架包資料夾 handle
    pkg: null,        // release.json
    cover: null,      // File
    audio: [],        // [{name, file}] 依檔名排序
    plan: [],         // [{no, title, file, srcName, uuid?}]
    busy: false,
  };

  // ---------- 小工具 ----------
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  // 人味節奏：避免三秒填完 12 首這種明顯的機器特徵
  const jitter = (a, b) => sleep((a == null ? 180 : a) + Math.random() * ((b == null ? 700 : b) - (a == null ? 180 : a)));
  const CLICK = 180;      // 每個互動之間的基本停頓

  const log = (msg, kind) => {
    const el = document.querySelector('#dkf-log');
    const line = document.createElement('div');
    const color = kind === 'ok' ? '#7fd1a8' : kind === 'err' ? '#ff8b8b' : kind === 'warn' ? '#ffd479' : '#9fb0c0';
    line.style.cssText = 'color:' + color + ';white-space:pre-wrap';
    line.textContent = msg;
    if (el) { el.appendChild(line); el.scrollTop = el.scrollHeight; }
    console.log('[lofi fill]', msg);
  };

  // React 受控元件：直接改 .value 不會觸發 onChange，必須走 native setter
  function setValue(el, value) {
    if (!el) return false;
    const proto = el instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const desc = Object.getOwnPropertyDescriptor(proto, 'value');
    if (desc && desc.set) desc.set.call(el, value);
    else el.value = value;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return el.value === value;
  }

  function setSelect(el, wanted) {
    if (!el || !wanted) return false;
    const opts = Array.from(el.options);
    const norm = (s) => String(s == null ? '' : s).replace(/^\s*\[x\]\s*/, '').trim();
    const want = norm(wanted).toLowerCase();
    const opt = opts.find((o) => norm(o.textContent).toLowerCase() === want)
      || opts.find((o) => norm(o.textContent).toLowerCase().indexOf(want) !== -1);
    if (!opt) return false;
    const desc = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value');
    if (desc && desc.set) desc.set.call(el, opt.value); else el.value = opt.value;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return el.value === opt.value;
  }

  const clickRadio = (sel) => {
    const el = typeof sel === 'string' ? document.querySelector(sel) : sel;
    if (!el) return false;
    if (!el.checked) el.click();
    return true;
  };

  // 設定 radio 並「驗證它真的被選上」。DistroKid 會在回答某些題目時重繪整個 track 區塊，
  // 重繪會把先前的選擇重置 —— 所以只是 click() 然後假設它成立是不夠的（explicit 就是這樣壞的）。
  async function setRadioVerified(sel, label, tries) {
    const el = typeof sel === 'string' ? document.querySelector(sel) : sel;
    if (!el) { if (label) log('  ⚠️ ' + label + '：找不到欄位', 'warn'); return false; }
    for (let k = 0; k < (tries || 3); k++) {
      if (el.checked) return true;
      el.click();
      await sleep(180);
      if (el.checked) return true;
      await sleep(320);
    }
    if (label) log('  ⚠️ ' + label + '：設了 ' + (tries || 3) + ' 次都沒選上（可能被重繪重置）', 'warn');
    return false;
  }

  async function setCheckboxVerified(sel, want, label, tries) {
    const el = typeof sel === 'string' ? document.querySelector(sel) : sel;
    if (!el) { if (label) log('  ⚠️ ' + label + '：找不到欄位', 'warn'); return false; }
    for (let k = 0; k < (tries || 3); k++) {
      if (el.checked === want) return true;
      el.click();
      await sleep(180);
    }
    if (el.checked === want) return true;
    if (label) log('  ⚠️ ' + label + '：設不成 ' + (want ? '勾選' : '取消'), 'warn');
    return false;
  }

  // 取一個 input 的標籤文字（三種常見寫法都要能抓到）。
  // DistroKid 有些控制項沒有 name/id，只能靠文字比對，所以這支是共用的基礎。
  function labText(el) {
    if (!el) return '';
    if (el.id) {
      const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (l) return (l.textContent || '').trim();
    }
    const wrap = el.closest('label');
    if (wrap) return (wrap.textContent || '').trim();
    const p = el.parentElement;
    return p ? (p.textContent || '').trim().slice(0, 120) : '';
  }

  // 在 root 裡用「標籤文字完全相同」找控制項（給沒有 name/id 的選項用）
  function findByLabelText(root, word, sel) {
    const scope = root || document;
    const list = Array.from(scope.querySelectorAll(sel || 'input[type="checkbox"],input[type="radio"]'));
    return list.find((el) => labText(el).toLowerCase() === String(word).toLowerCase());
  }

  // 用「文字包含」找控制項。有些選項（例如 AI 對話框的
  // "Apply these selections to all songs on this release"）沒有 name/id，
  // 而且 recon 抓不到（AI 對話框是 body 的 portal，排在 400 筆上限之後），
  // 所以只能靠這句話去定位。
  function findByTextContains(root, phrase) {
    const scope = root || document;
    const needle = String(phrase).toLowerCase();

    // 1) 先看 label / 元素本身文字
    for (const el of Array.from(scope.querySelectorAll('input[type="checkbox"],label,[class*="checkbox"]'))) {
      const t = (el.tagName === 'LABEL' ? el.innerText : (labText(el) || el.innerText) || '').toLowerCase();
      if (!t || t.indexOf(needle) === -1) continue;
      if (el.tagName === 'INPUT') return el;
      const inner = el.querySelector('input[type="checkbox"],input[type="radio"]');
      if (inner) return inner;
    }

    // 2) 再退一步：找文字節點，往上幾層找同一個容器裡的 checkbox
    const walker = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
      if ((node.nodeValue || '').toLowerCase().indexOf(needle) === -1) continue;
      let box = node.parentElement;
      for (let k = 0; k < 6 && box; k++) {
        const cb = box.querySelector('input[type="checkbox"],input[type="radio"]');
        if (cb) return cb;
        box = box.parentElement;
      }
    }
    return null;
  }

  const setCheckbox = (el, want) => {
    if (!el) return false;
    if (el.checked !== want) el.click();
    return el.checked === want;
  };

  // 繞過檔案選擇視窗，直接把 File 塞進 input
  function setFile(input, file) {
    if (!input || !file) return false;
    try {
      const dt = new DataTransfer();
      dt.items.add(file);
      input.files = dt.files;
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
      return input.files.length === 1 && input.files[0].name === file.name;
    } catch (e) {
      log('  塞檔失敗：' + e.message, 'err');
      return false;
    }
  }

  const visible = (el) => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
  const q = (s) => document.querySelector(s);
  const qa = (s) => Array.from(document.querySelectorAll(s));

  // ---------- 逐首欄位對應 ----------
  // 每次進站 uuid 都不同，但 input[name^="tracknum_"] 的值就是曲序，
  // 後綴 uuid 可用來組出該首所有欄位的 name/id。
  function trackMap() {
    const m = {};
    qa('input[name^="tracknum_"]').forEach((el) => {
      const uuid = el.getAttribute('name').slice('tracknum_'.length);
      const no = parseInt(el.value, 10);
      if (no) m[no] = uuid;
    });
    return m;
  }

  // ---------- 讀取上架包 ----------
  async function pickPackage() {
    if (!window.showDirectoryPicker) throw new Error('此環境不支援 showDirectoryPicker');
    const dir = await window.showDirectoryPicker({ id: 'lofi-release', mode: 'read' });
    state.dir = dir;
    state.dirName = dir.name;

    // release.json
    let pkg = null;
    try {
      const h = await dir.getFileHandle('release.json');
      pkg = JSON.parse(await (await h.getFile()).text());
    } catch (e) {
      throw new Error('這個資料夾沒有 release.json（請選 output/releases/<run>/ 那一層，不是 audio/）');
    }
    state.pkg = pkg;

    // 封面：找 cover*.jpg/png
    state.cover = null;
    for await (const [name, handle] of dir.entries()) {
      if (handle.kind !== 'file') continue;
      if (/^cover.*\.(jpe?g|png)$/i.test(name)) {
        state.cover = await handle.getFile();
        break;
      }
    }

    // 音檔
    state.audio = [];
    const audioDir = await dir.getDirectoryHandle('audio');
    for await (const [name, handle] of audioDir.entries()) {
      if (handle.kind !== 'file') continue;
      if (!/\.(flac|wav|mp3|m4a|aif|aiff|wma|ogg|aac)$/i.test(name)) continue;
      state.audio.push({ name, file: await handle.getFile() });
    }
    state.audio.sort((a, b) => a.name.localeCompare(b.name, 'en', { numeric: true }));

    // 對應：release.json 的曲序 → audio/ 裡檔名開頭為 "NN_" 的那個檔
    state.plan = (pkg.tracks || []).map((t) => {
      const nn = String(t.no).padStart(2, '0');
      const hit = state.audio.find((a) => a.name.startsWith(nn + '_'))
        || state.audio.find((a) => a.name.startsWith(nn + '-'))
        || state.audio.find((a) => a.name.startsWith(String(t.no) + '_'));
      return { no: t.no, title: t.title, file: hit ? hit.file : null, srcName: hit ? hit.name : null };
    });
  }

  function preview() {
    const p = state.pkg;
    const rows = [];
    rows.push('★ 資料夾  ' + (state.dirName || '?'));
    rows.push('★ 專輯    ' + p.album + '（' + (p.tracks || []).length + ' 首）');
    // 曲數若跟表單上的「Number of songs」不一致，就是選錯包了（最常見：選到修補前的 35 首原版）
    const formN = Number(q('#howManySongsOnThisAlbum') && q('#howManySongsOnThisAlbum').value);
    if (formN && formN !== (p.tracks || []).length) {
      rows.push('');
      rows.push('🛑 表單選了 ' + formN + ' 首，但這個資料夾是 ' + (p.tracks || []).length + ' 首。');
      rows.push('   幾乎一定是選錯資料夾了——請確認你選的是要上架的那一卷。');
    }
    rows.push('');
    rows.push('藝人  ' + p.artist);
    rows.push('風格  ' + (p.dsp && p.dsp.genre ? p.dsp.genre : '(無)') + '   曲數 ' + (p.tracks || []).length);
    rows.push('作者  ' + p.songwriter + '   Explicit ' + p.explicit);
    rows.push('封面  ' + (state.cover ? state.cover.name : '❌ 找不到'));
    rows.push('');
    rows.push('曲序  音檔                            → 曲名');
    state.plan.forEach((t) => {
      rows.push(
        String(t.no).padStart(2, '0') + '    ' +
        String(t.srcName || '❌ 找不到對應音檔').padEnd(30) + '→ ' + t.title
      );
    });
    const extra = state.audio.length - state.plan.length;
    if (extra > 0) rows.push('\n⚠️ audio/ 裡有 ' + extra + ' 個檔案沒有對應到曲目');
    return rows.join('\n');
  }

  // ---------- Number of songs 自動對齊 ----------
  const SONG_LIMIT = 35;   // DistroKid 的曲數上限（下拉只有到 "35 songs"）

  // 表單是不是「還沒填東西」——改曲數會讓 DistroKid 重建所有曲目區塊，
  // 所以只有空白表單才准自動改，否則會洗掉已填的內容。
  function formIsEmpty() {
    const titled = Array.from(document.querySelectorAll('input[name^="title_"]'))
      .some((el) => (el.value || '').trim());
    if (titled) return false;
    const album = q('#albumTitleInput');
    return !(album && (album.value || '').trim());
  }

  // 設定 Number of songs，並等 DistroKid 真的長出對應數量的曲目區塊
  async function setSongCount(n) {
    const sel = q('#howManySongsOnThisAlbum');
    if (!sel) return { ok: false, reason: '找不到 Number of songs 下拉' };
    if (!(n >= 1)) return { ok: false, reason: '曲數不合法：' + n };
    if (n > SONG_LIMIT) {
      return { ok: false, reason: '上架包有 ' + n + ' 首，超過 DistroKid 上限 ' + SONG_LIMIT + ' 首（請先拆卷）' };
    }
    const want = String(n);
    const opt = Array.from(sel.options).find((o) => o.value === want);
    if (!opt) return { ok: false, reason: '下拉裡找不到 ' + n + ' 首的選項' };

    const before = sel.value;
    if (before === want) return { ok: true, changed: false, value: want };

    const desc = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value');
    if (desc && desc.set) desc.set.call(sel, want); else sel.value = want;
    sel.dispatchEvent(new Event('input', { bubbles: true }));
    sel.dispatchEvent(new Event('change', { bubbles: true }));

    // 等曲目區塊長出來（最多 8 秒）
    let ready = false;
    for (let i = 0; i < 40; i++) {
      await sleep(200);
      if (document.querySelectorAll('input[name^="tracknum_"]').length === n) { ready = true; break; }
    }
    return { ok: true, changed: true, before: before, value: sel.value, ready: ready };
  }

  // ---------- 步驟 1：專輯層 ----------
  async function fillAlbum() {
    const p = state.pkg;
    const done = [];

    // 曲數（先確認表單已開到正確的曲數，逐首欄位才會存在）
    const n = Number(q('#howManySongsOnThisAlbum') && q('#howManySongsOnThisAlbum').value);
    if (n !== (p.tracks || []).length) {
      log('⚠️ 表單曲數是 ' + n + '，上架包是 ' + (p.tracks || []).length + ' 首。', 'warn');
      log('   請先把「Number of songs」選成 ' + (p.tracks || []).length + ' 首再繼續。', 'warn');
      return false;
    }
    done.push('曲數 ' + n);

    // 全部商店
    let stores = 0;
    for (const cb of qa('input[name="store"]')) {
      if (cb.checked) { stores++; continue; }
      cb.click();
      await sleep(CLICK);
      if (cb.checked) stores++;
    }
    done.push('商店 ' + stores + ' 個');

    // 專輯名
    if (setValue(q('#albumTitleInput'), p.album)) done.push('專輯名');
    else log('❌ 專輯名寫入失敗', 'err');

    // 主要風格（用 dsp.genre，不是文案用的 genre）
    const wantGenre = (p.dsp && p.dsp.genre) || p.genre;
    if (setSelect(q('#genrePrimary'), wantGenre)) done.push('風格 ' + wantGenre);
    else log('❌ 找不到風格選項：' + wantGenre, 'err');

    // 語言（曲名是英文，DistroKid 要求與曲名語言一致）
    if (setSelect(q('#language'), 'English')) done.push('語言 English');

    // 封面
    if (state.cover && setFile(q('#artwork'), state.cover)) done.push('封面');
    else if (!state.cover) log('❌ 找不到封面檔', 'err');

    // 首發（不是重發）
    clickRadio(q('input[name^="previouslyReleased_"]'));

    // 全新藝人頁（Spotify / Apple / YouTube Music / IG / FB 都選「還沒有 profile」）
    const newIds = ['spotifyArtistID', 'appleArtistID', 'googleArtistID',
      'instagramProfileArtistID', 'facebookProfileArtistID'];
    for (const nm of newIds) {
      const cands = qa('input[name="' + nm + '"]');
      const pick = cands.find((el) => visible(el) && el.value === 'new' && /zero-matches|First|NoArtistIDFirst/.test(el.id))
        || cands.find((el) => visible(el) && el.value === 'new');
      if (pick) { pick.click(); await sleep(60); }
    }
    done.push('首發/全新藝人頁');

    // 藝人：DistroKid 會自動帶入，程式不碰（使用者指定）
    const hidden = q('#artistName');
    if (hidden && hidden.value) done.push('藝人（系統帶入：' + hidden.value + '）');
    else log('⚠️ 藝人欄位是空的 → 請在畫面上手動選/填「' + p.artist + '」', 'warn');

    log('✅ 專輯層完成：' + done.join('、'), 'ok');
    return true;
  }

  // ---------- 步驟 2：逐首文字欄位 ----------
  async function fillTracks() {
    const p = state.pkg;
    const tmap = trackMap();
    const missing = (p.tracks || []).filter((t) => !tmap[t.no]);
    if (missing.length) {
      log('❌ 表單上找不到第 ' + missing.map((t) => t.no).join(', ') + ' 首的區塊', 'err');
      return false;
    }

    // "WANG, WEI-CHIEH" → last=WANG, first=WEI-CHIEH
    const swRaw = String(p.songwriter || '');
    let swLast = swRaw, swFirst = '';
    if (swRaw.indexOf(',') !== -1) {
      const [a, b] = swRaw.split(',');
      swLast = a.trim(); swFirst = (b || '').trim();
    }

    for (const t of p.tracks || []) {
      const uuid = tmap[t.no];
      const i = t.no;
      const bits = [];

      if (setValue(q('#title_' + CSS.escape(uuid)), t.title)) bits.push('曲名');
      else log('❌ 第 ' + i + ' 首曲名寫入失敗', 'err');

      // 無合作藝人 / 不是其他版本
      clickRadio(q('#js-no-feat-' + i));
      clickRadio(q('#track-' + i + '-version-no'));

      // 原創曲（不是翻唱）
      clickRadio(q('#not_coversong_radio_button_' + i));

      // 作者真名（DistroKid 拆成 first / middle / last）
      const f = q('input[name="songwriter_real_name_first' + i + '"]');
      const l = q('input[name="songwriter_real_name_last' + i + '"]');
      if (f && setValue(f, swFirst)) bits.push('作者名');
      if (l && setValue(l, swLast)) bits.push('作者姓');

      // Instrumental → Explicit → Radio edit。
      // 順序刻意把 Instrumental 放前面：DistroKid 回答「器樂」時會重繪這一列，
      // 若 Explicit 先設就會被重繪重置掉（會出現「不能同時是 explicit 又是 instrumental」的錯）。
      const okInst = await setRadioVerified(q('#js-instrumental-radio-button-' + i), '第 ' + i + ' 首 instrumental');
      const okExp = await setRadioVerified(q('#js-not-explicit-radio-button-' + i), '第 ' + i + ' 首 explicit=No');
      const okCln = await setRadioVerified(q('#js-not-cleaned-radio-button-' + i), '第 ' + i + ' 首 cleaned=No');
      if (okInst) bits.push('器樂');
      if (okExp) bits.push('非 explicit');

      log('  ' + String(i).padStart(2, '0') + ' ' + t.title + '  → ' + bits.join('、') +
        (okInst && okExp && okCln ? '' : '  ⚠️ 有欄位沒設成功'), okInst && okExp && okCln ? null : 'warn');
      await jitter();
    }
    log('✅ 逐首文字欄位完成', 'ok');

    // 填完不等於填對：DistroKid 回答某些題目時會重繪整列、把先前的選擇重置。
    // 所以這裡立刻讀回真實狀態，錯的就地重設（原本的「修復 explicit」按鈕已併進這一步）。
    await verifyTracks();
    return true;
  }

  // ---------- 逐首驗證＋自動修正（explicit / instrumental / cleaned）----------
  // 為什麼要獨立跑一次：DistroKid 回答某些題目時會重繪整個 track 區塊，
  // 「設完」不等於「還在」——explicit 就是這樣壞的（會變成「同時 explicit 又 instrumental」）。
  // 本產線全部是純器樂曲 → instrumental = Yes、explicit = No、cleaned = No，
  // 所以直接讀回狀態，不對的就地重設。
  async function verifyTracks() {
    const tmap = trackMap();
    const tracks = (state.pkg && state.pkg.tracks) || [];
    if (!tracks.length) { log('（沒有上架包資料，跳過逐首驗證）', 'warn'); return { fixed: 0, bad: [] }; }

    let fixed = 0;
    const bad = [];
    for (const t of tracks) {
      const i = t.no;
      if (!tmap[i]) { bad.push(i + '（找不到區塊）'); continue; }
      const want = [
        { el: q('#js-instrumental-radio-button-' + i), name: 'instrumental=Yes' },
        { el: q('#js-not-explicit-radio-button-' + i), name: 'explicit=No' },
        { el: q('#js-not-cleaned-radio-button-' + i), name: 'cleaned=No' },
      ];
      if (!want.some((w) => !w.el || !w.el.checked)) continue;   // 三個都對 → 完全不動

      // 順序：instrumental 先（設它才會觸發重繪，後面兩個要壓在它之後）
      for (const w of want) {
        if (w.el && w.el.checked) continue;
        await setRadioVerified(w.el, '第 ' + i + ' 首 ' + w.name);
      }
      const still = want.filter((w) => !w.el || !w.el.checked).map((w) => w.name);
      if (still.length) {
        bad.push(i + '（' + still.join('/') + '）');
        log('  ' + String(i).padStart(2, '0') + ' ❌ ' + still.join('/') + ' 設不上去', 'err');
      } else {
        fixed++;
        log('  ' + String(i).padStart(2, '0') + ' ✅ 已修正（重繪把設定洗掉了）', 'ok');
      }
      await jitter();
    }

    const ok = tracks.length - bad.length;
    log('逐首欄位驗證（explicit/instrumental/cleaned）：' +
      (bad.length ? '⚠️ ' + ok + '/' + tracks.length + '；失敗 ' + bad.join('、')
        : '✅ ' + tracks.length + '/' + tracks.length + ' 全部正確') +
      (fixed ? '（本次修正 ' + fixed + ' 首）' : ''), bad.length ? 'warn' : 'ok');
    return { fixed, bad };
  }

  // 診斷：dump 每一首的 explicit / cleaned / instrumental / ai_gate 目前狀態
  function radioState() {
    const uuidOf = {};
    qa('input[name^="tracknum_"]').forEach((el) => {
      uuidOf[el.value] = el.getAttribute('name').replace(/^tracknum_/, '');
    });
    const rows = [];
    for (let i = 1; i <= 40; i++) {
      const u = uuidOf[i];
      if (!u) continue;
      const pick = (nm) => {
        const els = qa('input[name="' + nm + '_' + u + '"]');
        if (!els.length) return '不存在';
        const chk = els.find((e) => e.checked);
        if (!chk) return '未選';
        const lab = chk.id ? document.querySelector('label[for="' + CSS.escape(chk.id) + '"]') : null;
        return chk.value + (lab ? '(' + lab.textContent.trim().slice(0, 26) + ')' : '') + '✓';
      };
      rows.push(String(i).padStart(2, '0') +
        '  explicit=' + pick('explicit') +
        '  cleaned=' + pick('cleaned') +
        '  instrumental=' + pick('instrumental') +
        '  ai_gate=' + pick('ai_gate'));
    }
    return rows.join('\n');
  }

  // ---------- 檢查狀態：一次列出「1-8 每一步的結果」 ----------
  // 目的：不用一步按一次、也不用回頭翻 log。按一次就看到哪一步還沒完成、
  // 哪個欄位被 DistroKid 重繪洗掉、以及付費加購有沒有被勾。
  function statusReport() {
    const p = state.pkg;
    const out = [];
    const val = (sel) => { const el = q(sel); return el ? el.value : ''; };

    // ① 專輯／表單
    const formN = Number(val('#howManySongsOnThisAlbum') || 0);
    const pkgN = p ? (p.tracks || []).length : 0;
    out.push('── ① 專輯／表單 ──');
    out.push('  曲數：表單 ' + formN + (p ? '／上架包 ' + pkgN + (formN === pkgN ? ' ✅' : ' ❌') : '（尚未選上架包）'));
    out.push('  專輯名：' + (val('#albumTitleInput') || '（空）'));
    out.push('  風格：' + (val('#genrePrimary') || '（未選）') + '　語言：' + (val('#language') || '（未選）'));
    const art = q('#artwork');
    out.push('  封面：' + (art && art.files && art.files.length ? '✅ ' + art.files[0].name : '❌ 未選'));
    const stores = qa('input[name="store"]');
    out.push('  商店：' + stores.filter((c) => c.checked).length + '/' + stores.length + ' 已勾' +
      (stores.some((c) => !c.checked) ? '（未勾：' + stores.filter((c) => !c.checked).map((c) => labText(c).slice(0, 18)).join('、') + '）' : ' ✅'));

    // ② 逐首欄位
    out.push('── ② 逐首欄位（explicit / cleaned / instrumental）──');
    out.push(radioState() || '  ❌ 找不到曲目區塊');

    if (p) {
      // ③ 音檔
      let files = 0;
      (p.tracks || []).forEach((t) => {
        const el = q('#js-track-upload-' + t.no);
        if (el && el.files && el.files.length) files++;
      });
      out.push('── ③ 音檔 ──');
      out.push('  已塞入 ' + files + '/' + pkgN + (files === pkgN ? ' ✅' : ' ⚠️ 還有 ' + (pkgN - files) + ' 首沒塞'));

      // ④ AI Credits
      const tmap = trackMap();
      const aiRows = [];
      let aiOkCount = 0;
      for (const t of p.tracks || []) {
        const u = tmap[t.no];
        if (!u) continue;
        const s = aiState(u);
        if (aiOk(s)) aiOkCount++;
        aiRows.push(aiLine(t.no, s));
      }
      out.push('── ④ AI Credits（gate=Yes／Music✓／Lyrics✗／AllAudio✗）──');
      out.push('  符合 ' + aiOkCount + '/' + pkgN + (aiOkCount === pkgN ? ' ✅' : ' ⚠️ 按「4. AI Credits」可補'));
      out.push(aiRows.join('\n'));
    }

    // ⑤ Apple credits（有這個區塊才顯示）
    const pRole = q('#track-1-performer-1-role');
    const dRole = q('#track-1-producer-1-role');
    if (pRole || dRole) {
      out.push('── ⑤ Apple credits ──');
      if (pRole) out.push('  performer role=' + pRole.value + (pRole.value === 'Unknown' ? ' ✅' : ''));
      if (dRole) out.push('  producer  role=' + dRole.value + (dRole.value === 'Engineer' ? ' ✅' : ''));
    }

    // ⑥ 必勾聲明
    const mand = sectionBoxes(MANDATORY_HEAD_RE, EXTRAS_HEAD_RE);
    out.push('── ⑥ 必勾聲明 ──');
    if (!mand.boxes.length) {
      out.push('  ❌ 找不到「Important checkboxes (mandatory)」區塊');
    } else {
      mand.boxes.forEach((cb) => {
        const label = (labText(cb) || cb.id || '(無標籤)').replace(/\s+/g, ' ').slice(0, 76);
        out.push('  [' + (cb.checked ? '✓' : '✗') + '] ' + label);
      });
      const miss = mand.boxes.filter((cb) => !cb.checked).length;
      out.push('  ' + (miss ? '⚠️ 還有 ' + miss + ' 條沒勾 → 按「6. 必勾項目全選」' : '全部已勾 ✅'));
    }

    // ⑦ Extras（付費加購）
    const extras = extrasBoxes();
    out.push('── ⑦ Extras（付費加購，應全部不勾）──');
    if (!extras.length) {
      out.push('  ℹ️ 沒抓到加購項目（若畫面上確實有，console 執行 __lofiFill.extrasProbe() 回報）');
    } else {
      extras.forEach((cb) => {
        const label = (labText(cb) || cb.id || '(無標籤)').replace(/\s+/g, ' ').slice(0, 76);
        out.push('  [' + (cb.checked ? '＄已勾' : '✓未勾') + '] ' + label);
      });
      const on = extras.filter((cb) => cb.checked).length;
      out.push('  ' + (on ? '❌ 有 ' + on + ' 個付費加購被勾著 → 按「7. 取消 Extras」' : '全部未勾 ✅'));
    }

    return out.join('\n');
  }

  // ---------- 頁尾「Important checkboxes (mandatory)」全部勾選 ----------
  // 用「區塊標題」定位，再勾該區塊裡每一個 checkbox：
  // 不需要知道個別 id（recon 時這些欄位被 400 筆上限截斷，本來就沒有它們的資料）。
  //
  // ⚠️ 大坑（0.3.9 修掉）：舊版「從標題往上爬，找到含 ≥2 個 checkbox 的容器就停」。
  //    但「Important checkboxes (mandatory)」與「Extras (optional but awesome)」（付費加購）
  //    常常共用同一個外層容器 → 往上爬就把加購一起框進來，按「6. 必勾項目全選」會**勾到要付錢的加購**。
  //
  //    2026-10-17 recon（browser/distrokid-recon-main-12tracks-1792219901787.json）證實：
  //    付費加購是固定特徵 → input[name="extras"].extras（例如 #socialmediapack
  //    "Social Media Pack ($14.95/yr …)"），免費商店則是 input[name="store"]。
  //    同一區塊實際有 6 個加購：Social Media Pack / Leave a Legacy / Discovery Pack /
  //    Store Maximizer / DistroVid / Loudness Normalization（全部標籤都帶價格）。
  //
  // 現在的做法（三重保險）：
  //   ① 用文件順序切「標題 → 下一個標題」之間的 checkbox（不靠容器，天生互不重疊）
  //   ② 用 name="extras" / class="extras" 精準抓出加購（不依賴文案，改版也不會漏）
  //   ③ 勾選時排除加購、勾完再主動全部取消
  const MANDATORY_HEAD_RE = /important checkboxes/i;
  const EXTRAS_HEAD_RE = /extras[\s\S]{0,40}?optional|optional but awesome/i;
  const EXTRAS_SEL = 'input[name="extras"],input.extras';
  const HEAD_SEL = 'h1,h2,h3,h4,h5,h6,legend,[class*="heading"],[class*="title"]';
  const HEAD_TAG_RE = /^(H[1-6]|LEGEND)$/;

  // recon 證實的加購特徵（name="extras" / class="extras"）。這是最可靠的一條，優先使用。
  const extrasByAttr = () => Array.from(document.querySelectorAll(EXTRAS_SEL));

  // 加購的共通文字特徵：標籤含價格（"$14.95/yr"、"$49 one-time fee"…）或已知加購名稱。
  // 為什麼還需要這層：recon 的 400 筆上限只抓到 1 個 checkbox 的 name="extras"，
  // 但同一區塊實際有 6 個加購（Social Media Pack / Leave a Legacy / Discovery Pack /
  // Store Maximizer / DistroVid / Loudness Normalization）。法律聲明不會出現價格，
  // 所以「標籤裡有 $ 金額或加購名稱」是安全的付費訊號，平台加新加購也接得住。
  const PAID_LABEL_RE = /\$\s?\d|social media pack|leave a legacy|discovery pack|store maximizer|distrovid|loudness normalization/i;
  const looksPaid = (cb) => PAID_LABEL_RE.test((labText(cb) || '').replace(/\s+/g, ' '));
  const extrasByLabel = () => Array.from(document.querySelectorAll('input[type="checkbox"]')).filter(looksPaid);

  // 真正的「區塊邊界」只認標籤型標題（h1-h6/legend）或 class 像 section title 的元素；
  // 選項自己的小標題（多半是 div + class 含 title）不算，否則會把區塊切碎。
  const isSectionHeading = (el) => HEAD_TAG_RE.test(el.tagName)
    || /section[-_ ]?(title|heading)|sectionTitle|sectionHeading/i.test(String(el.className || ''));

  function findSectionHeading(re) {
    return Array.from(document.querySelectorAll(HEAD_SEL))
      .find((el) => re.test(el.textContent || '') && el.children.length <= 1) || null;
  }

  // 標題層級：h1→1 … h6→6；legend／class 型標題當成第 2 層（區塊層級）。
  // 用途：往下的「子標題」（例如加購項目自己的 h4）不該把區塊切斷。
  const headLevel = (el) => {
    const m = /^H([1-6])$/.exec(el.tagName);
    return m ? Number(m[1]) : 2;
  };

  // 依文件順序收集「這個標題之後、下一個區塊標題之前」的所有 checkbox。
  // querySelectorAll 回傳的就是文件順序，所以同一容器裡的相鄰區塊也能乾淨切開。
  function boxesAfterHeading(h) {
    const myLevel = headLevel(h);
    const seq = Array.from(document.querySelectorAll(HEAD_SEL + ',input[type="checkbox"]'));
    const start = seq.indexOf(h);
    if (start < 0) return [];
    const out = [];
    for (let i = start + 1; i < seq.length; i++) {
      const el = seq[i];
      if (el.type === 'checkbox') { out.push(el); continue; }
      if (!(el.textContent || '').trim()) continue;
      if (el.closest('label')) continue;         // 選項自己的小標題，不是區塊邊界
      if (!isSectionHeading(el)) continue;
      if (headLevel(el) > myLevel) continue;      // 子標題（如加購項目 h4）不算邊界
      break;                                      // 下一個區塊開始
    }
    return out;
  }

  // 退路：標題後抓不到 checkbox 時，才用「從標題往上找容器」的舊方法。
  // avoidRe：若容器把別的區塊標題也吃進來，就往裡收斂到只包住本標題的子容器。
  function sectionBox(headRe, avoidRe) {
    const h = findSectionHeading(headRe);
    if (!h) return null;
    let box = h;
    for (let k = 0; k < 8 && box.parentElement; k++) {
      box = box.parentElement;
      if (!box.querySelector('input[type="checkbox"]')) continue;
      let cur = box;
      for (let m = 0; m < 6 && avoidRe && avoidRe.test(cur.textContent || ''); m++) {
        const next = Array.from(cur.children)
          .find((ch) => ch.contains(h) && ch.querySelector('input[type="checkbox"]'));
        if (!next) break;
        cur = next;
      }
      return cur;
    }
    return null;
  }

  // 某區塊的 checkbox 清單：優先用順序切法，找不到才退回容器法。
  function sectionBoxes(headRe, avoidRe) {
    const h = findSectionHeading(headRe);
    if (h) {
      const after = boxesAfterHeading(h);
      if (after.length) return { boxes: after, how: 'heading-order', box: null };
    }
    const box = sectionBox(headRe, avoidRe);
    if (box) return { boxes: Array.from(box.querySelectorAll('input[type="checkbox"]')), how: 'container', box };
    return { boxes: [], how: 'none', box: null };
  }

  function mandatorySection() {
    return sectionBox(MANDATORY_HEAD_RE, EXTRAS_HEAD_RE);
  }

  function extrasSection() {
    return sectionBox(EXTRAS_HEAD_RE, MANDATORY_HEAD_RE);
  }

  // Extras 的 checkbox 清單（三路合併，去重）：
  //   ① name="extras" / class="extras"（recon 實測最可靠）
  //   ② 標籤含價格或加購名稱（接住 recon 沒抓到的另外 5 個、以及平台未來新增的加購）
  //   ③ 標題「Extras (optional but awesome)」到下一個區塊之間的 checkbox（文案改版時的退路）
  // ② 只會命中 checkbox 的標籤，法律聲明沒有價格 → 不會誤判。
  function extrasBoxes() {
    const out = [];
    const add = (el) => { if (el && out.indexOf(el) === -1) out.push(el); };
    extrasByAttr().forEach(add);
    extrasByLabel().forEach(add);

    const sec = sectionBoxes(EXTRAS_HEAD_RE, MANDATORY_HEAD_RE);
    if (!(sec.how === 'container' && sec.box && sec.box === mandatorySection())) {
      sec.boxes.forEach(add);
    }
    return out;
  }

  // 診斷：Extras 區塊抓到什麼、每個 checkbox 的標籤與勾選狀態。
  // 換版／改文案後若抓不到，console 執行 __lofiFill.extrasProbe() 把結果貼回來就能修選擇器。
  function extrasProbe() {
    const h = findSectionHeading(EXTRAS_HEAD_RE);
    const sec = sectionBoxes(EXTRAS_HEAD_RE, MANDATORY_HEAD_RE);
    const byAttr = extrasByAttr().length;
    const byLabel = extrasByLabel().length;
    const boxes = extrasBoxes();
    const mand = sectionBoxes(MANDATORY_HEAD_RE, EXTRAS_HEAD_RE).boxes;
    const lines = boxes.map((cb) => {
      const label = (labText(cb) || cb.id || cb.getAttribute('name') || '(無標籤)').replace(/\s+/g, ' ').slice(0, 90);
      return '  [' + (cb.checked ? '✓' : ' ') + '] ' + label +
        '  (name=' + (cb.getAttribute('name') || '-') + ', id=' + (cb.id || '-') + ')';
    });
    if (!h && !byAttr && !byLabel) {
      const near = Array.from(document.querySelectorAll(HEAD_SEL))
        .map((el) => (el.textContent || '').replace(/\s+/g, ' ').trim())
        .filter((t) => /extra|awesome|optional/i.test(t))
        .map((t) => t.slice(0, 70));
      return 'Extras：三個線索都找不到（name="extras"、標籤價格、區塊標題）。頁面疑似標題 ' + JSON.stringify(near) + '（請回報此段 DOM）';
    }
    return 'Extras 定位：name法 ' + byAttr + '、標籤法 ' + byLabel + '、標題法(' + sec.how + ') ' +
      (h ? '「' + (h.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 50) + '」' : '無標題') +
      ' → 去重後 ' + boxes.length + ' 個；必勾區塊 ' + mand.length + ' 個\n' + lines.join('\n');
  }

  // 把 Extras（付費加購）全部取消勾選。只在 Extras 的 checkbox 上動手，不碰必勾區塊。
  async function uncheckExtras() {
    const boxes = extrasBoxes();
    if (!boxes.length) {
      if (!findSectionHeading(EXTRAS_HEAD_RE)) {
        log('ℹ️ 頁面上找不到「Extras (optional but awesome)」標題 → 沒有加購可取消（若畫面確實有，console 執行 __lofiFill.extrasProbe() 回報）', 'warn');
      } else {
        log('ℹ️ 找到 Extras 標題但抓不到 checkbox → 請手動確認（console 執行 __lofiFill.extrasProbe() 回報）', 'warn');
      }
      return false;
    }
    for (const cb of boxes) {
      const was = cb.checked;
      const good = await setCheckboxVerified(cb, false, null);
      const label = (labText(cb) || cb.id || cb.getAttribute('name') || '(無標籤)').replace(/\s+/g, ' ').slice(0, 88);
      log('  [' + (good ? '✓' : '✗') + '] ' + (was ? '已取消勾選' : '本來就沒勾') + '：' + label, good ? 'ok' : 'warn');
      await sleep(90);
    }
    const still = boxes.filter((cb) => cb.checked).length;
    log('Extras（付費加購）' + (still === 0 ? '全部未勾 ✓' : '⚠️ 還有 ' + still + ' 個被勾著 → 請手動取消'),
      still === 0 ? 'ok' : 'err');
    return still === 0;
  }

  async function fillMandatory() {
    const mand = sectionBoxes(MANDATORY_HEAD_RE, EXTRAS_HEAD_RE);
    if (mand.how === 'none') { log('❌ 找不到「Important checkboxes (mandatory)」區塊', 'err'); return false; }

    // ⛔ Extras（付費加購）一律不勾（name="extras" 精準特徵 ＋ 標題區塊雙重防護）
    const extrasSet = new Set(extrasBoxes());
    const boxes = mand.boxes.filter((cb) => !extrasSet.has(cb));
    if (!boxes.length) { log('❌ 找到區塊但沒有可勾的 checkbox（Extras 一律不勾）', 'err'); return false; }
    if (boxes.length !== mand.boxes.length) {
      log('  ⛔ 排除 ' + (mand.boxes.length - boxes.length) + ' 個 Extras checkbox（付費加購，不勾）', 'warn');
    }

    let ok = 0;
    for (const cb of boxes) {
      const good = await setCheckboxVerified(cb, true, null);
      const label = labText(cb) || cb.id || cb.getAttribute('name') || '(無標籤)';
      log('  [' + (good ? '✓' : '✗') + '] ' + label.replace(/\s+/g, ' ').slice(0, 95), good ? 'ok' : 'warn');
      if (good) ok++;
      await sleep(120);
    }
    log('必勾項目 ' + ok + '/' + boxes.length, ok === boxes.length ? 'ok' : 'warn');
    log('⚠️ 這幾條是法律聲明（授權、非抄襲、同意條款）。程式只是幫你打勾——內容請你自己確認過。', 'warn');

    // 最後再掃一次 Extras，確保沒有任何加購被勾起來
    await uncheckExtras();
    return true;
  }

  // ---------- 步驟 3：音檔（最需要單獨觀察的一步）----------
  async function fillAudio() {
    const tmap = trackMap();
    let ok = 0;
    for (const t of state.plan) {
      if (!t.file) { log('  ' + t.no + ' 沒有對應音檔，跳過', 'warn'); continue; }
      const input = q('#js-track-upload-' + t.no);
      if (!input) { log('❌ 找不到第 ' + t.no + ' 首的上傳欄位', 'err'); continue; }
      const good = setFile(input, t.file);
      log('  ' + String(t.no).padStart(2, '0') + ' ' + t.file.name +
        ' (' + (t.file.size / 1048576).toFixed(1) + 'MB) ' + (good ? '已塞入' : '❌ 失敗'), good ? 'ok' : 'err');
      // 上傳是序列的，一首一首來，不要一次丟 12 首
      await jitter(1200, 2500);
      if (good) ok++;
    }
    log('塞入 ' + ok + '/' + state.plan.length + ' 首。請看頁面上每首的進度條是否真的開始跑。', ok === state.plan.length ? 'ok' : 'warn');
    if (ok !== state.plan.length) log('  沒動靜就代表 DistroKid 不接受程式化塞檔，要退回手動拖檔。', 'warn');
    return true;
  }

  // ---------- AI Credits：共用小工具（步驟 4）----------
  // 已確認的組合：ai_gate=Yes、ai_music=✓、ai_lyrics=✗
  //
  // 實測到的流程（recon 證據）：
  //   ai_gate_<uuid> 選 Yes  →  出現 "Edit selections" 按鈕（button.ai-credits-open-modal-btn）
  //   → 點開 SweetAlert2 對話框，裡面才是 ai_lyrics / ai_music 勾選框
  //   → 按 Save（button.swal2-confirm.ai-modal-btn）才寫回表單
  // 所以不能只勾 ai_music 就結束——一定要走完「開對話框 → 勾 → Save」。
  const waitFor = async (fn, timeout, step) => {
    const t0 = Date.now();
    const gap = step || 120;
    while (Date.now() - t0 < (timeout || 3000)) {
      const v = fn();
      if (v) return v;
      await sleep(gap);
    }
    return null;
  };

  // 讀出某首目前的 AI 狀態（一律從 document 讀「最終狀態」）
  function aiState(uuid) {
    const gate = qa('input[name="ai_gate_' + uuid + '"]').find((el) => el.checked);
    const m = q('input[name="ai_music_' + uuid + '"]');
    const l = q('input[name="ai_lyrics_' + uuid + '"]');
    const a = findByLabelText(document, 'All of the audio (performed by AI)');
    return {
      gate: gate ? gate.value : null,
      music: m ? m.checked : null,
      lyrics: l ? l.checked : null,
      allAudio: a ? a.checked : null,
    };
  }

  // 目標：gate=Yes、Music ✓、Lyrics ✗、All of the audio ✗（使用者 2026-10-17 指定不勾）
  function aiOk(s) {
    return s.gate === '1' && s.music === true && s.lyrics === false && s.allAudio !== true;
  }

  const aiLine = (no, s) => '  ' + String(no).padStart(2, '0') +
    ' gate=' + s.gate + ' music=' + s.music + ' lyrics=' + s.lyrics +
    ' allAudio=' + (s.allAudio === true ? 'true ⚠️(應為 false)' : s.allAudio);

  // ---------- 步驟 4：AI Credits ----------
  // 實測流程（2026-10-17 recon）：
  //   在第 1 首按 gate=Yes → 跳出 SweetAlert2 對話框
  //   → 只勾 Music (composed by AI)；Lyrics 與 All of the audio 都「不勾」（使用者指定）
  //   → 勾「Apply these selections to all songs on this release」  ← 關鍵，只做一次
  //   → 按 Save（button.swal2-confirm.ai-modal-btn.ai-modal-btn-save）
  // 所以只處理第 1 首，其餘 11 首由「Apply to all」帶過去；最後仍逐首驗證。
  async function fillAi() {
    const tmap = trackMap();
    const tracks = (state.pkg.tracks || []).filter((t) => tmap[t.no]);
    const total = (state.pkg.tracks || []).length;
    if (!tracks.length) { log('❌ 找不到任何曲目的 AI 欄位', 'err'); return false; }

    const first = tracks[0];
    const uuid = tmap[first.no];

    // 1) 第 1 首 gate = Yes
    const gateYes = qa('input[name="ai_gate_' + uuid + '"]').find((el) => el.value === '1');
    if (!gateYes) { log('❌ 找不到第 ' + first.no + ' 首的 ai_gate', 'err'); return false; }
    await setRadioVerified(gateYes, '第 ' + first.no + ' 首 ai_gate=Yes');
    await jitter(400, 800);

    // 2) 等對話框開（點 Yes 通常會自動開；沒開就找 "Edit selections"）
    let modal = await waitFor(() => {
      const p = document.querySelector('div.swal2-popup, div.ai-modal');
      return p && visible(p) ? p : null;
    }, 2500);
    if (!modal) {
      const openBtn = qa('button.ai-credits-open-modal-btn').find(visible);
      if (openBtn) {
        log('  對話框沒自動開，改按「Edit selections」');
        openBtn.click();
        modal = await waitFor(() => {
          const p = document.querySelector('div.swal2-popup, div.ai-modal');
          return p && visible(p) ? p : null;
        }, 2500);
      }
    }
    if (!modal) { log('❌ 找不到 AI Credits 對話框', 'err'); return false; }

    // 3) 勾選
    const scope = modal;
    const setMusic = await setCheckboxVerified(scope.querySelector('input[name="ai_music_' + uuid + '"]'), true, 'Music (composed by AI)');
    const setLyrics = await setCheckboxVerified(scope.querySelector('input[name="ai_lyrics_' + uuid + '"]'), false, 'Lyrics (written by AI)');
    const noAll = await setCheckboxVerified(findByLabelText(scope, 'All of the audio (performed by AI)'), false, 'All of the audio');
    await setCheckboxVerified(findByLabelText(scope, 'Part of the audio (performed by AI + humans)'), false, null);
    log('  Music=' + (setMusic ? '✓' : '✗') + '  Lyrics=' + (setLyrics ? '✗(取消)' : '✗') +
      '  AllAudio=' + (noAll ? '✗(不勾)' : '⚠️仍被勾著'), noAll ? null : 'warn');

    // 4) Apply these selections to all songs on this release
    const applyAll = findByTextContains(scope, 'Apply these selections to all songs');
    let applied = false;
    if (applyAll) {
      applied = await setCheckboxVerified(applyAll, true, 'Apply to all songs');
      log('  Apply to all songs：' + (applied ? '✓ 已勾' : '✗ 勾不起來'), applied ? 'ok' : 'warn');
    } else {
      log('⚠️ 找不到「Apply these selections to all songs on this release」→ 只會套用到第 1 首。', 'warn');
      log('   請在對話框裡確認那行字實際寫什麼，回報給我。', 'warn');
    }
    await jitter(200, 400);

    // 5) Save
    const save = await waitFor(() => qa('button.swal2-confirm.ai-modal-btn, button.swal2-confirm.ai-modal, button.swal2-confirm')
      .find(visible), 2500);
    if (!save) { log('❌ 找不到 Save 按鈕', 'err'); return false; }
    save.click();
    await waitFor(() => !visible(document.querySelector('div.swal2-popup')), 2500);
    await jitter(400, 700);
    log('✅ 已按 Save', 'ok');

    // 6) 逐首驗證（Apply to all 到底有沒有生效，這裡見真章）
    let ok = 0;
    const bad = [];
    for (const t of tracks) {
      const s = aiState(tmap[t.no]);
      log(aiLine(t.no, s), aiOk(s) ? 'ok' : 'warn');
      if (aiOk(s)) ok++; else bad.push(t.no);
    }
    log('AI Credits 套用結果 ' + ok + '/' + total + ' 首' +
      (ok === total ? '' : '（沒過的：' + bad.join(',') + ' → 按「4」再跑一次會逐首補）'),
      ok === total ? 'ok' : 'warn');

    // 7) Apply to all 沒生效時，退回逐首硬填（保險）
    if (ok < total) {
      log('🔁 Apply to all 沒完全生效，改為逐首補設定…', 'warn');
      for (const no of bad) {
        const u = tmap[no];
        const gy = qa('input[name="ai_gate_' + u + '"]').find((el) => el.value === '1');
        await setRadioVerified(gy, null);
        await jitter(250, 500);
        const popup = document.querySelector('div.swal2-popup');
        if (popup && visible(popup)) {
          const sc = popup;
          await setCheckboxVerified(sc.querySelector('input[name="ai_music_' + u + '"]'), true, null);
          await setCheckboxVerified(findByLabelText(sc, 'All of the audio (performed by AI)'), false, null);
          const sv = qa('button.swal2-confirm.ai-modal-btn, button.swal2-confirm.ai-modal').find(visible);
          if (sv) { sv.click(); await waitFor(() => !visible(document.querySelector('div.swal2-popup')), 2000); }
        } else {
          await setCheckboxVerified(q('input[name="ai_music_' + u + '"]'), true, null);
          await setCheckboxVerified(findByLabelText(document, 'All of the audio (performed by AI)'), false, null);
        }
        await jitter();
      }
      const ok2 = tracks.filter((t) => aiOk(aiState(tmap[t.no]))).length;
      log('逐首補完後：' + ok2 + '/' + total + ' 首', ok2 === total ? 'ok' : 'warn');
    }
    return true;
  }

  // ---------- 步驟 5：Apple credits（performer / producer）----------
  // ⚠️ 這一段還沒驗證：recon 時該區塊是收合的（visible=false），
  //    所以 performer/producer 的「姓名輸入框」長在哪還不確定。這裡用啟發式：
  //    從 role 下拉往回找同一列的文字輸入框。跑完請看 log 確認。

  // 現場勘驗：把 Apple Music credits 那一塊的真實結構 dump 出來。
  // 之所以需要它：`#track-N-performer-1-role` 這些 <select> 是「永遠隱藏」的
  // （被 dk-searchable-select widget 蓋住），所以不能拿它當可見性判斷。
  function creditsProbe() {
    const sel = q('#track-1-performer-1-role');
    const out = { found: !!sel };
    if (sel) {
      out.chain = [];
      let cur = sel;
      for (let i = 0; i < 8 && cur && cur !== document.body; i++) {
        const cs = getComputedStyle(cur);
        out.chain.push({ tag: cur.tagName, cls: String(cur.className || '').slice(0, 70),
          display: cs.display, vis: cs.visibility, h: cur.offsetHeight });
        cur = cur.parentElement;
      }
      out.roleOptions = Array.from(sel.options).map((o) => o.textContent.trim()).slice(0, 25);
      // 往上找到一個夠大的容器，列出裡面所有控制項（含可見性）
      let box = sel;
      for (let i = 0; i < 7 && box.parentElement; i++) {
        box = box.parentElement;
        if (box.querySelectorAll('input,select,button').length > 4) break;
      }
      out.boxCls = String(box.className || '').slice(0, 80);
      out.controls = Array.from(box.querySelectorAll('input,select,button,[class*="searchable"],[contenteditable="true"]'))
        .slice(0, 40).map((el) => ({
          tag: el.tagName, type: el.type || undefined, id: el.id || undefined,
          name: el.getAttribute('name') || undefined,
          cls: String(el.className || '').slice(0, 60), visible: visible(el),
          ph: el.placeholder || undefined, text: (el.innerText || '').trim().slice(0, 30) || undefined,
        }));
    }
    out.linklikes = Array.from(document.querySelectorAll('.linklike'))
      .slice(0, 12).map((e) => ({ text: (e.innerText || '').trim().slice(0, 55), visible: visible(e) }));
    out.requirements = Array.from(document.querySelectorAll('[class*="requirement"]'))
      .slice(0, 30).map((e) => ({ cls: String(e.className || '').slice(0, 70), visible: visible(e),
        text: (e.innerText || '').trim().slice(0, 60) }));
    return out;
  }

  // 找某個 role 下拉所屬的那一列（往上擴張直到出現「非 searchable」的文字輸入框）
  // 注意：不能只數 input[type=text] —— searchable 下拉自己有搜尋框，
  //       那樣會停在 role 那一層，永遠找不到真正的姓名欄。
  function creditRow(sel) {
    const isSearchText = (el) => !!el.closest('[class*="searchable"],[class*="Searchable"],[class*="select__"],[role="combobox"],[role="listbox"]');
    let box = sel;
    for (let k = 0; k < 10 && box.parentElement; k++) {
      box = box.parentElement;
      const realText = Array.from(box.querySelectorAll('input[type="text"],input:not([type])'))
        .filter((el) => !isSearchText(el)).length;
      if (realText >= 1) return box;
      if (box.querySelectorAll('select').length > 2) return box;   // 保險：已經到夠大的區塊
    }
    return sel.parentElement || sel;
  }

  // 點一個「Copy this ... to all tracks」連結，然後處理 Do it / Ok 對話框
  async function copyToAllTracks(re, label) {
    const link = qa('.linklike').filter(visible).find((el) => re.test((el.innerText || '').trim()));
    if (!link) {
      const all = qa('.linklike').filter(visible).map((el) => (el.innerText || '').trim()).filter(Boolean);
      log('  ⚠️ ' + label + '：找不到符合的「Copy ... to all tracks」連結', 'warn');
      log('     畫面上可見的連結有：' + JSON.stringify(all));
      return false;
    }
    log('  按「' + (link.innerText || '').trim() + '」');
    link.click();
    await jitter(400, 700);

    for (let step = 0; step < 3; step++) {
      const popup = await waitFor(() => {
        const p = document.querySelector('div.swal2-popup');
        return p && visible(p) ? p : null;
      }, 1800);
      if (!popup) break;
      const btns = qa('div.swal2-popup button').filter(visible);
      const texts = btns.map((b) => (b.innerText || '').trim());
      log('    對話框按鈕：' + JSON.stringify(texts));
      // 先找 Do it，再找 Ok / OK
      const pick = btns.find((b) => /^do it$/i.test((b.innerText || '').trim()))
        || btns.find((b) => /^(ok|okay)$/i.test((b.innerText || '').trim()))
        || btns.find((b) => /confirm|swal2-confirm/i.test(b.className || ''));
      if (!pick) { log('    ⚠️ 找不到 Do it / Ok', 'warn'); break; }
      pick.click();
      await jitter(300, 600);
    }
    return true;
  }

  // ---------- 步驟 5：Apple credits ----------
  // 規格（使用者指定）：
  //   Performer → role「Unknown」、姓名「WANG, WEI-CHIEH」→ Copy this performer to all tracks
  //   Producer  → role「Engineer」、姓名「WANG, WEI-CHIEH」→ Copy this producer to all tracks
  // ⚠️ 姓名輸入框在 recon 裡被 400 筆上限截掉，所以位置是「從 role 下拉往上找」的啟發式；
  //    跑完請看 log 的 role=/name= 是否都 ✅。

  // 找 performer / producer 的「姓名」輸入框。
  // ⚠️ 大坑：role 那個下拉是 dk-searchable-select，它內部有一個「搜尋用」的文字框，
  //        直接抓 input[type=text] 會抓到它，名字就會被塞進 Role 欄位。
  // 所以這裡「排除」searchable 容器裡的 input，並優先用 id/name 命名規則。
  const SEARCHABLE_SEL = '[class*="searchable"],[class*="Searchable"],[class*="select__"],[role="combobox"],[role="listbox"]';

  function creditNameInput(row, kind /* 'performer' | 'producer' */) {
    // ① 命名規則優先：track-1-performer-1-name
    const byRule = q('#track-1-' + kind + '-1-name')
      || q('input[name="track-1-' + kind + '-1-name"]')
      || q('input[id*="' + kind + '"][id*="name"]');
    if (byRule && !byRule.closest(SEARCHABLE_SEL)) return byRule;

    // ② 在該列裡找，但排除 searchable 的搜尋框
    const cands = Array.from(row.querySelectorAll('input[type="text"],input:not([type]),textarea'))
      .filter((el) => {
        if (el.closest(SEARCHABLE_SEL)) return false;                 // 搜尋式下拉的搜尋框
        if (/search/i.test(String(el.className || ''))) return false;
        if (/search/i.test(el.getAttribute('placeholder') || '')) return false;
        if (/songwriter_real_name/.test(el.getAttribute('name') || '')) return false;
        if (/^track-\d+-(performer|producer)-\d+-role$/.test(el.id || '')) return false;
        return true;
      });
    // 優先選 placeholder / aria-label 有 name 的
    return cands.find((el) => /name/i.test((el.placeholder || '') + ' ' + (el.getAttribute('aria-label') || '')))
      || cands[0] || null;
  }

  // 把被誤填到 searchable 搜尋框裡的文字清掉（修復之前版本造成的污染）
  function clearSearchablePollution(row) {
    let cleared = 0;
    Array.from(row.querySelectorAll('input[type="text"]')).forEach((el) => {
      if (!el.closest(SEARCHABLE_SEL)) return;
      if ((el.value || '').trim() === CREDIT_NAME) { setValue(el, ''); cleared++; }
    });
    return cleared;
  }
  const CREDIT_NAME = 'WANG, WEI-CHIEH';   // 與護照相同

  async function fillAppleCredits() {
    const pRole = q('#track-1-performer-1-role');
    const dRole = q('#track-1-producer-1-role');
    if (!pRole && !dRole) { log('❌ 找不到 performer / producer 欄位', 'err'); return false; }

    const jobs = [];
    if (pRole) jobs.push({ sel: pRole, kind: 'performer', role: 'Unknown',  who: 'Performer', copyRe: /copy this performer to all tracks/i, fallbackRe: null });
    if (dRole) jobs.push({ sel: dRole, kind: 'producer',  role: 'Engineer', who: 'Producer',  copyRe: /copy this producer to all tracks/i,  fallbackRe: null });

    for (const job of jobs) {
      // 1) 先選 role（底層 select 雖然隱藏，setSelect 直接改值 + 送 change 可行）
      const okRole = setSelect(job.sel, job.role);
      await jitter(350, 650);

      // 2) 選完 role 才找姓名框（有些版面選了 role 才會長出姓名欄）
      const row = creditRow(job.sel);
      const messy = clearSearchablePollution(row);
      if (messy) log('    （清掉 ' + messy + ' 個被誤填到 Role 搜尋框的值）', 'warn');

      const nameInput = creditNameInput(row, job.kind);
      const okName = nameInput ? setValue(nameInput, CREDIT_NAME) : false;
      await jitter(250, 500);

      log('  ' + job.who + '  role=' + (okRole ? job.role + '✅' : job.role + '❌') +
        '  name=' + (okName ? CREDIT_NAME + '✅' : '❌ 找不到姓名輸入框'),
        okRole && okName ? 'ok' : 'warn');
      if (!nameInput) {
        const all = Array.from(row.querySelectorAll('input')).map((el) =>
          (el.id || el.getAttribute('name') || '(無id)') + '[' + (el.type || '') + ']' +
          (el.closest(SEARCHABLE_SEL) ? '(searchable)' : ''));
        log('     這一列的所有 input：' + JSON.stringify(all));
      } else if (!okName) {
        log('     找到姓名框但寫不進去：' + (nameInput.id || nameInput.getAttribute('name') || '(無id)'));
      }

      // 複製到全部曲目
      const done = await copyToAllTracks(job.copyRe, job.who)
        || (job.fallbackRe ? await copyToAllTracks(job.fallbackRe, job.who) : false);
      if (!done) log('  ⚠️ ' + job.who + ' 沒有複製到全部曲目', 'warn');
    }

    // 驗證：第 1 首的 role 是否正確
    if (pRole) log('  驗證 performer role=' + pRole.value + (pRole.value === 'Unknown' ? ' ✅' : ' ❌'));
    if (dRole) log('  驗證 producer  role=' + dRole.value + (dRole.value === 'Engineer' ? ' ✅' : ' ❌'));
    return true;
  }

  // ---------- UI ----------
  const panel = document.createElement('div');
  panel.style.cssText = [
    'position:fixed', 'top:12px', 'right:12px', 'z-index:2147483647',
    'width:560px', 'max-height:86vh', 'display:flex', 'flex-direction:column',
    'background:#101418', 'color:#e8edf2', 'font:12px/1.6 ui-monospace,Menlo,monospace',
    'border:1px solid #2a3540', 'border-radius:10px', 'box-shadow:0 12px 40px rgba(0,0,0,.5)',
  ].join(';');
  panel.innerHTML = [
    '<div style="display:flex;align-items:center;gap:8px;padding:8px 10px;background:#161d24;border-bottom:1px solid #2a3540">',
    '  <strong style="flex:1">DistroKid 填表助手 v' + VER + '</strong>',
    '  <button id="dkf-toggle" style="cursor:pointer">收合</button>',
    '</div>',
    '<div id="dkf-body" style="padding:10px;display:flex;flex-direction:column;gap:8px;overflow:auto">',
    '  <div style="color:#ffd479">只填不送：本程式不會按 Continue。</div>',
    '  <div style="display:flex;gap:6px;flex-wrap:wrap">',
    '    <button id="dkf-pick" style="cursor:pointer;font-weight:bold">1. 選取上架包</button>',
    '    <button id="dkf-basic" style="cursor:pointer;font-weight:bold">2. 填專輯層＋逐首</button>',
    '    <button id="dkf-audio">3. 塞音檔</button>',
    '    <button id="dkf-ai">4. AI Credits</button>',
    '    <button id="dkf-credits">5. Apple credits</button>',
    '    <button id="dkf-mandatory" style="cursor:pointer;font-weight:bold">6. 必勾項目全選</button>',
    '    <button id="dkf-extras" style="cursor:pointer;background:#2b3a2f;color:#d7ffe6;border:1px solid #3b7a4c;border-radius:4px">7. 取消 Extras（付費加購）</button>',
    '    <button id="dkf-state" style="cursor:pointer;font-weight:bold">檢查狀態（總檢查）</button>',
    '  </div>',
    '  <textarea id="dkf-preview" readonly placeholder="選取上架包後這裡會顯示讀到什麼" style="width:100%;height:170px;padding:8px;background:#0b0f13;color:#cfe3f5;border:1px solid #2a3540;border-radius:4px;resize:vertical"></textarea>',
    '  <div id="dkf-log" style="height:220px;overflow:auto;background:#0b0f13;border:1px solid #2a3540;border-radius:4px;padding:8px"></div>',
    '</div>',
  ].join('');

  const $ = (id) => panel.querySelector('#' + id);

  async function guard(fn) {
    if (state.busy) { log('⏳ 還在跑，稍等', 'warn'); return; }
    state.busy = true;
    try { await fn(); } catch (e) { log('❌ ' + e.message, 'err'); console.error(e); }
    state.busy = false;
  }

  $('dkf-toggle').onclick = () => {
    const b = $('dkf-body');
    const hidden = b.style.display === 'none';
    b.style.display = hidden ? 'flex' : 'none';
    $('dkf-toggle').textContent = hidden ? '收合' : '展開';
  };

  $('dkf-pick').onclick = () => guard(async () => {
    await pickPackage();
    const n = state.plan.length;

    // 先把曲數對齊，再判斷對不對（順序很重要：曲數一改，曲目區塊會重建）
    if (n > SONG_LIMIT) {
      log('❌ 上架包有 ' + n + ' 首，超過 DistroKid 上限 ' + SONG_LIMIT + ' 首 → 請先拆卷再上架', 'err');
      return;
    }
    const before = Number(q('#howManySongsOnThisAlbum') && q('#howManySongsOnThisAlbum').value);
    if (before && before !== n) {
      if (!formIsEmpty()) {
        log('⚠️ 表單已有內容，不自動改曲數（會洗掉已填的資料）。請手動把 Number of songs 改成 ' + n + '。', 'warn');
      } else {
        log('🔧 表單曲數 ' + before + ' → ' + n + '（上架包是 ' + n + ' 首，自動對齊）');
        const r = await setSongCount(n);
        if (!r.ok) log('❌ 自動改曲數失敗：' + r.reason, 'err');
        else if (!r.ready) log('⚠️ 已改成 ' + n + ' 首，但曲目區塊還沒長齊，稍等一下再按 2。', 'warn');
        else log('✅ 曲數已改為 ' + n + ' 首，曲目區塊已重生。', 'ok');
      }
    }

    $('dkf-preview').value = preview();   // 對齊之後才顯示，才反映最終狀態
    const formN = Number(q('#howManySongsOnThisAlbum') && q('#howManySongsOnThisAlbum').value);
    const wrong = formN && formN !== n;
    log((wrong ? '🛑 ' : '✅ ') + '資料夾「' + state.dirName + '」→ 專輯「' + state.pkg.album + '」' +
      n + ' 首、封面 ' + (state.cover ? '有' : '無') + '、音檔 ' + state.audio.length + ' 個',
      wrong ? 'err' : 'ok');
    if (wrong) {
      log('   ⚠️ 表單仍是 ' + formN + ' 首、上架包是 ' + n + ' 首 — 這一步沒對上，先別按 2。', 'err');
      log('   常見原因：選到同名的舊包（例如修補前的原版，曲數更多）。請確認專輯名與曲數。', 'warn');
    }
  });

  const need = () => {
    if (!state.pkg) { log('請先按「1. 選取上架包」', 'warn'); return false; }
    return true;
  };

  $('dkf-basic').onclick = () => guard(async () => {
    if (!need()) return;
    // 專輯層填失敗就不要往下填逐首（例如曲數沒對上，逐首會全部找不到區塊）
    if (!await fillAlbum()) return;
    await fillTracks();     // 內含 explicit/instrumental/cleaned 驗證與就地修正
  });
  $('dkf-audio').onclick = () => guard(async () => { if (need()) await fillAudio(); });
  $('dkf-ai').onclick = () => guard(async () => { if (need()) await fillAi(); });
  $('dkf-credits').onclick = () => guard(async () => { if (need()) await fillAppleCredits(); });
  $('dkf-mandatory').onclick = () => guard(async () => { await fillMandatory(); });
  $('dkf-extras').onclick = () => guard(async () => { await uncheckExtras(); });
  $('dkf-state').onclick = () => guard(async () => {
    log('──── 總檢查 ────');
    if (state.pkg) await verifyTracks();   // 先驗（只修錯的），再印狀態，數字才是最終值
    log(statusReport());
  });

  window.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key.toLowerCase() === 'f') {
      e.preventDefault(); $('dkf-toggle').onclick();
    }
  }, true);

  document.body.appendChild(panel);
  log('待命。把 Number of songs 選好，然後按「1. 選取上架包」。');

  // 偵錯入口
  window.__lofiFill = {
    version: VER, state,
    pick: pickPackage, album: fillAlbum, tracks: fillTracks,
    audio: fillAudio, ai: fillAi, credits: fillAppleCredits,
    basic: async () => { if (!await fillAlbum()) return false; return fillTracks(); },
    fixExplicit: verifyTracks, verifyTracks, statusReport, radioState, creditsProbe,
    fillMandatory, mandatorySection, setSongCount, formIsEmpty,
    extrasSection, uncheckExtras, extrasProbe,
    trackMap, preview,
    panel: () => { if (!panel.isConnected) document.body.appendChild(panel); return panel; },
  };
})();
