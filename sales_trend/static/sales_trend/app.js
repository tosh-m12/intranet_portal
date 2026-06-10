// 売上推移: サブナビ4構成(全体/F社除く/上海通運移管分込み/顧客別)＋期間トグル(四半期/月別)。
// 縦軸は min0・全構成共通max でサイズ・位置を固定。切替はアニメーションで滑らかに変化。
// 各棒の上に合計(100万元)を縦書きで表示。
(function () {
    "use strict";

    var root = document.querySelector(".sales-trend-app");
    if (!root) return;

    // 構成別ビュー: 各構成で除外する系列(SERIES のキー)。
    var VIEWS = {
        overall:       { exclude: ["grey"] },                 // 全体売上(F含む・グレー除く)
        ex_f:          { exclude: ["faurecia", "grey"] },     // F社除く全体
        with_shanghai: { exclude: ["faurecia"] },             // 上海通運移管分込み(=グレー含む)
    };

    var DATA = null;     // {qtr, month, meta, by_customer:{qtr,month}}
    var chart = null;
    var state = { view: "overall", period: "qtr" };

    var totalLabel = root.dataset.totalLabel || "計";
    var yuan = function (v) { return Math.round(v).toLocaleString("ja-JP") + " 元"; };
    var mm = function (v) { return (v / 1e6).toLocaleString("ja-JP", { minimumFractionDigits: 1, maximumFractionDigits: 1 }); };

    var isCustomer = function () { return state.view === "customer"; };

    // 現在のビュー・期間に対応する datasets(下→上の積み上げ順)。
    function datasets() {
        if (isCustomer()) {
            // 顧客別: Faurecia / 大口各社 / その他。上→下を反転して下→上に積む。
            return DATA.by_customer[state.period].series.slice().reverse().map(function (s) {
                return { label: s.label, data: s.data, backgroundColor: s.color,
                         borderWidth: 0, stack: "s", hidden: false };
            });
        }
        // 構成別: 全系列を常に保持し、除外系列は hidden に(切替を滑らかにアニメーションさせるため)。
        var ex = VIEWS[state.view].exclude;
        var block = DATA[state.period];
        return DATA.meta.slice().reverse().map(function (m) {
            return { label: m.label, data: block.series[m.key], backgroundColor: m.color,
                     borderWidth: 0, stack: "s", hidden: ex.indexOf(m.key) >= 0 };
        });
    }

    // 4構成すべての「期間ごとのバー合計」の最大値 → 縦軸maxを全構成で共通化。
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
        var cust = DATA.by_customer[periodKey];
        cust.labels.forEach(function (_, i) {
            var t = cust.series.reduce(function (s, se) { return s + (se.data[i] || 0); }, 0);
            if (t > max) max = t;
        });
        return max;
    }

    // 各棒の上に合計(100万元)を縦書きで描画(非表示系列は除外)。
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
            ctx.font = (state.period === "month" ? "700 11px" : "700 13px") + " sans-serif";
            c.data.labels.forEach(function (_, i) {
                var total = ds.reduce(function (s, d, idx) {
                    return s + (c.getDatasetMeta(idx).hidden ? 0 : (d.data[i] || 0));
                }, 0);
                if (total <= 0 || !top.data[i]) return;
                var x = top.data[i].x;
                var py = Math.min(y.getPixelForValue(total), y.bottom) - 4;
                ctx.save();
                ctx.translate(x, py);
                ctx.rotate(-Math.PI / 2);
                ctx.textAlign = "left";
                ctx.textBaseline = "middle";
                ctx.fillText(mm(total), 0, 0);
                ctx.restore();
            });
            ctx.restore();
        },
    };

    function render() {
        var labels = (isCustomer() ? DATA.by_customer[state.period] : DATA[state.period]).labels;
        var step = 5e6;
        var yMax = Math.ceil(commonMax(state.period) * 1.06 / step) * step;
        var cfg = {
            type: "bar",
            data: { labels: labels, datasets: datasets() },
            plugins: [totalsPlugin],
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: { duration: 500, easing: "easeOutQuart" },
                interaction: { mode: "index", intersect: false },
                layout: { padding: { top: 8 } },
                scales: {
                    x: { stacked: true, grid: { display: false },
                         ticks: { autoSkip: state.period === "month", autoSkipPadding: 12,
                                  maxRotation: state.period === "month" ? 90 : 55,
                                  minRotation: state.period === "month" ? 90 : 0,
                                  font: { size: 11 } } },
                    // 縦軸: min0固定・max共通(全構成で揃える) → 切替時に再スケールしない。
                    y: { stacked: true, min: 0, max: yMax,
                         ticks: { callback: function (v) { return (v / 1e6).toLocaleString("ja-JP"); } },
                         grid: { color: "#eef0f3" } },
                },
                plugins: {
                    // 凡例は上・右寄せ・横並び。非表示系列は出さない。
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

    // サブナビ(構成切替) — クライアント側のみ、アニメーションで変化。
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

    // 期間トグル
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

    // ハッシュから初期構成を復元
    var byHash = { "#overall": "overall", "#ex-f": "ex_f", "#with-shanghai": "with_shanghai", "#customer": "customer" };
    if (byHash[location.hash]) state.view = byHash[location.hash];

    fetch(root.dataset.apiUrl)
        .then(function (r) { return r.json(); })
        .then(function (d) { DATA = d; activate(state.view); });
})();
