'use strict';

/* ===== 一覧の行クリックで詳細へ ===== */
document.addEventListener('click', function (e) {
  var row = e.target.closest('tr.rowlink');
  if (row && row.dataset.href && !e.target.closest('a, button, input, select')) {
    window.location.href = row.dataset.href;
  }
});

/* ===== 入力モード制限(半角英数大文字/数字) — 全フォーム共通 ===== */
(function () {
  // 入力制限ルール: cls=クラス名, bad=不許可文字の検出, clean=整形, msg=吹き出し文言
  var RULES = [
    { cls: 'alnum-up', bad: /[^A-Za-z0-9]/, clean: function (v) { return v.replace(/[^A-Za-z0-9]/g, '').toUpperCase(); }, msg: '英数字のみ' },
    { cls: 'alpha-up', bad: /[^A-Za-z]/, clean: function (v) { return v.replace(/[^A-Za-z]/g, '').toUpperCase(); }, msg: '英字のみ' },
    { cls: 'vessel-up', bad: /[^A-Za-z ]/, clean: function (v) { return v.replace(/[^A-Za-z ]/g, '').toUpperCase(); }, msg: '英字のみ' },
    { cls: 'decimal', bad: /[^0-9.]/, clean: function (v) { return v.replace(/[^0-9.]/g, '').replace(/(\..*)\./g, '$1'); }, msg: '数字のみ' },
    { cls: 'digits', bad: /[^0-9]/, clean: function (v) { return v.replace(/[^0-9]/g, ''); }, msg: '数字のみ' }
  ];

  // 共有の吹き出し1個を対象セルの上に表示してフェードアウト
  var hintEl = null, hintTimer = null;
  function flashHint(el, msg) {
    if (!hintEl) { hintEl = document.createElement('div'); hintEl.className = 'vt-hint'; document.body.appendChild(hintEl); }
    hintEl.textContent = msg;
    var r = el.getBoundingClientRect();
    hintEl.style.left = r.left + 'px';
    hintEl.style.top = (r.top - 26) + 'px';
    hintEl.classList.add('show');
    clearTimeout(hintTimer);
    hintTimer = setTimeout(function () { if (hintEl) hintEl.classList.remove('show'); }, 1100);
  }

  document.addEventListener('input', function (e) {
    var t = e.target; if (!t.classList) return;
    for (var i = 0; i < RULES.length; i++) {
      if (t.classList.contains(RULES[i].cls)) {
        var before = t.value;
        var blocked = RULES[i].bad.test(before)
          || (RULES[i].cls === 'decimal' && (before.split('.').length > 2));
        t.value = RULES[i].clean(before);
        if (blocked) flashHint(t, RULES[i].msg);
        return;
      }
    }
  });
})();

/* ===== 一覧: ライブ絞り込み(入力で即時反映。ボタン不要) ===== */
(function () {
  var form = document.getElementById('filterForm');
  if (!form) return;
  var result = document.getElementById('listResult');
  var action = form.getAttribute('action') || window.location.pathname;
  var timer;
  function query() {
    var parts = [];
    new FormData(form).forEach(function (v, k) {
      if (String(v).trim() !== '') parts.push(encodeURIComponent(k) + '=' + encodeURIComponent(v));
    });
    return parts.join('&');
  }
  function run() {
    var qs = query();
    var url = action + (qs ? '?' + qs : '');
    fetch(url).then(function (r) { return r.text(); }).then(function (html) {
      var doc = new DOMParser().parseFromString(html, 'text/html');
      var fresh = doc.getElementById('listResult');
      if (fresh) result.innerHTML = fresh.innerHTML;
      window.history.replaceState(null, '', url);
    });
  }
  form.addEventListener('input', function (e) {
    if (e.target.type === 'checkbox') { run(); return; }
    clearTimeout(timer); timer = setTimeout(run, 200);
  });
  form.addEventListener('change', function (e) { if (e.target.tagName === 'SELECT') run(); });
  var clr = document.getElementById('clearFilters');
  if (clr) clr.addEventListener('click', function (e) {
    e.preventDefault();
    form.querySelectorAll('input').forEach(function (i) {
      if (i.type === 'checkbox') i.checked = false; else i.value = '';
    });
    form.querySelectorAll('select').forEach(function (s) { s.selectedIndex = 0; });
    run();
  });
})();

/* ===== 簡易入力: 区分=LCL のときコンテナ本数をグレーアウト ===== */
(function () {
  var ct = document.getElementById('qk_ctype');
  var cc = document.getElementById('qk_ccount');
  if (!ct || !cc) return;
  function sync() {
    if (ct.value === 'LCL') { cc.value = ''; cc.disabled = true; }
    else { cc.disabled = false; }
  }
  ct.addEventListener('change', sync);
  sync();
})();

/* ===== 簡易入力: 登録は［登録］ボタンを押した時のみ。Enter では送信しない ===== */
(function () {
  var qf = document.getElementById('quickForm');
  if (!qf) return;
  // 送信はボタン押下時のみ許可(Enterによる暗黙送信を確実に無効化)
  var submitOK = false;
  var btn = qf.querySelector('button[type=submit]');
  if (btn) {
    btn.addEventListener('click', function () { submitOK = true; });
  }
  qf.addEventListener('submit', function (e) {
    if (!submitOK) { e.preventDefault(); }
    submitOK = false;
  });
  // Enterは次の欄へ移動(送信しない)
  var composing = false;
  qf.addEventListener('compositionstart', function () { composing = true; });
  qf.addEventListener('compositionend', function () { composing = false; });
  qf.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter' || composing || e.isComposing || e.keyCode === 229) return;
    var t = e.target;
    if (t.tagName === 'TEXTAREA' || t.type === 'submit' || t.type === 'button') return;
    e.preventDefault();
    var list = Array.prototype.filter.call(
      qf.querySelectorAll('input:not([type=hidden]), select'),
      function (el) { return !el.disabled && el.offsetParent !== null; });
    var i = list.indexOf(t);
    if (i > -1 && i < list.length - 1) list[i + 1].focus();
  });
})();

/* ===== 入力フォーム: IME/Tab/Enter 制御 ===== */
(function () {
  var form = document.getElementById('entryForm');
  if (!form) return;

  /* 日本語入力(IME)変換中フラグ。変換確定 Enter で誤送信・移動しない。 */
  var composing = false;
  form.addEventListener('compositionstart', function () { composing = true; });
  form.addEventListener('compositionend', function () { composing = false; });
  function imeEnter(e) {
    return composing || e.isComposing || e.keyCode === 229;
  }

  function focusables() {
    return Array.prototype.filter.call(
      form.querySelectorAll('input:not([type=hidden]), select, textarea'),
      function (el) { return !el.disabled && el.offsetParent !== null; });
  }
  /* Enter で次の入力欄へ移動(送信しない)。textarea は改行のため除外。 */
  form.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter' || imeEnter(e)) return;
    var t = e.target;
    if (t.tagName !== 'INPUT' || t.type === 'submit' || t.type === 'button') return;
    e.preventDefault();
    var list = focusables();
    var i = list.indexOf(t);
    if (i > -1 && i < list.length - 1) list[i + 1].focus();
    else if (i === list.length - 1) t.blur();
  });
})();
