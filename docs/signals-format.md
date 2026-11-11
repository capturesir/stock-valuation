# B 組相容交易訊號檔格式（schema_version 1.0）

本文件說明本套件匯出的交易訊號檔，格式與 A 組正式匯出器（`models/build_signals.py`）寫給 B 組自動交易系統的 `schema_version 1.0` 相同。範例文件見 `docs/signals_schema_v1.json`（官方模板副本）；欄位型別、列舉與格式見 `docs/signals.schema.json`。

**這是研究用訊號檔，不是投資建議，也不是下單指令。** 後續交易框架讀取後，仍須自行決定是否下單、如何風控與如何執行。

本套件預設只寫到套件目錄內的 `out/signals.json` 與 `out/signals/YYYY-MM-DD.json`（在 Windows、macOS、Linux 皆同），**不會**寫到套件以外的正式訊號目錄。A 組內部正式檔路徑是 `signals.json`／`signals/YYYY-MM-DD.json`；可攜套件預設不會去改那些檔案。

---

## 1. 檔名與寫入方式

| 檔案 | 說明 |
| --- | --- |
| `out/signals.json` | 最新一份訊號檔（預設目的地） |
| `out/signals/YYYY-MM-DD.json` | 日期副本；日期為運行／估值日（`--date`），不是市價日期 |
| `out/signals.invalid.json` | 驗證失敗時才寫入；**不會**覆蓋已通過驗證的 `signals.json` |

寫入採原子方式：同目錄暫存檔 → `os.replace`。預設若目的檔已存在，會以代號為鍵合併（同一代號被取代，其他代號保留），再重算頂層 `price_date` 與 `generated_at`。使用 `--no-merge` 則只寫入本次列出的代號。

驗證在發布前進行（優先用 `jsonschema`，未安裝則退回手動檢查，並做 `upside_pct` 語意核對）。失敗結束碼為 2。

---

## 2. 頂層欄位（此次序）

| 欄位 | 型別 | 規則 |
| --- | --- | --- |
| `schema_version` | 字串 | 固定 `"1.0"` |
| `generated_at` | 字串 | 產生時間，ISO 日期時間，時區 `+08:00`（亞洲／澳門），無微秒。格式：`YYYY-MM-DDTHH:MM:SS+08:00` |
| `generated_by` | 字串 | 預設 `"投資經理"`；可用 CLI `--generated-by` 覆寫 |
| `price_date` | 字串 | 各訊號 `price_date` 之中最新者（`YYYY-MM-DD`）。若 `signals` 為空陣列，則用運行日 |
| `signals` | 陣列 | 訊號列表；空陣列合法 |

---

## 3. 個別訊號欄位（此次序）

與官方模板完全相同：

`signal_id`, `ticker`, `market`, `name`, `asset_type`, `currency`, `side`, `confidence`, `fair_value`, `market_price`, `price_date`, `upside_pct`, `horizon`, `rationale`, `report_path`, `expires_at`

| 欄位 | 型別 | 規則 |
| --- | --- | --- |
| `signal_id` | 字串 | `{YYYYMMDD}-{ticker}`，以運行／估值日組成。前 8 碼必須是數字，且 `signal_id[9:] == ticker`。正則：`^\d{8}-[A-Z0-9][A-Z0-9.\-]*$` |
| `ticker` | 字串 | 股票（或加密貨幣對）代號，例如 `NVDA`、`1830.HK` |
| `market` | 字串 | 由代號後綴推斷：`.TW`／`.TWO` → `TW`，`.HK` → `HK`，`.T` → `JP`，`.KS`／`.KQ` → `KR`，無後綴 → `US`。加密貨幣對（如 `BTC-USD`）→ `CRYPTO`。合法值：`US`、`JP`、`HK`、`KR`、`TW`、`CRYPTO`。不在列舉則拒絕匯出 |
| `name` | 字串 | 公司名稱，取自估值 calc JSON 的 `asset` |
| `asset_type` | 字串 | 上述股票市場為 `equity`；`CRYPTO` 為 `crypto` |
| `currency` | 字串 | 三位大寫貨幣碼，取自 calc JSON 的 `currency`（如 `USD`、`HKD`） |
| `side` | 字串 | `BUY`／`SELL`／`HOLD`，見第 4 節 |
| `confidence` | 整數 | 0–100，見第 4 節（本套件只輸出基礎公式） |
| `fair_value` | 數值 | 理論價。取自 calc JSON 的 `fair_value_per_share`（若無則用名稱為「基準」的情境），以四捨五入（half-up）至 2 位小數後寫入。這與正式檔常見的 2 位小數，以及本套件 README 列示的 NVDA US$874.98、1830.HK HK$5.51 一致 |
| `market_price` | 數值 | 市價，取自 calc JSON 的 `price`，同樣 half-up 至 2 位小數後寫入（避免浮點雜訊，例如 `168.88999938964844` → `168.89`） |
| `price_date` | 字串 | 市價日期，取自 calc JSON 的 `price_date`（`YYYY-MM-DD`） |
| `upside_pct` | 數值 | 見第 4 節。**必須**由檔中已寫入的 `fair_value` 與 `market_price` 算出 |
| `horizon` | 字串 | 固定 `"12M"`（官方常數；模板範例寫 `"6-12m"`，僅供示意，並非列舉值） |
| `rationale` | 字串 | 一句繁體中文，見第 5 節 |
| `report_path` | 字串 | 必填、不可空白。優先使用該次估值實際寫出的 Markdown 路徑，例如 `out/2026-11-11_NVDA/2026-11-11_NVDA_valuation.md`（套件內路徑以套件根目錄的相對路徑表示） |
| `expires_at` | 字串 | 運行日之後下一個工作日 07:00 `+08:00`（週五／六／日運行 → 週一）。不計公眾假期。格式與 `generated_at` 相同 |

---

## 4. 計算規則（與正式 `build_signals.py` 基礎公式相同）

以下用 half-up（四捨五入，`.5` 進位）——與正式腳本的 `half_up` 相同（`Decimal`、`ROUND_HALF_UP`）。

### 4.1 潛在上升／下跌幅度

```
upside_pct = half_up((fair_value / market_price - 1) * 100, 1)
```

例：理論價 874.98、市價 168.89 → `(874.98 / 168.89 - 1) * 100 = 418.0768…` → **418.1**。

### 4.2 方向（side）

| 條件 | side |
| --- | --- |
| `upside_pct >= 15.0` | `BUY`（剛好 15.0 亦為買入） |
| `upside_pct <= -15.0` | `SELL`（剛好 −15.0 亦為賣出） |
| 其餘（例如 14.9 或 −14.9） | `HOLD` |

### 4.3 信心分數（本套件預設路徑：只輸出基礎公式）

先取已四捨五入至 1 位的 `|upside_pct|`，再 half-up 取整（**33.5 → 34**），然後：

- 若 `BUY` 或 `SELL`：`confidence = min(90, 40 + round(|upside_pct|))`
- 若 `HOLD`：`confidence = max(0, 50 - round(|upside_pct|))`
- 最後夾在 0–100

例：

| upside_pct | side | round(\|upside\|) | confidence |
| --- | --- | --- | --- |
| +15.0 | BUY | 15 | 55 |
| +14.9 | HOLD | 15 | 35 |
| −15.0 | SELL | 15 | 55 |
| +33.5 | BUY | 34 | 74 |
| +418.1 | BUY | 418 | **90**（触及上限） |
| +7.6 | HOLD | 8 | 42 |

### 4.4 與正式 A 組匯出器的差異（請後續交易框架注意）

正式 A 組 `build_signals.py` 在上述基礎分之後，**還可能**再調整：

- 市價日期較運行日超過 3 個曆日：信心 −10
- 重大資料缺口，或市價早於其後公布的業績：信心 −10（兩者同時亦只扣一次）
- `coverage/review_status.csv` 的 `confidence_cap`（例如擴大覆蓋附註名單上限 60）
- `signal_enabled` 覆核閘門：未啟用則整筆不出訊號

本套件可攜匯出器**不套用**上述調整，只輸出穩定、可文件化的「理論價 vs 市價」對映，方便日後開發交易框架。若把本套件輸出與 A 組正式訊號檔對照，部分代號的 `confidence` 可能較正式檔為高（正式檔已扣分或封頂）；`side` 與 `upside_pct` 只要理論價與市價相同就應一致。

---

## 5. `rationale` 寫法

一句繁體中文，結構與正式 `build_rationale` 相同：

> FCFF DCF 理論價 {幣別符號}{理論價} 較 {MM/DD} 收市價 {幣別符號}{市價} 高／低 {幅度}% ，悲觀／樂觀情境 {幣別符號}{低}–{高}。

- 幣別符號：`USD`→`US$`、`HKD`→`HK$`、`TWD`→`NT$`、`CNY`→`RMB`、`JPY`→`¥`、`KRW`→`₩`；其餘用「貨幣碼 + 空格」
- 若 calc JSON 的 `scenarios` 有名稱以「悲觀」「樂觀」開頭的情境，則附上區間；若名稱含「：」，括號內寫「：」之後的說明
- 本套件預設路徑不加正式 A 組的「市價過期／資料缺口／信心上限」尾註

例（NVDA，2026-11-11 估值）：

> FCFF DCF 理論價 US$874.98 較 11/10 收市價 US$168.89 高 418.1%，悲觀／樂觀情境 US$783.54–961.89。

---

## 6. 後續交易框架應如何讀取

1. 讀 `schema_version`，本版本為 `"1.0"`。可用本目錄的 `signals.schema.json` 再驗證一次。
2. 日常取最新的 `out/signals.json`；若要重播某一天，用 `out/signals/YYYY-MM-DD.json`。
3. 對每筆訊號至少讀取：`ticker`、`side`、`confidence`、`fair_value`、`market_price`、`upside_pct`、`currency`、`expires_at`、`rationale`、`report_path`。
4. 建議自行重算 `upside_pct = half_up((fair_value / market_price - 1) * 100, 1)`，與檔內值核對。
5. `expires_at` 之後應視為過期，需等下一份訊號檔。
6. **不要把本檔直接當訂單。** `side` 是估值相對市價的分類，不是已核准的交易指令，也不是投資建議。
7. 合併更新時，同一 `ticker` 以新列取代舊列；不要假設陣列次序穩定。

指令與網頁操作見套件根目錄 `README.md` 的「交易訊號匯出」一節。
