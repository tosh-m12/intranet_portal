// cs_tasks/board.js : クローズ・トグル（スライドスイッチ）の AJAX 処理
(function () {
    function getCookie(name) {
        const m = document.cookie.match("(^|;)\\s*" + name + "\\s*=\\s*([^;]+)");
        return m ? m.pop() : "";
    }

    // readonly のままフォーカス（オートフィル抑止）→ 次tickで解除（IME を壊さない）
    function focusSoft(el) {
        if (!el) return;
        el.focus();
        setTimeout(function () { el.removeAttribute("readonly"); }, 0);
    }

    function applyClass(rows, cls, on) {
        rows.forEach(function (r) {
            r.classList.toggle(cls, on);
        });
    }

    document.addEventListener("change", function (e) {
        const input = e.target;
        if (!input.classList || !input.classList.contains("close-toggle")) return;

        const url = input.dataset.url;
        if (!url) return;

        const desired = input.checked;
        input.disabled = true;

        fetch(url, {
            method: "POST",
            headers: {
                "X-Requested-With": "XMLHttpRequest",
                "X-CSRFToken": getCookie("csrftoken"),
            },
        })
            .then(function (res) {
                if (!res.ok) throw new Error("request failed");
                return res.json();
            })
            .then(function (data) {
                if (!data.ok) {
                    input.checked = !desired; // revert
                    alert(data.error || "操作に失敗しました。");
                    return;
                }
                const closed = data.is_closed;
                input.checked = closed;

                if (input.classList.contains("task-toggle")) {
                    // 課題全体 → その課題の全行（タイトル＋ぶら下がる進捗）に task-closed。
                    // 他の課題・担当/顧客セルには影響させない（CSS 側で除外）。
                    const taskId = input.dataset.taskId;
                    const rows = document.querySelectorAll(
                        'tr[data-task-id="' + taskId + '"]'
                    );
                    applyClass(Array.prototype.slice.call(rows), "task-closed", closed);

                    // 配下の進捗スイッチも連動（サーバ側でも is_closed を更新済み）
                    document
                        .querySelectorAll(
                            'tr[data-task-id="' + taskId + '"] input.progress-toggle'
                        )
                        .forEach(function (child) {
                            child.checked = closed;
                            const crow = child.closest("tr");
                            if (crow) crow.classList.toggle("prog-closed", closed);
                        });
                } else {
                    // 進捗（複数コメント行に跨る）→ その進捗の全行に prog-closed
                    const pid = input.dataset.progressId;
                    const rows = document.querySelectorAll(
                        'tr[data-progress-id="' + pid + '"]'
                    );
                    applyClass(Array.prototype.slice.call(rows), "prog-closed", closed);
                }
            })
            .catch(function () {
                input.checked = !desired; // revert
                alert("通信エラーが発生しました。");
            })
            .finally(function () {
                input.disabled = false;
            });
    });

    // ===== 顧客列ホバーで重複顧客名をうっすら表示 =====
    function inClientCell(node) {
        return node && node.closest && node.closest("td.col-client");
    }
    document.addEventListener("mouseover", function (e) {
        if (inClientCell(e.target)) {
            const table = document.querySelector(".cs-board");
            if (table) table.classList.add("show-client-dups");
        }
    });
    document.addEventListener("mouseout", function (e) {
        if (inClientCell(e.target) && !inClientCell(e.relatedTarget)) {
            const table = document.querySelector(".cs-board");
            if (table) table.classList.remove("show-client-dups");
        }
    });

    // ===== 進捗・コメント入力欄（textarea）の高さ自動調整 =====
    function autoGrow(el) {
        el.style.height = "auto";
        el.style.height = el.scrollHeight + "px";
    }

    document.addEventListener("input", function (e) {
        const el = e.target;
        if (el.classList && el.classList.contains("add-input") && el.tagName === "TEXTAREA") {
            autoGrow(el);
        }
    });

    function submitForm(el) {
        if (!el.form) return;
        if (el.form.requestSubmit) el.form.requestSubmit();
        else el.form.submit();
    }

    function ntWarn(show, msg) {
        const w = document.getElementById("nt-warning");
        if (!w) return;
        if (show) { w.textContent = msg; w.style.display = ""; }
        else { w.style.display = "none"; }
    }

    // 新規課題の確定処理。両方入力済み→送信、片方だけ→警告、両方空→何もしない。
    function commitNewTask() {
        const client = document.querySelector('.new-task-row input[name="cs_cust"]');
        const title = document.querySelector('.new-task-row input[name="cs_subj"]');
        if (!client || !title) return;
        const cv = client.value.trim();
        const tv = title.value.trim();

        if (cv === "" && tv === "") {
            ntWarn(false);
            return;
        }
        if (cv === "" || tv === "") {
            ntWarn(true, "顧客名と課題の両方を入力してください。");
            focusSoft(cv === "" ? client : title);
            return;
        }
        ntWarn(false);
        const form = document.getElementById("ntf");
        if (form) {
            if (form.requestSubmit) form.requestSubmit();
            else form.submit();
        }
    }

    // Enter で送信 / Shift+Enter で改行（textarea のみ改行可）
    document.addEventListener("keydown", function (e) {
        const el = e.target;
        if (!el.classList || !el.classList.contains("add-input")) return;
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            const newRow = el.closest(".new-task-row");
            if (newRow) {
                if (el.name === "cs_cust") {
                    // 顧客名 Enter → 課題欄へフォーカス移動（オートフィル抑止＋IME維持）
                    focusSoft(newRow.querySelector('input[name="cs_subj"]'));
                } else {
                    // 課題 Enter → 確定（両方検証）
                    commitNewTask();
                }
                return;
            }
            if (el.value.trim() !== "") submitForm(el);
        }
    });

    // フォーカス時に元の値を保持（変更検知用）
    document.addEventListener("focusin", function (e) {
        const el = e.target;
        if (el.classList && el.classList.contains("add-input")) {
            el.dataset.orig = el.value;
        }
    });

    // 欄外クリック（blur）でも確定。
    document.addEventListener("focusout", function (e) {
        const el = e.target;
        if (!el.classList || !el.classList.contains("add-input")) return;

        // 新規課題行の input（顧客名・課題）：行外へフォーカスが出たら確定処理。
        // 顧客→課題と行内で移動している間は確定しない。
        const newRow = el.closest(".new-task-row");
        if (newRow) {
            const next = e.relatedTarget;
            if (next && newRow.contains(next)) return; // 行内移動中
            commitNewTask();
            return;
        }

        // 既存の進捗・コメント（textarea）／顧客名・課題のその場編集（input）：
        // 内容が変わっていれば確定。
        const v = el.value;
        if (v.trim() !== "" && v !== (el.dataset.orig || "")) {
            submitForm(el);
        }
    });

    // 初期表示時に既存の textarea を一度フィット
    document.addEventListener("DOMContentLoaded", function () {
        document.querySelectorAll("textarea.add-input").forEach(autoGrow);

        // ===== ブラウザのオートフィル抑止（顧客名・課題） =====
        // 読み込み時に readonly にしておくとオートフィル対象から外れる。
        // ユーザーが実際に操作したときだけ readonly を解除する。
        document
            .querySelectorAll('input[name="cs_cust"], input[name="cs_subj"]')
            .forEach(function (inp) {
                inp.setAttribute("readonly", "readonly");
                function unlock() { inp.removeAttribute("readonly"); }
                // focus では解除しない（プログラム的フォーカス時のオートフィル誘発を防ぐ）。
                // 実際のユーザー操作（クリック/キー入力）でのみ解除する。
                inp.addEventListener("pointerdown", unlock);
                inp.addEventListener("keydown", unlock);
            });

        // ===== 課題のインライン追加行（罫線付き）の表示切替 =====
        const showBtn = document.getElementById("show-new-task");
        const newRows = document.querySelectorAll(".new-task-row");
        if (showBtn && newRows.length) {
            showBtn.addEventListener("click", function () {
                const willShow = newRows[0].style.display === "none";
                newRows.forEach(function (r) {
                    r.style.display = willShow ? "" : "none";
                });
                ntWarn(false);
                if (willShow) {
                    const clientInput = document.querySelector(
                        '.new-task-row input[name="cs_cust"]'
                    );
                    if (clientInput) {
                        clientInput.scrollIntoView({ behavior: "smooth", block: "center" });
                        focusSoft(clientInput);
                    }
                }
            });
        }
    });
})();
