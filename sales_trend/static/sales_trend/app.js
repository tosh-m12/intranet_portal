// 売上推移: 1つの積み上げ棒グラフを 期間/Faurecia/グレー のトグルで切替。
(function () {
    "use strict";

    var root = document.querySelector(".sales-trend-app");
    if (!root) return;

    var DATA = null;   // {qtr, month, meta}
    var chart = null;
    var state = { period: "qtr", faurecia: true, grey: false };

    var totalLabel = root.dataset.totalLabel || "計";
    var yen = function (v) { return "¥" + Math.round(v).toLocaleString("ja-JP"); };

    // 積み上げの視覚順は上→下: Faurecia/大口/その他.../グレー(最下段)。
    // Chart.js は datasets[0] を最下段に描くため meta を反転して下→上に積む。
    function datasets(block) {
        var meta = DATA.meta.slice().reverse();  // 下→上
        var out = [];
        meta.forEach(function (m) {
            if (m.key === "faurecia" && !state.faurecia) return;
            if (m.key === "grey" && !state.grey) return;
            out.push({
                label: m.label,
                data: block.series[m.key],
                backgroundColor: m.color,
                borderWidth: 0,
                stack: "s",
            });
        });
        return out;
    }

    function render() {
        var block = DATA[state.period];
        var monthly = state.period === "month";
        var cfg = {
            type: "bar",
            data: { labels: block.labels, datasets: datasets(block) },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: false,
                interaction: { mode: "index", intersect: false },
                scales: {
                    // 月別は本数が多い(～52)ため自動間引き＋縦書き。四半期は全本表示。
                    x: { stacked: true, grid: { display: false },
                         ticks: { autoSkip: monthly, autoSkipPadding: 12,
                                  maxRotation: monthly ? 90 : 55,
                                  minRotation: monthly ? 90 : 0,
                                  font: { size: 11 } } },
                    y: { stacked: true, beginAtZero: true,
                         ticks: { callback: function (v) { return (v / 1e6).toLocaleString("ja-JP"); } },
                         grid: { color: "#eef0f3" } },
                },
                plugins: {
                    legend: { position: "right", reverse: true,
                              labels: { boxWidth: 12, font: { size: 12 } } },
                    tooltip: {
                        callbacks: {
                            label: function (c) { return c.dataset.label + ": " + yen(c.parsed.y); },
                            footer: function (items) {
                                var t = items.reduce(function (s, i) { return s + i.parsed.y; }, 0);
                                return totalLabel + ": " + yen(t);
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

    // セグメント切替
    root.querySelectorAll(".st-seg").forEach(function (seg) {
        var key = seg.dataset.toggle;
        seg.addEventListener("click", function (e) {
            var btn = e.target.closest("button");
            if (!btn) return;
            seg.querySelectorAll("button").forEach(function (b) { b.classList.remove("active"); });
            btn.classList.add("active");
            var val = btn.dataset.val;
            state[key] = (key === "period") ? val : (val === "1");
            render();
        });
    });

    fetch(root.dataset.apiUrl)
        .then(function (r) { return r.json(); })
        .then(function (d) { DATA = d; render(); });
})();
