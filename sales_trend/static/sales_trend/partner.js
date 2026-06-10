// ビジネス概要ページの売上推移チャート(四半期/月別トグル・棒の上に合計値)。
(function () {
    "use strict";
    var el = document.getElementById("pt-data");
    var canvas = document.getElementById("ptChart");
    if (!el || !canvas) return;

    var DATA = JSON.parse(el.textContent);   // {qtr:{labels,data}, month:{labels,data}}
    var period = "qtr";
    var chart = null;

    var yuan = function (v) { return Math.round(v).toLocaleString("ja-JP") + " 元"; };
    var mm = function (v) { return (v / 1e6).toLocaleString("ja-JP", { minimumFractionDigits: 1, maximumFractionDigits: 1 }); };

    // 各棒のすぐ上に値(100万元)を横書きで表示。
    var totalsPlugin = {
        id: "ptTotals",
        afterDatasetsDraw: function (c) {
            var ds = c.data.datasets[0];
            if (!ds) return;
            var meta = c.getDatasetMeta(0), y = c.scales.y, ctx = c.ctx;
            ctx.save();
            ctx.fillStyle = "#1f2937";
            ctx.font = (period === "month" ? "700 10px" : "700 12px") + " sans-serif";
            ctx.textAlign = "center";
            ctx.textBaseline = "bottom";
            ds.data.forEach(function (v, i) {
                if (!v || v <= 0 || !meta.data[i]) return;
                ctx.fillText(mm(v), meta.data[i].x, Math.min(y.getPixelForValue(v), y.bottom) - 3);
            });
            ctx.restore();
        },
    };

    function render() {
        var blk = DATA[period] || { labels: [], data: [] };
        var monthly = period === "month";
        var cfg = {
            type: "bar",
            data: { labels: blk.labels,
                    datasets: [{ data: blk.data, backgroundColor: "#3a6ea5", borderWidth: 0 }] },
            plugins: [totalsPlugin],
            options: {
                responsive: true, maintainAspectRatio: false, animation: { duration: 350 },
                layout: { padding: { top: 14 } },
                scales: {
                    x: { grid: { display: false },
                         ticks: { autoSkip: monthly, autoSkipPadding: 12,
                                  maxRotation: monthly ? 90 : 55, minRotation: monthly ? 90 : 0,
                                  font: { size: 11 } } },
                    y: { min: 0,
                         ticks: { callback: function (v) { return (v / 1e6).toLocaleString("ja-JP"); } },
                         grid: { color: "#eef0f3" } },
                },
                plugins: {
                    legend: { display: false },
                    tooltip: { callbacks: { label: function (c2) { return yuan(c2.parsed.y); } } },
                },
            },
        };
        if (chart) { chart.data = cfg.data; chart.options = cfg.options; chart.update(); }
        else { chart = new Chart(canvas.getContext("2d"), cfg); }
    }

    document.querySelectorAll('.st-seg[data-toggle="pt-period"] button').forEach(function (b) {
        b.addEventListener("click", function () {
            document.querySelectorAll('.st-seg[data-toggle="pt-period"] button')
                .forEach(function (x) { x.classList.remove("active"); });
            b.classList.add("active");
            period = b.dataset.val;
            render();
        });
    });

    render();
})();
