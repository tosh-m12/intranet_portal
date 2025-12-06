// envmon/static/envmon/envmon_index.js

document.addEventListener("DOMContentLoaded", () => {
    const root = document.getElementById("envmon-root");
    if (!root) {
        return;
    }

    const apiUrl = root.dataset.apiUrl;
    const intervalSeconds = parseInt(root.dataset.interval || "10", 10);

    const sectionsContainer = document.getElementById("warehouse-sections");
    const realtimeSpan = document.getElementById("envmon-realtime-updated");

    if (!apiUrl || !sectionsContainer) {
        console.error("[envmon] apiUrl or sectionsContainer not found.");
        return;
    }

    /**
     * devices 配列を「倉庫ごと」にグルーピングしてテーブルを描画
     * devices の要素例:
     *  { id, name, temperature, humidity, last_seen, online, warehouse, location_id }
     */
    function renderDevices(devices) {
        const warehouseMap = {};

        devices.forEach((d) => {
            const warehouse = d.warehouse;
            // 未割当・未登録は表示しない（サーバー側ロジックと合わせる）
            if (
                !warehouse ||
                warehouse === "未割当" ||
                String(warehouse).startsWith("[未登録:")
            ) {
                return;
            }

            if (!warehouseMap[warehouse]) {
                warehouseMap[warehouse] = [];
            }
            warehouseMap[warehouse].push(d);
        });

        // 中身をクリア
        sectionsContainer.innerHTML = "";

        const warehouses = Object.keys(warehouseMap).sort();

        if (warehouses.length === 0) {
            sectionsContainer.innerHTML =
                "<p>表示対象のデバイスがありません。</p>";
            return;
        }

        warehouses.forEach((warehouse) => {
            const devs = warehouseMap[warehouse];

            const section = document.createElement("section");
            section.className = "envmon-section";

            const h2 = document.createElement("h2");
            h2.textContent = warehouse;
            section.appendChild(h2);

            const table = document.createElement("table");
            table.className = "envmon-table";

            const thead = document.createElement("thead");
            thead.innerHTML = `
                <tr>
                    <th>シリアルナンバー</th>
                    <th>温度 (℃)</th>
                    <th>湿度 (%)</th>
                    <th>最終更新</th>
                    <th>状態</th>
                </tr>
            `;
            table.appendChild(thead);

            const tbody = document.createElement("tbody");

            devs.forEach((d) => {
                const tr = document.createElement("tr");

                const tdSn = document.createElement("td");
                tdSn.textContent = d.id || "";
                tr.appendChild(tdSn);

                const tdTemp = document.createElement("td");
                tdTemp.textContent =
                    d.temperature === "" || d.temperature === null || typeof d.temperature === "undefined"
                        ? ""
                        : d.temperature;
                tr.appendChild(tdTemp);

                const tdHum = document.createElement("td");
                tdHum.textContent =
                    d.humidity === "" || d.humidity === null || typeof d.humidity === "undefined"
                        ? ""
                        : d.humidity;
                tr.appendChild(tdHum);

                const tdLast = document.createElement("td");
                tdLast.textContent = d.last_seen || "";
                tr.appendChild(tdLast);

                const tdOnline = document.createElement("td");
                tdOnline.textContent = d.online ? "●" : "×";
                tr.appendChild(tdOnline);

                tbody.appendChild(tr);
            });

            table.appendChild(tbody);
            section.appendChild(table);
            sectionsContainer.appendChild(section);
        });

        if (realtimeSpan) {
            const now = new Date();
            // ローカルタイム表示（PCのロケール依存）
            realtimeSpan.textContent = now.toLocaleString();
        }
    }

    async function fetchAndUpdate() {
        try {
            const resp = await fetch(apiUrl, {
                cache: "no-store",
            });
            if (!resp.ok) {
                console.error("[envmon] API error:", resp.status, await resp.text());
                return;
            }
            const data = await resp.json();

            if (!Array.isArray(data)) {
                console.error("[envmon] Unexpected API response (not array):", data);
                return;
            }

            renderDevices(data);
        } catch (e) {
            console.error("[envmon] fetch error:", e);
        }
    }

    // 初回即時取得
    fetchAndUpdate();

    // interval 秒ごとに更新
    if (intervalSeconds > 0) {
        setInterval(fetchAndUpdate, intervalSeconds * 1000);
    }
});
