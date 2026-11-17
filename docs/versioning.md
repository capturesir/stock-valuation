# 版本維護

## 1. 兩種版本號

| 版本 | 位置 | 何時改 |
| --- | --- | --- |
| **產品版本**（SemVer，例如 `2.0.0-alpha.1`） | 根目錄 `VERSION`（唯一來源），同步到各 `package.json`、`.env.example`、`docker-compose.yml` 的映像標籤 | 每次發佈 |
| **API 版本**（路徑，`/api/v1`） | `backend/app/main.py` 的 `API` | 只在**不相容**的 API 變更時改為 `/api/v2`，並保留 `/api/v1` 至少一個小版本 |

手機 App 與小程序更新較慢，舊版本會繼續呼叫舊的 API，所以 API 版本獨立於產品版本。

## 2. SemVer 規則

`主版本.次版本.修訂版[-預覽.序號]`

| 變更 | 例子 | 版本 |
| --- | --- | --- |
| 修正錯誤，行為不變 | 修正敏感度表的格式 | `2.0.0` → `2.0.1` |
| 新功能，向下相容 | 新增相對估值方法、新的 API 端點、新語言 | `2.0.1` → `2.1.0` |
| 不相容變更 | 移除 API 欄位、資料庫結構需要人手處理、Excel 版面大改 | `2.1.0` → `3.0.0` |
| 預覽版本 | 功能未完整 | `2.0.0-alpha.1` → `-alpha.2` → `-beta.1` → `-rc.1` → `2.0.0` |

**估值結果相關的特別規則：** 改動任何會令同一輸入得出不同理論價的程式（引擎、規則、預設參數），至少升次版本，並在 CHANGELOG 的「估值結果變動」小節寫明哪些股票、改變多少（v1.1.1 的做法）。

## 3. 發佈步驟

```bash
# 1. 確認全部測試通過
python -m pytest -q                       # v1 引擎（143 項）
python -m pytest backend/tests -q         # v2 API（21 項）
cd frontend && pnpm typecheck && pnpm test && pnpm build && cd ..

# 2. API 有變動時重新產生合約與前端型別
cd frontend && pnpm gen:api && cd ..

# 3. 改版本號（會自動加入 CHANGELOG 空白段落）
python scripts/bump_version.py 2.0.0-alpha.2

# 4. 填寫 CHANGELOG.md，然後提交並打標籤
git add -A && git commit -m "release: v2.0.0-alpha.2"
git tag -a v2.0.0-alpha.2 -m "v2.0.0-alpha.2"
git push && git push --tags

# 5. 部署
docker compose up -d --build
```

`python scripts/bump_version.py --check` 會檢查所有檔案的版本是否一致，適合放進 CI。

## 4. 資料庫遷移

- 結構變更一律用 Alembic：先改 `backend/app/models.py`，再產生遷移檔：
  ```bash
  alembic -c backend/alembic.ini revision --autogenerate -m "說明"   # 連到本機開發用 PostgreSQL（見 INSTALL.md B1）
  ```
- 檢查產生的檔案（特別是改名欄位，Alembic 會誤判為刪除再新增）。
- API 與 worker 啟動時會自動升級到最新版本（`init_db()`），不需要人手執行。
- 遷移不得刪除使用者資料；需要刪除時分兩個版本：先停用，下一個版本再移除。

## 5. 分支與提交

- `main` 永遠可以部署；功能在 `feat/…`、修正在 `fix/…` 分支開發，以 PR 合併。
- 提交訊息用 Conventional Commits：`feat:`、`fix:`、`docs:`、`refactor:`、`test:`、`chore:`，方便整理 CHANGELOG。
- 每個 PR 必須：測試通過、更新 CHANGELOG 的「未發佈」段落、API 變動時附上新的 `openapi.json`。

## 6. 支援期

| 版本 | 支援 |
| --- | --- |
| 最新次版本 | 修正與新功能 |
| 上一個次版本 | 只修正嚴重錯誤與安全問題，3 個月 |
| v1（`src/web`） | v2.0.0 正式版後再保留一個次版本，之後移除 |
