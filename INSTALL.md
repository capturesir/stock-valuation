# 安裝說明（v2.0.0-alpha.4）

兩種方式：

- **A. Docker（建議）**：一條指令啟動整套系統，Windows、macOS、Linux 步驟相同。
- **B. 本機開發**：不用 Docker，適合修改程式碼。

> 本系統產出不構成投資建議。

---

## A. Docker 安裝

### A1. 需要甚麼

| 項目 | 版本 | 說明 |
| --- | --- | --- |
| Docker Desktop（Windows／macOS）或 Docker Engine＋Compose 外掛（Linux） | Compose v2 以上 | <https://docs.docker.com/get-docker/> |
| 記憶體 | 4 GB 以上可用 | PostgreSQL、Redis、API、worker、網頁五個容器 |
| 網絡 | 可連 Yahoo Finance 等資料源 | 估值時抓取市價與財報 |

### A2. 第一次啟動

```bash
# 1. 解壓縮（或 git clone），進入資料夾
cd stock-valuation

# 2. 建立設定檔
cp .env.example .env            # Windows PowerShell：Copy-Item .env.example .env

# 3. 產生加密金鑰，貼到 .env 的 APP_SECRET_KEY=
python -c "import secrets; print(secrets.token_urlsafe(32))"
#    沒有 Python 時：openssl rand -base64 32

# 4. 同時把 .env 的 POSTGRES_PASSWORD 改成你自己的密碼

# 5. 建置並啟動（第一次約 3–6 分鐘）
docker compose up -d --build

# 6. 確認五個服務都在運行
docker compose ps
```

建立第一個管理員帳號（只需做一次；密碼會在終端機詢問）：

```bash
docker compose exec api python -m backend.app.cli create-user --email you@example.com --name 你的名字 --role admin
```

打開 <http://localhost:8080>，以這個帳號登入。

### A3. 開始使用

0. **新增其他用戶：** 管理員在右上角「系統管理」新增用戶，可設為「管理員」或「一般用戶」；同一頁可設定系統名稱與 Logo。每個用戶有自己的觀察名單；估值紀錄、市場與財報數據全系統共享。
1. **（美股財報，管理員）設定 SEC 聯絡資料：** 「系統管理」→「數據來源與財報」→ 分別填寫「機構名稱」與「聯絡電郵」（SEC 要求兩項都要有，只會發送給 SEC）→「保存」，然後輸入代號（例如 `NVDA`）→「取得財報數據」→ 按「查看財報數據」，或在股票頁的「財報」分頁查看每個申報數字及其 SEC 原文。設定好 SEC 聯絡資料後，任何用戶打開美股的「財報」分頁都會自動取得，系統也會每日檢查新申報。
2. **（可選）設定 AI（管理員）：** 右上角「AI 與費用」→ 選服務商 → 貼上你的 API 金鑰 →「測試連線」→「保存」。不設定也能估值，系統會使用規則後備。
3. **加入股票：** 在「觀察名單」輸入代號（例如 `0883.HK`、`NVDA`）。
4. **估值：** 打開股票 →「重新估值」→ 查看 AI 工作清單與預估費用 →「開始估值」。
5. 以前用 v1 做過的估值（`out/` 資料夾）會在第一次啟動時自動匯入，走勢圖從第一天起就有歷史。

### A4. 在手機上使用（PWA）

- 同一網絡內：把 `.env` 的 `WEB_BIND` 改成 `0.0.0.0`，然後 `docker compose up -d`，手機瀏覽器打開 `http://<電腦 IP>:8080`，用自己的帳號登入。
- `APP_API_TOKEN` 只給腳本或排程程式使用（以 `Authorization: Bearer <令牌>` 呼叫 API，具管理員權限），一般使用不需要設定。
- 安裝到主畫面需要 HTTPS（`localhost` 除外）。有網域名稱時，把 `SITE_ADDRESS` 設為該網域，並把 `WEB_PORT=80`、`WEB_HTTPS_PORT=443`、`WEB_BIND=0.0.0.0`；Caddy 會自動申請及續期憑證。

### A5. 使用本地模型（免費）

在電腦上執行 Ollama 或 LM Studio，然後在「AI 與費用」選「本地模型」：位址預設 `http://host.docker.internal:11434/v1`，模型填你已下載的名稱（例如 `qwen3:8b`）。本地模型的費用一律為 0。

### A6. 更新到新版本

```bash
git pull                         # 或用新的壓縮檔覆蓋（保留 .env）
docker compose up -d --build     # 資料庫結構會在啟動時自動升級
```

### A6b. 市場數據完整性

每日更新後，系統按各交易所的交易日曆檢查每隻證券有沒有缺失的交易日，並自動從來源重抓；來源本身也沒有的日子會標示「來源缺失」，不會填補。結果在「系統管理 → 市場數據 → 完整性」。管理員也可呼叫 `POST /api/v1/admin/market/check/{代號}` 即時檢查（`?full=true` 會重讀全部歷史）。

### A7. 備份與還原

```bash
# 備份資料庫（觀察名單、所有估值紀錄、AI 用量、設定）
docker compose exec -T db pg_dump -U valuation valuation > backup-$(date +%F).sql

# 備份 Excel 與估值檔（outputs 資料卷）
docker run --rm -v stock-valuation_outputs:/data -v "$PWD":/backup alpine tar czf /backup/outputs-$(date +%F).tgz -C /data .

# 備份系統資料（appdata 資料卷：Logo、每次估值的存檔、SEC 原始文件）
docker run --rm -v stock-valuation_appdata:/data -v "$PWD":/backup alpine tar czf /backup/appdata-$(date +%F).tgz -C /data .

# 還原資料庫
docker compose exec -T db psql -U valuation valuation < backup-2026-11-17.sql
```

`.env` 的 `APP_SECRET_KEY` 也要備份：遺失後，保存的 AI 金鑰無法解密（需要在設定頁重新輸入，估值資料不受影響）。

### A8. 停止與移除

```bash
docker compose stop              # 停止，資料保留
docker compose down              # 移除容器，資料保留
docker compose down -v           # 連同所有資料一併刪除（無法復原）
```

---

## B. 本機開發與測試

### B0. 一條指令（Linux／macOS，建議）

資料庫用 Docker，API 與網頁直接在本機執行（改程式碼會自動重載）：

```bash
scripts/dev.sh                          # 啟動 PostgreSQL、Redis、API（:8000）、網頁（:5173）；Ctrl+C 結束
scripts/dev.sh admin you@example.com    # 另開終端機：建立第一個管理員（只需一次，會詢問密碼）
scripts/dev.sh test                     # 全部測試：v1 引擎、v2 API、前端型別檢查與建置、版本號
scripts/dev.sh stop                     # 停止資料庫（資料保留）
```

打開 <http://localhost:5173> 登入。腳本不讀 `.env`，資料庫一律用預設帳號（valuation／change-me，只開在 127.0.0.1）；
Python 依序使用 `$SV_PYTHON`、`./.venv`、`~/.venvs/stock-valuation`、`python3`，缺少的套件會自動安裝。

以下 B1–B3 是同一件事的分步說明。

### B1. 需要甚麼

- Python 3.12 以上
- Node.js 20 以上，並執行 `corepack enable` 啟用 pnpm
- PostgreSQL 16：本系統只支援 PostgreSQL（開發、測試、正式環境相同）。最簡單是用 Docker 只啟動資料庫：
  ```bash
  docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d db redis
  ```
  資料庫與 Redis 會開在本機 `127.0.0.1:5432`、`127.0.0.1:6379`（埠號可用 `DEV_DB_PORT`、`DEV_REDIS_PORT` 更改）。
  也可以自行安裝 PostgreSQL 16，再設定 `DATABASE_URL`。
- （可選）Redis 7：不設定 `REDIS_URL` 的話，估值在 API 程序內排隊執行

### B2. 後端

```bash
python -m venv .venv
source .venv/bin/activate              # Windows：.venv\Scripts\activate
pip install -r backend/requirements.txt

# 未設定 DATABASE_URL 時，連到 localhost:5432，帳號、密碼、資料庫名稱取自 POSTGRES_USER／
# POSTGRES_PASSWORD／POSTGRES_DB（預設 valuation／change-me／valuation，與上面的 Docker 資料庫相同）。
# 加密金鑰在未設定 APP_SECRET_KEY 時自動建立在 var/（已列入 .gitignore）
uvicorn backend.app.main:app --port 8000 --reload
```

建立第一個管理員（只需一次）：

```bash
python -m backend.app.cli create-user --email you@example.com --name 你的名字 --role admin
python -m backend.app.cli list-users           # 查看所有用戶
```

API 文件：<http://localhost:8000/api/v1/docs>

（可選）使用 Redis 與獨立 worker，與 Docker 的架構相同：

```bash
export REDIS_URL=redis://localhost:6379/0
uvicorn backend.app.main:app --port 8000 &
arq backend.app.worker.WorkerSettings
```

### B3. 前端

```bash
cd frontend
pnpm install
pnpm dev                               # http://localhost:5173，/api 自動轉到 :8000
```

其他指令：

| 指令 | 作用 |
| --- | --- |
| `pnpm build` | 建置正式版（含 PWA Service Worker）到 `apps/web/dist` |
| `pnpm typecheck` | 全部套件型別檢查 |
| `pnpm test` | 語言檔一致性測試 |
| `pnpm gen:api` | API 改動後重新產生 `openapi.json` 與 TypeScript 型別 |
| `pnpm build:tokens` | 由 `tokens.json` 重新產生 CSS／JS／WXSS 與 `docs/design-system.html` |

### B4. 舊版 v1 仍然可用

```bash
python -m src.pipeline.cli run --ticker NVDA --date 2026-11-17   # 命令列
python -m src.web.app                                            # v1 網頁 http://127.0.0.1:8765
```

---

## C. 測試

```bash
python -m pytest -q                    # v1 引擎與匯出：143 項
python -m pytest backend/tests -q      # v2 API（需要 PostgreSQL，見 B1；或設定 TEST_DATABASE_URL）
cd frontend && pnpm typecheck && pnpm test && pnpm build
```

---

## D. 疑難排解

| 問題 | 解決 |
| --- | --- |
| `required variable APP_SECRET_KEY is missing` | `.env` 未設定 `APP_SECRET_KEY`，見 A2 第 3 步 |
| 8080 埠已被使用 | `.env` 改 `WEB_PORT=8090` 後 `docker compose up -d` |
| 估值卡在「取得市價」並失敗 | 電腦連不到 Yahoo Finance：檢查網絡或公司代理伺服器設定 |
| 「設定」裡的 AI 金鑰顯示「尚未設定」 | `APP_SECRET_KEY` 被改過，舊金鑰無法解密：重新輸入一次 |
| 手機上沒有「加入主畫面」 | PWA 需要 HTTPS（`localhost` 除外），見 A4 |
| 進度沒有即時更新 | 前面若有其他反向代理，需關閉對 `/api/v1/jobs/*/events` 的緩衝；前端會自動改為每秒輪詢 |
| `docker compose logs -f api worker` | 查看 API 與 worker 的詳細錯誤 |

---

## E. 這個版本的驗證範圍

- ✅ v2 API 75 項測試、v1 的 165 項測試全部通過（另 2 項略過），全部在 PostgreSQL 16 上執行（CI 每個 PR 都跑，另含資料庫遷移升級／回退／再升級）。
- ✅ 在 PostgreSQL 16 上執行資料庫遷移，並跑過主要 API 流程。
- ✅ Redis＋獨立 arq worker＋API 三個程序串連，SSE 進度串流正常。
- ✅ 前端型別檢查與正式建置通過；以 Chrome 實測桌面、手機、English、简体中文畫面，主控台沒有錯誤。
- ✅ `docker compose config` 驗證兩個 compose 檔。
- ✅ Docker 映像由 CI 在每個 PR 建置。
