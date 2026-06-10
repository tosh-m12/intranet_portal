// 売上推移: サブナビ4構成＋期間トグル(四半期/月別)。
// 構成別3種(全体/F社除く/上海通運移管分込み)= 1つの積み上げ棒。縦軸 min0・共通max で揃え、
// 切替はアニメーションで滑らかに変化。各棒の上に合計(100万元)を横書き表示。
// 顧客別 = 顧客ごとに別々の小グラフ(スモールマルチプル)を並べる。
(function () {
    "use strict";

    var root = document.querySelector(".sales-trend-app");
    if (!root) return;

    var VIEWS = {
        overall:       { exclude: ["grey"] },
        ex_f:          { exclude: ["faurecia", "grey"] },
        with_shanghai: { exclude: ["faurecia"] },
    };

    var DATA = null;
    var chart = null;            // 構成別の単一チャート
    var gridCharts = [];         // 顧客別の小チャート群
    var state = { view: "overall", period: "qtr" };

    var chartwrapEl = document.querySelector(".st-chartwrap");
    var gridEl = document.getElementById("stGrid");

    var totalLabel = root.dataset.totalLabel || "計";
    var yuan = function (v) { return Math.round(v).toLocaleString("ja-JP") + " 元"; };
    var mm = function (v) { return (v / 1e6).toLocaleString("ja-JP", { minimumFractionDigits: 1, maximumFractionDigits: 1 }); };
    var escapeHtml = function (s) { return String(s).replace(/[&<>"]/g, function (c) {
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); };

    var isCustomer = function () { return state.view === "customer"; };

    // ============ 構成別(単一の積み上げ棒) ============

    function datasets() {
        // 全系列を常に保持し、除外系列は hidden に(切替を滑らかにアニメーションさせる)。
        var ex = VIEWS[state.view].exclude;
        var block = DATA[state.period];
        return DATA.meta.slice().reverse().map(function (m) {
            return { label: m.label, data: block.series[m.key], backgroundColor: m.color,
                     borderWidth: 0, stack: "s", hidden: ex.indexOf(m.key) >= 0 };
        });
    }

    function commonMax(periodKey) {
        var max = 0;
        var block = DATA[periodKey];
        Object.keys(VIEWS).forEach(function (vk) {
            var ex = VIEWS[vk].exclude;
            block.labels.forEach(function (_, i) {
                var t = DATA.meta.reduce(function (s, m) {
                    return s + (ex.indexOf(m.key) >= 0 ? 0 : (block.series[m.key][i] || 0));
                }, 0);
                if (t > max) max = t;
            });
        });
        return max;
    }

    // 各棒の上に合計(100万元)を横書きで描画(非表示系列は除外)。
    var totalsPlugin = {
        id: "barTotals",
        afterDatasetsDraw: function (c) {
            var ds = c.data.datasets;
            if (!ds.length) return;
            var ctx = c.ctx, y = c.scales.y;
            var topIdx = -1;
            for (var k = ds.length - 1; k >= 0; k--) { if (!c.getDatasetMeta(k).hidden) { topIdx = k; break; } }
            if (topIdx < 0) return;
            var top = c.getDatasetMeta(topIdx);
            ctx.save();
            ctx.fillStyle = "#1f2937";
            ctx.font = (state.period === "month" ? "700 10px" : "700 12px") + " sans-serif";
            ctx.textAlign = "center";
            ctx.textBaseline = "bottom";
            c.data.labels.forEach(function (_, i) {
                var total = ds.reduce(function (s, d, idx) {
                    return s + (c.getDatasetMeta(idx).hidden ? 0 : (d.data[i] || 0));
                }, 0);
                if (total <= 0 || !top.data[i]) return;
                var x = top.data[i].x;
                var py = Math.min(y.getPixelForValue(total), y.bottom) - 3;
                ctx.fillText(mm(total), x, py);
            });
            ctx.restore();
        },
    };

    function renderComposition() {
        var block = DATA[state.period];
        var step = 5e6;
        var yMax = Math.ceil(commonMax(state.period) * 1.06 / step) * step;
        var cfg = {
            type: "bar",
            data: { labels: block.labels, datasets: datasets() },
            plugins: [totalsPlugin],
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: { duration: 500, easing: "easeOutQuart" },
                interaction: { mode: "index", intersect: false },
                layout: { padding: { top: 10 } },
                scales: {
                    x: { stacked: true, grid: { display: false },
                         ticks: { autoSkip: state.period === "month", autoSkipPadding: 12,
                                  maxRotation: state.period === "month" ? 90 : 55,
                                  minRotation: state.period === "month" ? 90 : 0,
                                  font: { size: 11 } } },
                    y: { stacked: true, min: 0, max: yMax,
                         ticks: { callback: function (v) { return (v / 1e6).toLocaleString("ja-JP"); } },
                         grid: { color: "#eef0f3" } },
                },
                plugins: {
                    legend: { position: "top", align: "end", reverse: true,
                              labels: { boxWidth: 12, font: { size: 12 }, padding: 10,
                                        filter: function (item) { return !item.hidden; } } },
                    tooltip: {
                        callbacks: {
                            label: function (c2) { return c2.dataset.label + ": " + yuan(c2.parsed.y); },
                            footer: function (items) {
                                var t = items.reduce(function (s, i) { return s + i.parsed.y; }, 0);
                                return totalLabel + ": " + yuan(t);
                            },
                        },
                    },
                },
            },
        };
        if (chart) {
            chart.data = cfg.data;
            chart.options = cfg.options;
            chart.update();
        } else {
            chart = new Chart(document.getElementById("stChart").getContext("2d"), cfg);
        }
    }

    // ============ 顧客別(顧客ごとの小グラフ) ============

    function clearGrid() {
        gridCharts.forEach(function (c) { c.destroy(); });
        gridCharts = [];
        gridEl.innerHTML = "";
    }

    function renderCustomerGrid() {
        var block = DATA.by_customer[state.period];
        clearGrid();
        block.series.forEach(function (s) {
            var cell = document.createElement("div");
            cell.className = "st-cell";

            var title = document.createElement("div");
            title.className = "st-cell-title";
            title.innerHTML = '<span class="sw" style="background:' + s.color + '"></span>' + escapeHtml(s.label);

            var wrap = document.createElement("div");
            wrap.className = "st-cell-canvas";
            var cv = document.createElement("canvas");
            wrap.appendChild(cv);
            cell.appendChild(title);
            cell.appendChild(wrap);
            gridEl.appendChild(cell);

            // 縦軸は各顧客ごとに自動スケール(min0)。顧客別の推移を見やすくする。
            var ch = new Chart(cv.getContext("2d"), {
                type: "bar",
                data: { labels: block.labels, datasets: [{ data: s.data, backgroundColor: s.color, borderWidth: 0 }] },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    animation: { duration: 400 },
                    plugins: {
                        legend: { display: false },
                        tooltip: { callbacks: { title: function (it) { return it[0].label; },
                                                label: function (c2) { return yuan(c2.parsed.y); } } },
                    },
                    scales: {
                        x: { grid: { display: false },
                             ticks: { autoSkip: true, maxTicksLimit: 6, maxRotation: 0, font: { size: 9 } } },
                        y: { min: 0, ticks: { maxTicksLimit: 4, font: { size: 9 },
                                              callback: function (v) { return (v / 1e6).toLocaleString("ja-JP"); } },
                             grid: { color: "#f0f2f5" } },
                    },
                },
            });
            gridCharts.push(ch);
        });
    }

    // ============ 切替 ============

    function render() {
        if (isCustomer()) {
            chartwrapEl.hidden = true;
            gridEl.hidden = false;
            renderCustomerGrid();
        } else {
            gridEl.hidden = true;
            chartwrapEl.hidden = false;
            if (gridCharts.length) clearGrid();
            renderComposition();
        }
    }

    var tabs = document.querySelectorAll(".st-subnav a");
    function activate(view) {
        state.view = view;
        tabs.forEach(function (a) { a.classList.toggle("active", a.dataset.view === view); });
        if (DATA) render();
    }
    tabs.forEach(function (a) {
        a.addEventListener("click", function (e) {
            e.preventDefault();
            history.replaceState(null, "", a.getAttribute("href"));
            activate(a.dataset.view);
        });
    });

    document.querySelectorAll(".st-seg").forEach(function (seg) {
        seg.addEventListener("click", function (e) {
            var btn = e.target.closest("button");
            if (!btn) return;
            seg.querySelectorAll("button").forEach(function (b) { b.classList.remove("active"); });
            btn.classList.add("active");
            state.period = btn.dataset.val;
            if (DATA) render();
        });
    });

    var byHash = { "#overall": "overall", "#ex-f": "ex_f", "#with-shanghai": "with_shanghai", "#customer": "customer" };
    if (byHash[location.hash]) state.view = byHash[location.hash];

    fetch(root.dataset.apiUrl)
        .then(function (r) { return r.json(); })
        .then(function (d) { DATA = d; activate(state.view); });
})();
