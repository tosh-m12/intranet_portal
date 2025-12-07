// envmon/static/envmon/envmon_index.js

document.addEventListener("DOMContentLoaded", () => {
    const root = document.getElementById("envmon-root");
    if (!root) return;

    console.log("[envmon] envmon_index.js loaded");

    let GLOBAL_TEMP_AXIS_MAX = null;
    const TEMP_AXIS_RANGE = 25;

    const apiUrl = root.dataset.apiUrl;
    const historyUrl = root.dataset.historyUrl;
    const intervalSeconds = parseInt(root.dataset.interval || "10", 10);

    const sectionsContainer = document.getElementById("warehouse-sections");
    const realtimeSpan = document.getElementById("envmon-realtime-updated");

    if (!apiUrl || !historyUrl || !sectionsContainer) {
        console.error("[envmon] missing apiUrl or historyUrl or sectionsContainer");
        return;
    }

    const warehouseCharts = {};   // 倉庫名 → Chart インスタンス
    const warehouseGauges = {};   // 倉庫名 → { setTemp, setHum }

    // ==============================
    // タコメーター風ゲージ コンポーネント
    // ==============================

    const GAUGE_MIN_ANGLE = -130;
    const GAUGE_MAX_ANGLE = 130;

    // container: ゲージを挿入するDOM要素
    // options: { min, max, unit, label, majorStep, minorStep, initialValue }
    function createGauge(container, options) {
        const {
            min,
            max,
            unit,
            label,
            majorStep = 10,
            minorStep = 5,
            initialValue = 0,
        } = options;

        // ===== DOM構築 =====
        const wrapper = document.createElement("div");
        wrapper.className = "envmon-gauge-wrapper";

        const gauge = document.createElement("div");
        gauge.className = "envmon-gauge";

        const scale = document.createElement("div");
        scale.className = "envmon-gauge-scale";

        const mask = document.createElement("div");
        mask.className = "envmon-gauge-mask";

        const vignette = document.createElement("div");
        vignette.className = "envmon-gauge-vignette";

        const ring = document.createElement("div");
        ring.className = "envmon-gauge-ring";

        const ticks = document.createElement("div");
        ticks.className = "envmon-gauge-ticks";

        const needle = document.createElement("div");
        needle.className = "envmon-gauge-needle";

        const cap = document.createElement("div");
        cap.className = "envmon-gauge-cap";

        const valueEl = document.createElement("div");
        valueEl.className = "envmon-gauge-value";

        gauge.appendChild(scale);
        gauge.appendChild(mask);
        gauge.appendChild(vignette);
        gauge.appendChild(ticks);
        gauge.appendChild(needle);
        gauge.appendChild(cap);
        gauge.appendChild(valueEl);
        gauge.appendChild(ring);

        wrapper.appendChild(gauge);

        if (label) {
            const title = document.createElement("div");
            title.className = "envmon-gauge-title";
            title.textContent = label;
            wrapper.appendChild(title);
        }

        container.appendChild(wrapper);

        // ===== 目盛り生成 =====
        const TICKS_RADIUS = 95;
        const LABEL_RADIUS = 78;

        function valueToAngle(v) {
            const ratio = (v - min) / (max - min);
            return GAUGE_MIN_ANGLE + ratio * (GAUGE_MAX_ANGLE - GAUGE_MIN_ANGLE);
        }

        for (let v = min; v <= max; v += minorStep) {
            const angle = valueToAngle(v);

            const tick = document.createElement("div");
            tick.className = "envmon-gauge-tick";
            if (v % majorStep !== 0) {
                tick.classList.add("minor");
            }
            tick.style.transform =
                `rotate(${angle}deg) translate(0, -${TICKS_RADIUS}px)`;
            ticks.appendChild(tick);

            if (v % majorStep === 0) {
                const labelEl = document.createElement("div");
                labelEl.className = "envmon-gauge-label";
                labelEl.textContent = v.toString();
                labelEl.style.transform =
                    `rotate(${angle}deg) translate(0, -${LABEL_RADIUS}px) rotate(${-angle}deg)`;
                ticks.appendChild(labelEl);
            }
        }

        // ===== 値のセット関数 =====
        function setValue(v) {
            if (v == null || isNaN(v)) {
                valueEl.textContent = "--";
                // 初期値は中央に固定（針が大きく動かない）
                needle.style.transform = "rotate(0deg)";
                return;
            }
            const clamped = Math.max(min, Math.min(max, v));
            const angle = valueToAngle(clamped);
            needle.style.transform = `rotate(${angle}deg)`;
            valueEl.textContent = `${clamped.toFixed(1)} ${unit}`;
        }

        // 初期値を反映
        setValue(initialValue);

        return { setValue };
    }

    // ----------------------------
    //  履歴グラフ（7日間）
    // ----------------------------
    function createHistoryChart(ctx, historyData) {
        const labels = historyData.labels || [];

        const datasets = [];
        const devices = Array.isArray(historyData.devices) ? historyData.devices : [];

        if (devices.length > 0) {
            devices.forEach((dev) => {
                datasets.push({
                    label: dev.sn ? `T:${dev.sn}` : "Temp",
                    data: dev.temps || [],
                    yAxisID: "yTemp",
                    tension: 0.2,
                    pointRadius: 0,
                    borderWidth: 2,
                });
                datasets.push({
                    label: dev.sn ? `H:${dev.sn}` : "Hum",
                    data: dev.hums || [],
                    yAxisID: "yHum",
                    tension: 0.2,
                    pointRadius: 0,
                    borderWidth: 2,
                    borderDash: [6, 4],
                });
            });
        } else {
            datasets.push({
                label: "温度(℃)",
                data: historyData.temps || [],
                yAxisID: "yTemp",
                tension: 0.2,
                pointRadius: 0,
                borderWidth: 2,
            });
            datasets.push({
                label: "湿度(%)",
                data: historyData.hums || [],
                yAxisID: "yHum",
                tension: 0.2,
                pointRadius: 0,
                borderWidth: 2,
                borderDash: [6, 4],
            });
        }

        // ★ 温度軸の min/max を決定
        const axisMax = GLOBAL_TEMP_AXIS_MAX != null ? GLOBAL_TEMP_AXIS_MAX : 40;
        const axisMin = axisMax - TEMP_AXIS_RANGE;

        return new Chart(ctx, {
            type: "line",
            data: {
                labels: labels,
                datasets: datasets,
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: {
                    mode: "index",
                    intersect: false,
                },
                plugins: {
                    legend: {
                        display: false,   // 凡例は非表示
                    },
                },
                scales: {
                    x: {
                        offset: false,
                        grid: {
                            color: (ct) => {
                                const idx = ct.tick.value;
                                const label = labels[idx] || "";
                                return label.endsWith("00:00")
                                    ? "rgba(0,0,0,0.2)"   // 00:00 の縦線だけ表示
                                    : "rgba(0,0,0,0)";    // 他は非表示
                            },
                        },
                        ticks: {
                            callback: function (value) {
                                const label = labels[value] || "";
                                if (label.endsWith("00:00")) {
                                    return label.split(" ")[0]; // "12/01 00:00" → "12/01"
                                }
                                return "";
                            },
                            maxRotation: 0,
                            minRotation: 0,
                        },
                    },
                    yTemp: {
                        type: "linear",
                        position: "left",
                        title: {
                            display: true,
                            text: "温度(℃)",
                        },
                        min: axisMin,
                        max: axisMax,
                    },
                    yHum: {
                        type: "linear",
                        position: "right",
                        title: {
                            display: true,
                            text: "湿度(%)",
                        },
                        grid: {
                            drawOnChartArea: false,
                        },
                        min: 20,
                        max: 100,
                    },
                },
            },
        });
    }



    function createWarehouseSection(warehouseName, historyData) {
        const section = document.createElement("section");
        section.className = "warehouse-section";

        const title = document.createElement("h2");
        title.textContent = warehouseName;
        section.appendChild(title);

        const main = document.createElement("div");
        main.className = "warehouse-main";

        // グラフ側
        const chartWrapper = document.createElement("div");
        chartWrapper.className = "chart-wrapper";
        const canvas = document.createElement("canvas");
        chartWrapper.appendChild(canvas);

        // ===== タコメーター側 =====
        const gaugesWrapper = document.createElement("div");
        gaugesWrapper.className = "gauges-wrapper";

        // ★ 左右のボックスを作る
        const tempBox = document.createElement("div");
        tempBox.className = "gauge-box";

        const humBox = document.createElement("div");
        humBox.className = "gauge-box";

        // wrapper に追加
        gaugesWrapper.appendChild(tempBox);
        gaugesWrapper.appendChild(humBox);

        // 履歴の最後の値を初期値にしておく（任意）
        function getLastNonNull(arr) {
            if (!Array.isArray(arr)) return null;
            for (let i = arr.length - 1; i >= 0; i--) {
                const v = arr[i];
                if (v !== null && v !== undefined) return v;
            }
            return null;
        }

        const lastTemp = null;
        const lastHum  = null;

        // ★ createGauge の container を gaugesWrapper → 各 box に変更
        const tempGauge = createGauge(tempBox, {
            min: 0,
            max: 40,
            unit: "℃",
            label: "温度（現在値）",
            majorStep: 5,
            minorStep: 2.5,
            initialValue: lastTemp,
        });

        const humGauge = createGauge(humBox, {
            min: 0,
            max: 100,
            unit: "%",
            label: "湿度（現在値）",
            majorStep: 10,
            minorStep: 5,
            initialValue: lastHum,
        });

        main.appendChild(chartWrapper);
        main.appendChild(gaugesWrapper);

        section.appendChild(main);
        sectionsContainer.appendChild(section);

        const ctx = canvas.getContext("2d");
        const chart = createHistoryChart(ctx, historyData);
        warehouseCharts[warehouseName] = chart;

        warehouseGauges[warehouseName] = {
            setTemp: tempGauge.setValue,
            setHum: humGauge.setValue,
        };
    }


    function renderWarehouses(historyMap) {
        // historyMap: { warehouseName: {labels, temps, hums}, ... }
        sectionsContainer.innerHTML = "";
        Object.keys(warehouseCharts).forEach((key) => {
            const ch = warehouseCharts[key];
            if (ch && typeof ch.destroy === "function") {
                ch.destroy();
            }
        });
        Object.keys(warehouseCharts).forEach((k) => delete warehouseCharts[k]);
        Object.keys(warehouseGauges).forEach((k) => delete warehouseGauges[k]);

        const names = Object.keys(historyMap).sort();
        names.forEach((name) => {
            createWarehouseSection(name, historyMap[name]);
        });

        console.log("[envmon] renderWarehouses:", names);
    }


    // 全倉庫の履歴データから温度軸の最大値を決める
    function computeGlobalTempAxisMax(historyMap) {
        let maxVal = null;

        Object.values(historyMap).forEach((hist) => {
            // 倉庫平均
            if (Array.isArray(hist.temps)) {
                hist.temps.forEach((v) => {
                    if (v != null && !Number.isNaN(v)) {
                        if (maxVal === null || v > maxVal) maxVal = v;
                    }
                });
            }
            // デバイス別
            if (Array.isArray(hist.devices)) {
                hist.devices.forEach((dev) => {
                    if (Array.isArray(dev.temps)) {
                        dev.temps.forEach((v) => {
                            if (v != null && !Number.isNaN(v)) {
                                if (maxVal === null || v > maxVal) maxVal = v;
                            }
                        });
                    }
                });
            }
        });

        if (maxVal === null) {
            return null;
        }
        // 最高温度 + 2℃ を切り上げ
        return Math.ceil(maxVal + 2);
    }


    async function fetchHistoryAndRender() {
        try {
            const resp = await fetch(historyUrl, { cache: "no-store" });
            if (!resp.ok) {
                console.error("[envmon] history_7days error:", resp.status, await resp.text());
                return;
            }
            const historyMap = await resp.json();
            console.log("[envmon] history_7days warehouses:", Object.keys(historyMap));

            // ★ ここで一度だけ温度軸最大値を決定
            if (GLOBAL_TEMP_AXIS_MAX === null) {
                const axisMax = computeGlobalTempAxisMax(historyMap);
                GLOBAL_TEMP_AXIS_MAX = axisMax != null ? axisMax : 40; // フォールバック 40℃
                console.log("[envmon] GLOBAL_TEMP_AXIS_MAX =", GLOBAL_TEMP_AXIS_MAX);
            }

            renderWarehouses(historyMap);
        } catch (e) {
            console.error("[envmon] history_7days fetch error:", e);
        }
    }

    // ----------------------------
    //  リアルタイム平均値 → ゲージ更新
    // ----------------------------

    function computeRealtimeAverages(devices) {
        // devices: data_api の配列
        const acc = {};
        devices.forEach((d) => {
            const wh = d.warehouse;
            if (!wh || wh === "未割当") {
                return;
            }
            if (!acc[wh]) {
                acc[wh] = {
                    tempSum: 0,
                    tempCount: 0,
                    humSum: 0,
                    humCount: 0,
                };
            }

            // 温度
            if (d.temperature !== null && d.temperature !== undefined) {
                const t = typeof d.temperature === "number"
                    ? d.temperature
                    : parseFloat(d.temperature);
                if (!Number.isNaN(t)) {
                    acc[wh].tempSum += t;
                    acc[wh].tempCount += 1;
                }
            }

            // 湿度
            if (d.humidity !== null && d.humidity !== undefined) {
                const h = typeof d.humidity === "number"
                    ? d.humidity
                    : parseFloat(d.humidity);
                if (!Number.isNaN(h)) {
                    acc[wh].humSum += h;
                    acc[wh].humCount += 1;
                }
            }
        });

        const result = {};
        Object.keys(acc).forEach((wh) => {
            const a = acc[wh];
            result[wh] = {
                temp: a.tempCount > 0 ? a.tempSum / a.tempCount : null,
                hum: a.humCount > 0 ? a.humSum / a.humCount : null,
            };
        });
        return result;
    }


    function applyRealtimeToGauges(devices) {
        const averages = computeRealtimeAverages(devices);
        console.log("[envmon] realtime devices:", devices.length);

        Object.keys(warehouseGauges).forEach((wh) => {
            const g = warehouseGauges[wh];
            const avg = averages[wh];

            if (!g) return;

            if (!avg) {
                g.setTemp(null);
                g.setHum(null);
            } else {
                g.setTemp(avg.temp);
                g.setHum(avg.hum);
            }
        });

        if (realtimeSpan) {
            realtimeSpan.textContent = new Date().toLocaleString();
        }
    }


    async function fetchRealtimeAndUpdate() {
        try {
            const resp = await fetch(apiUrl, { cache: "no-store" });
            if (!resp.ok) {
                console.error("[envmon] data_api error:", resp.status, await resp.text());
                return;
            }
            const data = await resp.json();
            if (!Array.isArray(data)) {
                console.error("[envmon] data_api response is not array:", data);
                return;
            }
            applyRealtimeToGauges(data);
        } catch (e) {
            console.error("[envmon] data_api fetch error:", e);
        }
    }

    // ----------------------------
    //  初期化（高速版）
    // ----------------------------
    (async () => {

        // ① キャッシュ（device_cache_latest.json）を高速取得
        let initDevices = [];
        try {
            const resp = await fetch(apiUrl, { cache: "no-store" });
            if (resp.ok) {
                initDevices = await resp.json();
            }
        } catch (e) {
            console.error("[envmon] initial cache load failed:", e);
        }

        // ② グラフ＋ゲージの「器」だけ作る（履歴待ち）
        await fetchHistoryAndRender();

        // ③ キャッシュ値でゲージ初期表示（瞬時に針が動く）
        if (Array.isArray(initDevices)) {
            console.log("[envmon] applying initial cached values:", initDevices.length);
            applyRealtimeToGauges(initDevices);
        }

        // ④ 最初のリアルタイム更新（外部 API ではなく cache_latest.json）
        await fetchRealtimeAndUpdate();

        // ⑤ 次回以降のリアルタイム更新
        if (intervalSeconds > 0) {
            setInterval(fetchRealtimeAndUpdate, intervalSeconds * 1000);
        }
    })();

});
