// 売上推移: サブナビで3つの構成(全体/F社除く/上海通運移管分込み)を切替、
// 期間トグルで四半期/月別を切替。3構成は縦軸(min0・共通max)・サイズ・位置を揃え、
// クライアント側切替(リロードなし)でチカチカを防ぐ。各棒に合計(100万元)を表示。
(function () {
    "use strict";

    var root = document.querySelector(".sales-trend-app");
    if (!root) return;

    // 各構成で除外する系列(SERIES のキー)。残りは上→下の積み上げ順のまま。
    var VIEWS = {
        overall:       { exclude: ["grey"] },                 // 全体売上(F含む・グレー除く)
        ex_f:          { exclude: ["faurecia", "grey"] },     // F社除く全体
        with_shanghai: { exclude: ["faurecia"] },             // 上海通運移管分込み(=グレー含む)
    };

    var DATA = null;     // {qtr, month, meta}
    var chart = null;
    var state = { view: "overall", period: "qtr" };

    var totalLabel = root.dataset.totalLabel || "計";
    var yuan = function (v) { return Math.round(v).toLocaleString("ja-JP") + " 元"; };
    var mm = function (v) { return (v / 1e6).toLocaleString("ja-JP", { minimumFractionDigits: 1, maximumFractionDigits: 1 }); };

    function included(viewKey) {
        var ex = VIEWS[viewKey].exclude;
        return DATA.meta.filter(function (m) { return ex.indexOf(m.key) < 0; });
    }

    // Chart.js は datasets[0] を最下段に描くため、上→下の meta を反転して下→上に積む。
    function datasets(block) {
        return included(state.view).slice().reverse().map(function (m) {
            return { label: m.label, data: block.series[m.key],
                     backgroundColor: m.color, borderWidth: 0, stack: "s" };
        });
    }

    // 3構成すべての「期間ごとのバー合計」の最大値 → 縦軸maxを全構成で共通化。
    function commonMax(block) {
        var max = 0;
        Object.keys(VIEWS).forEach(function (vk) {
            var inc = included(vk);
            block.labels.forEach(function (_, i) {
                var t = inc.reduce(function (s, m) { return s + (block.series[m.key][i] || 0); }, 0);
                if (t > max) max = t;
            });
        });
        return max;
    }

    // 各棒の上に合計(100万元)を縦書きで描画。縦書きなら本数が多くても隣と重ならない。
    var totalsPlugin = {
        id: "barTotals",
        afterDatasetsDraw: function (c) {
            var ds = c.data.datasets;
            if (!ds.length) return;
            var ctx = c.ctx, y = c.scales.y;
            var top = c.getDatasetMeta(ds.length - 1);
            var font = state.period === "month" ? "600 9px" : "600 10px";
            ctx.save();
            ctx.fillStyle = "#374151";
            ctx.font = font + " sans-serif";
            c.data.labels.forEach(function (_, i) {
                var total = ds.reduce(function (s, d) { return s + (d.data[i] || 0); }, 0);
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
        var block = DATA[state.period];
        // 縦軸max: 全構成共通の最大値を 500万元 単位に切り上げ(目盛りを整然化＋ラベル余白確保)。
        var step = 5e6;
        var yMax = Math.ceil(commonMax(block) * 1.06 / step) * step;
        var cfg = {
            type: "bar",
            data: { labels: block.labels, datasets: datasets(block) },
            plugins: [totalsPlugin],
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: false,
                interaction: { mode: "index", intersect: false },
                layout: { padding: { top: 18 } },
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
                    // 凡例は下配置。横幅を広く取り、四半期の合計ラベルが重ならないようにする。
                    legend: { position: "bottom", reverse: true,
                              labels: { boxWidth: 12, font: { size: 12 }, padding: 12 } },
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

    // サブナビ(構成切替) — クライアント側のみ。リロードしないのでチカチカしない。
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
    var byHash = { "#overall": "overall", "#ex-f": "ex_f", "#with-shanghai": "with_shanghai" };
    if (byHash[location.hash]) state.view = byHash[location.hash];

    fetch(root.dataset.apiUrl)
        .then(function (r) { return r.json(); })
        .then(function (d) { DATA = d; activate(state.view); });
})();
