# 技術架構 v2.0

- 日期：2026-11-17（澳門時間）
- 狀態：已採用（v2.0.0-alpha.1 起實施）
- 相關文件：[`系統改進計劃-v2.md`](系統改進計劃-v2.md)、[`design-system.html`](design-system.html)、[`versioning.md`](versioning.md)、[`../INSTALL.md`](../INSTALL.md)

## 1. 結論

**前後端分離，一個後端 API 服務所有前端，全部以 Docker 部署。**

```
                ┌──────────── 前端（各自獨立發佈） ────────────┐
                │ 電腦網站 ┐                                    │
                │ 手機網站 ┴ apps/web：React + Vite，PWA        │
                │ 手機 App ─ 第一步 Capacitor 包裝 PWA            │
                │            第二步（需要時）React Native         │
                │ 微信小程序 ─ Taro（React 語法）                  │
                └───────┬───────────────────────────────────────┘
                        │ HTTPS · REST /api/v1 · OpenAPI 3.1 合約
                        │ 進度：SSE 串流，或輪詢（小程序用）
┌───────────────────────▼───────────────────────────────────────┐
│ web（Caddy）：提供 PWA 靜態檔、反向代理 /api、自動 HTTPS          │
├───────────────────────┬───────────────────────────────────────┤
│ api（FastAPI）         │ worker（arq）                          │
│ 驗證、查詢、開工作      │ 執行估值：抓資料 → AI 任務 → 引擎 → 驗證 │
│ 估算 AI 費用、讀紀錄    │ 回報進度與每項 AI 費用                   │
├───────────────────────┴───────────────────────────────────────┤
│ libs/（共用領域程式庫：sv_data 四層財報與檢查、sv_market 指標）  │
│ src/（估值引擎、多語言匯出與驗證，v1/v2 共用）                  │
├────────────────────────────┬──────────────────────────────────┤
│ PostgreSQL 16              │ Redis 7                           │
│ 用戶、市場數據、四層財報、  │ 工作佇列、工作進度、取消訊號          │
│ 調整方案、估值紀錄、組合場景 │                                   │
└────────────────────────────┴──────────────────────────────────┘
```

## 2. 逐項決定與理由

| 範疇 | 選擇 | 理由 | 放棄的方案 |
| --- | --- | --- | --- |
| 後端語言 | **Python 3.12 + FastAPI** | 現有估值引擎、Excel 匯出、驗證器全是 Python，重寫沒有好處；FastAPI 自動產生 OpenAPI 合約 | Node／Go：要重寫引擎 |
| API 風格 | **REST，路徑帶版本 `/api/v1`** | 所有前端（含小程序）都能直接用；版本號讓舊 App 在新後端上繼續運作 | GraphQL：小程序與快取較麻煩 |
| API 合約 | **OpenAPI 3.1**（`/api/v1/openapi.json`），前端用 `openapi-typescript` 產生型別 | 一份合約，網頁、App、小程序共用同一套型別；改 API 時型別檢查會指出受影響的前端 | 手寫各平台型別 |
| 長時間工作 | **Redis + arq worker** | 一次估值約 30–90 秒，不能卡住 API；worker 可獨立擴充；取消訊號經 Redis 傳遞 | Celery：較重；API 內執行：多前端同時用會互相阻塞 |
| 進度推送 | **SSE（Server-Sent Events）＋輪詢後備** | SSE 走普通 HTTP，Caddy 與瀏覽器原生支援；小程序不支援 SSE，改每秒輪詢同一個工作狀態端點 | WebSocket：要另外處理重連與代理 |
| 資料庫 | **PostgreSQL 16**（正式、本機開發、自動測試一律使用），SQLAlchemy 2 + Alembic 遷移 | 多前端、多用戶同時寫入；開發與正式用同一種資料庫，避免只在某一種資料庫出現的錯誤（決定 D3） | 只用 JSON 檔：無法查詢、無法並行；SQLite：多程序寫入會鎖檔，且不保存時區 |
| 網頁前端 | **React 18 + TypeScript + Vite** | 生態最大；Taro 與 React Native 也用 React 語法，邏輯與型別可共用 | Vue：小程序有 uni-app，但 App 端選擇較少 |
| 資料請求 | **TanStack Query** | 快取、重試、背景更新、離線狀態都內建 | 自寫 fetch 狀態管理 |
| 圖表 | **ECharts** | 同一套圖表設定可用於網頁、React Native 與微信小程序（echarts-for-weixin），走勢圖不必寫三次 | Recharts／D3：沒有小程序版本 |
| 手機網站 | **PWA**（vite-plugin-pwa / Workbox） | 可安裝到主畫面、離線查看最近的估值、Web Push 提醒（iOS 16.4 起支援已安裝的 PWA）；與電腦網站同一套程式 | 另寫一個手機網站 |
| 手機 App | **第一步 Capacitor 包裝 PWA，第二步 React Native（Expo）** | Capacitor 幾乎零成本上架 App Store／Google Play，並取得原生推播；體驗不足時才換 React Native，屆時沿用 `api-client`、`tokens`、`core` | 一開始就寫原生 App：成本高 |
| 微信小程序 | **Taro（React 語法）** | 與網頁同為 React，共用 `api-client`、`core`、`tokens.wxss`；圖表用 ECharts 小程序版 | 原生小程序：邏輯要重寫 |
| 前端倉庫 | **pnpm workspace monorepo** | `apps/*`（各前端）與 `packages/*`（tokens、api-client、core）同倉管理，版本一致 | 多倉：合約與型別容易不同步 |
| 閘道 | **Caddy** | 一行設定自動申請 HTTPS 憑證（PWA 與 Service Worker 必須 HTTPS）；同時提供靜態檔與反向代理 | Nginx：HTTPS 要另配 certbot |
| 部署 | **Docker Compose**：web、api、worker、db、redis 五個服務 | 一條指令啟動整套；api 與 worker 共用同一映像；資料放在具名 volume | 裸機安裝：Windows／macOS／Linux 步驟各異 |
| 驗證 | **alpha：單一 API 令牌（Bearer）**；之後 JWT 多使用者、微信登入 | 本機單人使用時不必登入；開放到區網或公網時設定令牌即可 | 一開始就做完整帳號系統 |
| AI 金鑰 | **伺服器端以 Fernet 加密保存**（`APP_SECRET_KEY`），API 只寫不讀 | 金鑰不會出現在任何前端、日誌或估值檔案；多前端共用同一份設定 | 存在瀏覽器：每個前端各存一份，也較易外洩 |
| 版本 | **SemVer**，根目錄 `VERSION` 為唯一來源；API 另有路徑版本 | 見 [`versioning.md`](versioning.md) | — |

## 3. 目錄結構

```
stock-valuation/
├── VERSION                     # 唯一版本來源（2.0.0-alpha.1）
├── CHANGELOG.md
├── INSTALL.md                  # 安裝說明（Docker／本機開發）
├── var/                        # 本機執行狀態（加密金鑰、上傳檔），不進版本控制
├── docker-compose.yml          # 正式／本機一條指令啟動
├── docker-compose.dev.yml      # 開發用覆寫（熱重載、開放埠）
├── .env.example                # Docker 環境變數範例
├── libs/                       # 共用領域程式庫（不依賴 FastAPI／ORM）
│   ├── sv_data/                # 四層財報（原始、申報、標準、檢查）與調整方案、SEC XBRL 解析與 YAML 規則
│   └── sv_market/              # 市場數據純函數、交易日曆完整性、CFA 績效指標與投資組合最佳化
├── src/                        # 估值引擎與多語言 Excel／Markdown 匯出（v1 網頁與 CLI 照常可用）
├── backend/                    # v2 API 與 worker
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── alembic.ini, alembic/   # 資料庫遷移
│   ├── app/
│   │   ├── main.py             # FastAPI 應用、路由、CORS、令牌驗證
│   │   ├── config.py           # 環境變數設定
│   │   ├── db.py, models.py    # SQLAlchemy 模型
│   │   ├── schemas.py          # API 輸入輸出（OpenAPI 由此產生）
│   │   ├── pricing.py          # 價格表、AI 任務費用估算
│   │   ├── jobs.py             # 工作佇列與進度（Redis／記憶體）
│   │   ├── worker.py           # arq worker 進入點
│   │   ├── services/           # 估值執行、舊資料匯入、設定與加密
│   │   └── routers/            # /api/v1/* 各端點
│   └── tests/
├── frontend/                   # pnpm workspace
│   ├── apps/web/               # 電腦網站＋手機網站（PWA）
│   ├── packages/tokens/        # 設計 token → CSS／JS／WXSS＋設計規範頁
│   ├── packages/i18n/          # 語言檔＋數字／貨幣／日期格式（不依賴框架）
│   └── packages/api-client/    # 由 OpenAPI 產生的型別＋輕量請求函式
├── deploy/Caddyfile
├── scripts/bump_version.py     # 同步 VERSION 至各 package.json
└── docs/
```

日後新增前端時只加目錄：`frontend/apps/mobile`（Capacitor 或 Expo）、`frontend/apps/miniprogram`（Taro）。

## 4. API 與資料庫

- 全部接口（用途、權限、格式、錯誤代碼）：[`docs/接口文件.md`](接口文件.md)
- 全部資料表與遷移：[`docs/資料庫設計.md`](資料庫設計.md)

兩份文件由 CI 測試核對，與程式碼不符時 PR 不能合併。

## 5. 多語言

| 層 | 做法 |
| --- | --- |
| 介面文字 | `frontend/packages/i18n/src/locales/*.json`，以鍵值管理；網頁、App、小程序共用同一份。新增語言＝複製 `zh-Hant.json` 翻譯後在 `index.ts` 登記 |
| 一致性 | `pnpm test` 檢查每種語言的鍵與 `{佔位符}` 與繁體中文完全相同，缺一個就失敗 |
| 語言選擇 | 首次按裝置語言（zh-TW／HK／MO → 繁體、zh-CN／SG → 简体、en → English），之後以用戶選擇為準並保存在該裝置；`<html lang>` 同步更新，簡體使用 Noto Sans SC |
| 數字格式 | `createFormat(locale)`：千分位、貨幣符號（HK$、US$、¥）、正負號（−，U+2212）、百分比、日期一律 YYYY-MM-DD |
| API | 狀態、步驟、略過原因、錯誤都以固定代碼回傳（`skip_code`、`note_code`、`detail.code`），前端按代碼翻譯；錯誤文字另按 `Accept-Language` 回覆繁／簡／英 |
| AI 產出 | 第 2 階段起，AI 任務的提示詞加入用戶語言，報告與理由以該語言產生；數字由引擎計算，不受語言影響 |
| 引擎說明 | 資料缺口（`src/pipeline/locales/gaps.json`）、估值 Excel 與 Markdown 報告（`src/export/locales/export.json`）均支援繁體中文、简体中文與 English；後端依 `?lang=` 或 `Accept-Language` 輸出 |

## 6. 安全

- 預設只對本機開放（`127.0.0.1:8080`）；設定 `APP_API_TOKEN` 後才建議開放到區網。
- Caddy 只接受設定的主機名稱，防止 DNS rebinding。
- 跨網域只允許 `CORS_ORIGINS` 列明的前端（例如小程序的開發者工具、App 的 `capacitor://localhost`）。
- AI 金鑰加密保存，API 回應只顯示長度與末四碼。
- 代號沿用 v1.1.1 的嚴格檢查，不會成為檔案路徑攻擊。

## 7. 與 v1 的關係

- `src/` 引擎、CLI（`python -m src.pipeline.cli`）與 v1 網頁（`python -m src.web.app`）在 v2 開發期間**照常可用**，測試也照常通過。
- v2 的 worker 直接呼叫 `src.pipeline.orchestrator.run_ticker()`，Excel 與 calc JSON 仍寫入 `out/`，與 v1 完全相同。
- 首次啟動時，`backend` 會把 `out/` 內既有的估值匯入資料庫，走勢圖從第一天起就有歷史。
- v1 網頁在 v2.0 正式版後標示為「舊版」，再保留一個小版本週期才移除。
