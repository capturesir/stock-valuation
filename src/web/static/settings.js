/* AI 設定頁（R12）。只用 fetch + DOM，與主頁同一設計語言。 */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const status = $("settings-status");

  function setStatus(msg, kind) {
    status.textContent = msg || "";
    status.className = "settings-status" + (kind ? " " + kind : "");
  }

  function fill(data) {
    $("base-url").value = data.base_url || "";
    $("model").value = data.model || "";
    $("api-key").value = "";
    $("api-key").placeholder = data.api_key && data.api_key.set
      ? "留空＝保留現有 Key"
      : "貼上 API Key（本地模型可留空）";
    $("key-state").textContent = "現時：" + ((data.api_key && data.api_key.hint) || "未設定")
      + (data.available ? "；AI 可用" : "；AI 目前不可用（估值會用規則後備）");
    $("env-path").textContent = "設定檔：" + (data.env_path || ".env");
  }

  async function load(showMessage) {
    try {
      const res = await fetch("/api/settings/llm", { headers: { Accept: "application/json" } });
      const data = await res.json();
      if (!res.ok) throw new Error((data.detail && data.detail.error) || "讀取設定失敗");
      fill(data);
      if (showMessage) setStatus("已重新讀取設定。", "ok");
    } catch (err) {
      setStatus(err.message || "讀取設定失敗", "bad");
    }
  }

  $("btn-save").addEventListener("click", async () => {
    const body = {
      base_url: $("base-url").value.trim(),
      model: $("model").value.trim(),
      api_key: $("api-key").value,
    };
    setStatus("儲存中…");
    try {
      const res = await fetch("/api/settings/llm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) throw new Error((data.detail && data.detail.error) || "儲存失敗");
      fill(data);
      setStatus("已儲存並即時生效；下次估值會使用這組設定。", "ok");
    } catch (err) {
      setStatus(err.message || "儲存失敗", "bad");
    }
  });

  $("btn-test").addEventListener("click", async () => {
    setStatus("測試中（會發送一次極小請求）…");
    try {
      const res = await fetch("/api/settings/llm/test", { method: "POST" });
      const data = await res.json();
      if (data.ok) {
        const u = data.usage || {};
        const tok = u.total_tokens != null ? `，用量 ${u.total_tokens} tokens` : "，服務商未回報用量";
        setStatus(`連線成功：${data.model || "模型"}${tok}。`, "ok");
      } else {
        setStatus(`連線失敗：${data.error || "原因不明"}`, "bad");
      }
    } catch (err) {
      setStatus(err.message || "測試失敗", "bad");
    }
  });

  $("btn-reload").addEventListener("click", () => load(true));
  load(false);
})();
