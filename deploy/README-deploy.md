# 部署說明：讓 Web UI 常駐

「點估值出現 Failed to fetch」的**根本原因**是伺服器沒有常駐：它原本由
終端機／Agent session 以子進程啟動，session 結束就被回收，瀏覽器連不上
`127.0.0.1:8765`，JavaScript 只會拋出瀏覽器原生的 `Failed to fetch`。

以下兩種方式任選其一（建議用方式 A）。

---

## 方式 A：macOS launchd 常駐（建議）

特性：登入自動啟動、當機自動重啟（實測約 3 秒）、日誌寫入 `out/web.log`。

### 1. 調整路徑（重要）

`com.erichoi.stock-valuation-web.plist` 內有 3 處絕對路徑，請改成你的實際位置：

```xml
<key>ProgramArguments</key>
<array>
  <string>/Users/erichoi/stock-valuation/.venv/bin/python</string>   <!-- ← 改這裡 -->
  <string>-m</string><string>src.web</string>
</array>
<key>WorkingDirectory</key><string>/Users/erichoi/stock-valuation</string>   <!-- ← 改這裡 -->
<key>StandardOutPath</key><string>/Users/erichoi/stock-valuation/out/web.log</string>  <!-- ← 改這裡 -->
<key>StandardErrorPath</key><string>/Users/erichoi/stock-valuation/out/web.log</string> <!-- ← 改這裡 -->
```

同時把 `Label` 改成你自己的（例如 `com.yourname.stock-valuation-web`）。

### 2. 安裝

```bash
cp deploy/com.erichoi.stock-valuation-web.plist ~/Library/LaunchAgents/
plutil -lint ~/Library/LaunchAgents/com.erichoi.stock-valuation-web.plist   # 檢查語法

launchctl unload ~/Library/LaunchAgents/com.erichoi.stock-valuation-web.plist 2>/dev/null
launchctl load -w ~/Library/LaunchAgents/com.erichoi.stock-valuation-web.plist
```

### 3. 驗證

```bash
launchctl list | grep stock-valuation      # 應看到一列，第二欄為 PID
curl -s http://127.0.0.1:8765/api/health   # 應回 {"ok":true,...}

# 驗證「當機自動重生」
kill -9 $(lsof -tiTCP:8765 -sTCP:LISTEN)
sleep 5
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8765/api/health   # 應回 200
```

### 4. 停用 / 移除

```bash
launchctl unload -w ~/Library/LaunchAgents/com.erichoi.stock-valuation-web.plist
rm ~/Library/LaunchAgents/com.erichoi.stock-valuation-web.plist
```

---

## 方式 B：手動啟停腳本

特性：不需要 launchd，但**不會**在登入後自動啟動，也不會在當機後自動重啟。

```bash
cp deploy/stock-valuation-start ~/.local/bin/
chmod +x ~/.local/bin/stock-valuation-start

stock-valuation-start          # 啟動 / 重啟
stock-valuation-start status   # 查看是否在跑
stock-valuation-start stop     # 停止
```

腳本重點：用 `setsid nohup ... &`（macOS 無 `setsid` 時退回 `nohup ... & disown`）
讓進程脫離呼叫它的 shell，並輪詢 `/api/health` 最多 30 秒確認就緒。

可用環境變數覆寫：

```bash
STOCK_VALUATION_ROOT=/path/to/project VALUATION_WEB_PORT=9000 stock-valuation-start
```

---

## 其他平台的等價做法

| 平台 | 做法 |
|---|---|
| Linux | systemd unit（`Restart=always`），或 `tmux`／`screen` 常駐 |
| Docker | `restart: unless-stopped` |
| 開發者本機 | 用 `uvicorn --reload` 開發，但仍需常駐才不會重現此問題 |

---

## 為什麼不能這樣啟動

```bash
.venv/bin/python -m src.web                 # 前景：終端機關掉就死；工具 timeout 也會砍
nohup .venv/bin/python -m src.web &         # 仍掛在該 shell 的進程樹，session 結束連帶終止
```
