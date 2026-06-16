'use strict';

/* ===== 一覧の行クリックで編集へ ===== */
document.addEventListener('click', function (e) {
  var row = e.target.closest('tr.rowlink');
  if (row && row.dataset.href && !e.target.closest('a, button, input, select')) {
    // 詳細へ。承認/戻る後に一覧の直前位置(絞り込み+この行)へ戻れるよう back を渡す。
    var back = encodeURIComponent(window.location.pathname + window.location.search);
    var sep = row.dataset.href.indexOf('?') < 0 ? '?' : '&';
    window.location.href = row.dataset.href + sep + 'back=' + back;
  }
});

/* ===== 入力モード制限(半角英数/数字/ISO通貨) — 全フォーム共通 ===== */
(function () {
  function isoCodes() {
    var dl = document.getElementById('dl-currencies');
    return dl ? [].map.call(dl.options, function (o) { return o.value; }) : [];
  }
  // 入力中: 不許可文字を除去(=英数モード相当。日本語等は入らない)
  document.addEventListener('input', function (e) {
    var t = e.target; if (!t.classList) return;
    if (t.classList.contains('alnum-up')) t.value = t.value.replace(/[^A-Za-z0-9]/g, '').toUpperCase();
    else if (t.classList.contains('iso-cur')) t.value = t.value.replace(/[^A-Za-z]/g, '').toUpperCase().slice(0, 3);
    else if (t.classList.contains('digits')) t.value = t.value.replace(/[^0-9]/g, '');
  });
  // ISO通貨: フォーカス時に直前値を退避、外したときISO以外なら元に戻す
  document.addEventListener('focusin', function (e) {
    if (e.target.classList && e.target.classList.contains('iso-cur')) e.target.dataset.prev = e.target.value;
  }, true);
  document.addEventListener('focusout', function (e) {
    var t = e.target; if (!t.classList || !t.classList.contains('iso-cur')) return;
    var v = (t.value || '').toUpperCase(); t.value = v;
    var codes = isoCodes();
    if (codes.length && v !== '' && codes.indexOf(v) < 0) t.value = t.dataset.prev || '';
  }, true);
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
  form.addEventListener('input', function () { clearTimeout(timer); timer = setTimeout(run, 200); });
  form.addEventListener('change', function (e) { if (e.target.tagName === 'SELECT') run(); });
  var clr = document.getElementById('clearFilters');
  if (clr) clr.addEventListener('click', function (e) {
    e.preventDefault();
    form.querySelectorAll('input').forEach(function (i) { i.value = ''; });
    form.querySelectorAll('select').forEach(function (s) { s.selectedIndex = 0; });
    run();
  });
})();

/* ===== 入力フォーム ===== */
(function () {
  var form = document.getElementById('entryForm');
  if (!form) return;

  var API_PARTIES = form.dataset.apiParties;
  var API_CHECK = form.dataset.apiCheck;
  var API_SERIAL = form.dataset.apiSerial;
  var MASTER_ADD = form.dataset.masterAdd;

  /* ---- 数値ユーティリティ ---- */
  function num(el) {
    if (!el) return 0;
    var v = parseFloat(String(el.value).replace(/,/g, '').trim());
    return isNaN(v) ? 0 : v;
  }
  function r2(x) { return Math.round((x + 1e-9) * 100) / 100; }
  function fmt(x) { return r2(x).toLocaleString('ja-JP', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }

  /* ---- 自動計算 ----
     税抜入力: 税込=ROUND(額*(1+率/100),2)
     税込入力: 税抜=ROUND(額/(1+率/100),2)  （逆算）
     金額入力あり・税率空欄 → 警告。 */
  var netAll = document.getElementById('id_net_all');
  var rateWarn = document.getElementById('rateWarn');
  var rateWarnList = document.getElementById('rateWarnList');
  var exEl = document.getElementById('id_exrate');
  var roConverted = document.getElementById('ro_converted');

  function feeLabel(key) {
    var row = form.querySelector('[data-fee-row="' + key + '"] .fee-label');
    return row ? row.textContent.trim() : key;
  }

  function recalc() {
    var before = 0, after = 0, warns = [];
    form.querySelectorAll('.fee-amt').forEach(function (amtEl) {
      var key = amtEl.dataset.fee;
      var rateEl = form.querySelector('.fee-rate[data-fee="' + key + '"]');
      var netEl = form.querySelector('.fee-net-chk[data-fee="' + key + '"]');
      var netMode = !netEl || netEl.checked;          // チェック=税抜入力(既定)。外す=税込入力
      var hasAmt = amtEl.value.trim() !== '';
      var hasRate = rateEl.value.trim() !== '';
      var amt = num(amtEl), rate = num(rateEl);
      var net, incl;
      if (netMode) { net = amt; incl = hasRate ? r2(amt * (1 + rate / 100)) : amt; }
      else { incl = amt; net = hasRate ? r2(amt / (1 + rate / 100)) : amt; }

      var rowWarn = hasAmt && !hasRate;       // 金額あり・税率空欄
      rateEl.classList.toggle('warn-field', rowWarn);
      if (rowWarn) warns.push(feeLabel(key));

      var netOut = form.querySelector('.fee-net[data-fee="' + key + '"]');
      var inclOut = form.querySelector('.fee-incl[data-fee="' + key + '"]');
      if (netOut) netOut.textContent = hasAmt ? fmt(net) : '0.00';
      if (inclOut) inclOut.textContent = hasAmt ? fmt(incl) : '0.00';
      before += net; after += incl;
    });
    document.getElementById('sumBefore').textContent = fmt(before);
    document.getElementById('sumAfter').textContent = fmt(after);
    document.getElementById('sumTax').textContent = fmt(after - before);

    // 換算後金額(自動) = 税込合計 ÷ 為替レート
    if (roConverted) {
      var ex = exEl ? num(exEl) : 0;
      roConverted.textContent = ex ? fmt(after / ex) : '—';
    }

    if (warns.length) { rateWarnList.textContent = warns.join('、'); rateWarn.hidden = false; }
    else { rateWarn.hidden = true; }
    return warns.length;
  }

  /* タイトル行のチェックで全費目を一括オンオフ(チェック=税抜) */
  function syncMaster() {
    if (!netAll) return;
    var chks = form.querySelectorAll('.fee-net-chk');
    var on = 0;
    chks.forEach(function (c) { if (c.checked) on++; });
    netAll.checked = on === chks.length && chks.length > 0;
    netAll.indeterminate = on > 0 && on < chks.length;
  }
  if (netAll) {
    netAll.addEventListener('change', function () {
      form.querySelectorAll('.fee-net-chk').forEach(function (c) { c.checked = netAll.checked; });
      netAll.indeterminate = false;
      recalc();
    });
  }

  form.addEventListener('input', function (e) {
    var t = e.target;
    if (t.classList && (t.classList.contains('fee-amt') || t.classList.contains('fee-rate')) || t.id === 'id_exrate') {
      recalc();
    }
  });

  /* 金額入力欄: フォーカスを外すとカンマ区切り＋小数第2位で表示。編集時は素の数値に戻す。 */
  function fmtAmtField(el) {
    var raw = String(el.value).replace(/,/g, '').trim();
    if (raw === '') return;
    var v = parseFloat(raw);
    if (!isNaN(v)) el.value = fmt(v);   // fmt = 1,234.56 形式
  }
  function rawAmtField(el) { el.value = String(el.value).replace(/,/g, ''); }
  form.addEventListener('focusin', function (e) {
    if (e.target.classList && e.target.classList.contains('fee-amt')) rawAmtField(e.target);
  });
  form.addEventListener('focusout', function (e) {
    if (e.target.classList && e.target.classList.contains('fee-amt')) fmtAmtField(e.target);
  });
  form.querySelectorAll('.fee-amt').forEach(fmtAmtField);   // 初期表示の整形

  form.addEventListener('change', function (e) {
    if (e.target.classList && e.target.classList.contains('fee-net-chk')) {
      syncMaster();
      recalc();
    }
  });
  syncMaster();
  recalc();

  /* 金額あり・税率空欄のまま保存しようとしたら確認 */
  form.addEventListener('submit', function (e) {
    if (recalc() > 0) {
      if (!window.confirm('税率が空欄の費目があります（税額が計算できません）。このまま保存しますか？')) {
        e.preventDefault();
      }
    }
  });

  /* ---- 日本語入力(IME)変換中フラグ ---- */
  var composing = false;
  form.addEventListener('compositionstart', function () { composing = true; });
  form.addEventListener('compositionend', function () { composing = false; });
  function imeEnter(e) {
    // 変換確定の Enter(IME中)はセル確定・移動に使わない
    return composing || e.isComposing || e.keyCode === 229;
  }

  /* ---- Tab/Enter で次の入力欄へ ---- */
  var focusables = function () {
    return Array.prototype.filter.call(
      form.querySelectorAll('input:not([type=hidden]), select, textarea'),
      function (el) { return !el.disabled && el.offsetParent !== null; });
  };
  form.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter' || imeEnter(e)) return;
    var t = e.target;
    if (t.tagName !== 'INPUT' || t.type === 'submit' || t.type === 'button') return;
    // オートコンプリートのドロップダウンが開いている場合はそちらが処理(stopPropagation 済)
    e.preventDefault();
    var list = focusables();
    var i = list.indexOf(t);
    if (i > -1 && i < list.length - 1) list[i + 1].focus();
    else if (i === list.length - 1) t.blur();
  });

  /* ===== オートコンプリート ===== */
  function debounce(fn, ms) {
    var h; return function () { var a = arguments, c = this; clearTimeout(h); h = setTimeout(function () { fn.apply(c, a); }, ms); };
  }
  function esc(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }
  function hl(text, q) {
    if (!q) return text;
    return text.replace(new RegExp('(' + esc(q) + ')', 'i'), '<mark>$1</mark>');
  }

  function attachAC(input, field) {
    if (!input) return;
    var box = null, items = [], active = -1;

    function close() { if (box) { box.remove(); box = null; } items = []; active = -1; }
    function open() {
      close();
      box = document.createElement('div');
      box.className = 'ac-list';
      input.parentNode.appendChild(box);
    }
    function render(q) {
      box.innerHTML = '';
      items.forEach(function (it, idx) {
        var d = document.createElement('div');
        d.className = 'ac-item' + (idx === active ? ' active' : '');
        var sub = it.group ? '<span class="sub">' + it.group + '</span>' : '';
        d.innerHTML = hl(it.label, q) + sub;
        d.addEventListener('mousedown', function (ev) { ev.preventDefault(); choose(idx); });
        box.appendChild(d);
      });
    }
    function choose(idx) {
      var it = items[idx]; if (!it) return;
      input.value = it.value;
      if (field === 'company') {
        // 会社確定 → グループを補完、警告解除(担当者はログイン由来のため補完しない)
        var gc = document.getElementById('id_customer_gc');
        if (gc && it.group) gc.value = it.group;
        hideWarn();
      }
      close();
      // 次の欄へ
      var list = focusables(); var i = list.indexOf(input);
      if (i > -1 && i < list.length - 1) list[i + 1].focus();
    }

    var fetchItems = debounce(function () {
      var q = input.value.trim();
      if (q.length < 1) { close(); return; }
      var url = API_PARTIES + '?q=' + encodeURIComponent(q) + '&field=' + field;
      if (field === 'company') {
        var gc = document.getElementById('id_customer_gc');
        if (gc && gc.value.trim()) url += '&group=' + encodeURIComponent(gc.value.trim());
      }
      fetch(url).then(function (r) { return r.json(); }).then(function (data) {
        items = data.items || [];
        active = items.length ? 0 : -1;
        if (!items.length) { close(); return; }
        if (!box) open();
        render(q);
      });
    }, 140);

    input.addEventListener('input', fetchItems);
    input.addEventListener('keydown', function (e) {
      // 日本語変換中の Enter(等)はオートコンプリート操作に使わない
      if (imeEnter(e)) return;
      if (!box || !items.length) {
        if (field === 'company' && e.key === 'Enter') checkCompany();
        return;
      }
      if (e.key === 'ArrowDown') { e.preventDefault(); active = (active + 1) % items.length; render(input.value.trim()); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); active = (active - 1 + items.length) % items.length; render(input.value.trim()); }
      else if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); choose(active); }
      else if (e.key === 'Escape') { close(); }
    });
    input.addEventListener('blur', function () {
      setTimeout(close, 150);
      if (field === 'company') checkCompany();
    });
  }

  /* ===== 会社名のマスタ存在チェック → 警告 ===== */
  var warn = document.getElementById('companyWarn');
  var warnName = document.getElementById('warnName');
  var warnReg = document.getElementById('warnRegister');
  function hideWarn() { if (warn) warn.hidden = true; }
  function checkCompany() {
    var input = document.getElementById('id_bill_to');
    var name = input.value.trim();
    if (!name) { hideWarn(); return; }
    var gcv0 = (document.getElementById('id_customer_gc').value || '').trim();
    fetch(API_CHECK + '?company=' + encodeURIComponent(name)
          + (gcv0 ? '&group=' + encodeURIComponent(gcv0) : ''))
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.exists) {
          hideWarn();
          var gc = document.getElementById('id_customer_gc');
          if (gc && d.group && !gc.value) gc.value = d.group;
        } else {
          warnName.textContent = name;
          var gcv = (document.getElementById('id_customer_gc').value || '').trim();
          var url = MASTER_ADD + '?company=' + encodeURIComponent(name)
                  + (gcv ? '&group=' + encodeURIComponent(gcv) : '')
                  + '&next=' + encodeURIComponent(window.location.pathname);
          warnReg.href = url;
          warn.hidden = false;
        }
      });
  }

  attachAC(document.getElementById('id_customer_gc'), 'group');
  attachAC(document.getElementById('id_bill_to'), 'company');

  /* ===== 連番(登録時当日YYYYMMDD+4桁)のプレビュー表示(読み取り専用) ===== */
  var roSerial = document.getElementById('ro_serial');
  if (roSerial && roSerial.dataset.auto === '1') {
    fetch(API_SERIAL).then(function (r) { return r.json(); })
      .then(function (j) { roSerial.textContent = j.next; });
  }
})();

/* ===== 取引先マスタ: ヘッダクリックで並べ替え(▲▼) ===== */
(function () {
  document.querySelectorAll('table.sort-table').forEach(function (table) {
    if (!table.tHead || !table.tBodies.length) return;
    var ths = [].slice.call(table.tHead.rows[0].cells);
    var tbody = table.tBodies[0];
    var st = { col: -1, dir: 1 };
    var collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });
    ths.forEach(function (th, idx) {
      if (!th.classList.contains('sortable')) return;
      th.addEventListener('click', function () {
        st.dir = (st.col === idx) ? -st.dir : 1;
        st.col = idx;
        var rows = [].slice.call(tbody.rows).filter(function (r) {
          return r.cells.length && !r.querySelector('td[colspan]');
        });
        function cellVal(cell) {
          if (!cell) return '';
          var ctl = cell.querySelector('textarea, input');
          return (ctl ? ctl.value : cell.textContent).trim();
        }
        rows.sort(function (a, b) {
          var av = cellVal(a.cells[idx]), bv = cellVal(b.cells[idx]);
          if (!av && bv) return 1;            // 空欄は末尾
          if (av && !bv) return -1;
          return collator.compare(av, bv) * st.dir;
        });
        rows.forEach(function (r) { tbody.appendChild(r); });
        ths.forEach(function (h) { h.classList.remove('sort-asc', 'sort-desc'); });
        th.classList.add(st.dir > 0 ? 'sort-asc' : 'sort-desc');
      });
    });
  });
})();

/* ===== 取引先マスタ: 業務概要のインライン編集(自動保存) ===== */
(function () {
  var root = document.querySelector('.billing-app[data-summary-url]');
  if (!root) return;
  var urlTmpl = root.dataset.summaryUrl;   // .../master/0/summary/
  function tokenEl() { return root.querySelector('input[name=csrfmiddlewaretoken]'); }

  function autogrow(el) { el.style.height = 'auto'; el.style.height = (el.scrollHeight + 2) + 'px'; }
  function growAll() { document.querySelectorAll('.biz-edit').forEach(autogrow); }
  growAll();

  document.addEventListener('focusin', function (e) {
    if (e.target.classList && e.target.classList.contains('biz-edit')) e.target.dataset.orig = e.target.value;
  });
  document.addEventListener('input', function (e) {
    if (e.target.classList && e.target.classList.contains('biz-edit')) autogrow(e.target);
  });
  document.addEventListener('focusout', function (e) {
    var t = e.target;
    if (!t.classList || !t.classList.contains('biz-edit')) return;
    if (t.value === (t.dataset.orig || '')) return;          // 変更なしは送らない
    var tok = tokenEl();
    var body = new URLSearchParams();
    body.set('business_summary', t.value);
    fetch(urlTmpl.replace('/0/', '/' + t.dataset.pk + '/'), {
      method: 'POST',
      headers: { 'X-CSRFToken': tok ? tok.value : '', 'Content-Type': 'application/x-www-form-urlencoded' },
      body: body.toString(),
    }).then(function (r) {
      if (!r.ok) throw 0;
      t.dataset.orig = t.value;
      var flag = t.parentNode.querySelector('.biz-saved');
      if (flag) { flag.classList.add('show'); setTimeout(function () { flag.classList.remove('show'); }, 1200); }
    }).catch(function () {
      t.classList.add('biz-err'); setTimeout(function () { t.classList.remove('biz-err'); }, 1500);
    });
  });
})();
