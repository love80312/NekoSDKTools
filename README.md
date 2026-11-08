# NekoSDKTools（個人優化版）

用於 NekoSDK／Waffle 遊戲資源的個人工具組，包含 NEKOPACK4A／NEKOPACK4S 封包、NEKOSDK_ADVSCRIPT2 腳本、BMP+ALP 圖像，以及 CP932 字元統計與替代字元管理。

> 本專案不是通用的 PAK／TXT 解包工具。只有符合對應格式的檔案才能使用相關功能。修改後資源仍須在實際遊戲中測試。

## 功能與檔案

| 檔案／資料夾 | 用途 |
|---|---|
| `GUI.py` | Tkinter 圖形介面，整合 PAK、腳本、BMP／ALP 及字元管理 |
| `pak.py` | 解包 NEKOPACK4A／NEKOPACK4S，並可重包成 4A 或 4S |
| `scr.py` | 解析 NEKOSDK_ADVSCRIPT2，將可辨識的對話／選項匯出為 UTF-8 JSON，再匯入修改內容 |
| `bmp_alp.py` | BMP+ALP 與一般圖片之間的合併／拆分工具 |
| `char_manager.py` | CP932 字元統計、候選排除、映射工作區及兩行替換字串匯出 |
| `tool/常用字.txt` | 使用者自訂的常用字排除清單 |
| `tool/char_manager.json` | 字元管理設定、人工排除及候選排除開關 |
| `tool/char_candidate_exclusions.json` | 中文／日文標點、常用符號及自訂排除字元 |
| `char_manager_README.md` | 字元管理工具的詳細操作、排除規則及命令列說明 |
| `requirements.txt` | 圖像轉換工具的 Pillow 依賴 |

## 環境需求與啟動

- Python 3.10 以上版本。
- 使用圖形介面需要 Tkinter；Windows 版 Python 安裝時需包含 Tcl/Tk。
- `pak.py`、`scr.py`、`char_manager.py` 使用 Python 標準函式庫。
- `bmp_alp.py` 需要 Pillow 10.0 以上版本。

安裝圖像工具依賴：

```bat
py -3 -m pip install -r requirements.txt
```

啟動圖形介面：

```bat
py -3 GUI.py
```

`GUI.py`、`pak.py`、`scr.py`、`bmp_alp.py`、`char_manager.py` 請放在同一個資料夾。字元管理功能直接由 `GUI.py` 載入 `char_manager.py`，不需要另外啟動第二套 GUI。

---

## 1. PAK 封包：解包與重包

`pak.py` 支援以下兩種 NekoSDK 封包：

| 格式 | 結構 | 解包 | 重包 |
|---|---|---:|---:|
| `NEKOPACK4A` | 索引區可包含多筆檔案紀錄 | 支援 | 支援 |
| `NEKOPACK4S` | 單一檔案紀錄 | 支援 | 支援，來源資料夾必須恰好有一個檔案 |

兩種格式的索引配置不同，不能只替換檔頭就互相轉換。程式會依格式分別建立索引，並共用檔案資料的 Zlib 壓縮、大小資訊及 XOR 遮罩處理。

### 1.1 解包

A、S 格式使用相同命令，程式會自動辨識封包檔頭：

```bat
py -3 pak.py -u "D:\Game\scr.pak" "D:\Game\scr_unpacked"
```

### 1.2 重包

預設輸出 `NEKOPACK4A`，可以包含一個或多個檔案：

```bat
py -3 pak.py -p "D:\Game\scr_imported" "D:\Game\scr_new.pak"
```

指定輸出 `NEKOPACK4A`：

```bat
py -3 pak.py -p "D:\Game\scr_imported" "D:\Game\scr_new.pak" --format 4A
```

指定輸出 `NEKOPACK4S`：

```bat
py -3 pak.py -p "D:\Game\single_file_folder" "D:\Game\single_new.pak" --format 4S
```

**4S 格式限制：** 來源資料夾內必須恰好有一個檔案，可位於子資料夾。若有零個或多個檔案，程式會停止並回報數量，不會輸出不符合格式的封包。

GUI 的「PAK 封包」頁籤也可在重包時選擇 4A 或 4S。解包時會自動辨識格式；選擇 4S 重包時，GUI 會先檢查來源檔案數量，`pak.py` 仍會再次檢查。

### 1.3 重包設定

| 環境變數 | 說明 | 預設值 |
|---|---|---:|
| `NEKO_PAK_LEVEL` | Zlib 壓縮等級，範圍 0–9 | `9` |
| `NEKO_PAK_WORKERS` | 壓縮工作執行緒，需為正整數 | `1` |

例如：

```bat
set NEKO_PAK_LEVEL=6
set NEKO_PAK_WORKERS=1
py -3 pak.py -p "D:\Game\scr_imported" "D:\Game\scr_new.pak" --format 4A
```

舊電腦建議先維持單執行緒。輸出 PAK 必須放在來源資料夾之外。封包內檔名使用 CP932 編碼；若檔名無法使用 CP932 表示，重包會明確報錯。

> 重包成功代表封包結構及工具內部往返測試通過，不代表遊戲一定接受新封包。若遊戲原始素材使用 4S，建議優先以 4S 重包並在遊戲內測試；不要只因 A、S 都能被工具解析，就假定遊戲端可以互換。

---

## 2. 腳本／JSON：匯出、檢視與匯入

### 2.1 先確認 TXT 的實際格式

`NEKOSDK_ADVSCRIPT2` 腳本雖然常使用 `.txt` 副檔名，內容實際上是二進位資料，檔案開頭具有 `NEKOSDK_ADVSCRIPT2` 檔頭。不要因為副檔名是 `.txt`，就直接用文字編輯器開啟後另存；這可能破壞二進位腳本。

`scr.py` 只解析 `NEKOSDK_ADVSCRIPT2`，不是一般 TXT 閱讀器，也不會自動辨識其他遊戲的文字格式。

### 2.2 從腳本匯出 JSON

單一檔案：

```bat
py -3 scr.py -e "D:\Game\scr_unpacked\ep_300_000_000.txt" "D:\Game\ep_300_000_000.json" --encoding cp932
```

整個資料夾：

```bat
py -3 scr.py -e "D:\Game\scr_unpacked" "D:\Game\scr_json" --encoding cp932
```

匯出 JSON 使用 UTF-8，資料夾模式會保留相對子資料夾結構，並將腳本副檔名改為 `.json`。目前會匯出可辨識的對話及選項，不等於將整個腳本轉成可閱讀的純文字；其他指令、控制資料及未辨識欄位不會列入翻譯 JSON。

一般對話格式：

```json
[
  {"name": "角色名稱", "message": "對話內容"},
  {"message": "沒有說話者名稱的對話"}
]
```

選項格式：

```json
[
  {"choice": "選項文字 A"},
  {"choice": "選項文字 B"}
]
```

編輯時請保留 JSON 陣列項目順序，不要任意增加或刪除項目。對話項目保留 `message`，選項項目使用 `choice`，單一選項不可含換行。

### 2.3 將修改後的 JSON 匯入腳本

單一檔案：

```bat
py -3 scr.py -i "D:\Game\scr_unpacked\ep_300_000_000.txt" "D:\Game\ep_300_000_000.json" "D:\Game\ep_300_000_000_imported.txt" --encoding cp932
```

整個資料夾：

```bat
py -3 scr.py -i "D:\Game\scr_unpacked" "D:\Game\scr_json" "D:\Game\scr_imported" --encoding cp932
```

資料夾匯入時，原始腳本與 JSON 資料夾的相對路徑必須對應；每個 ADVSCRIPT2 腳本都必須有相對應的 JSON。非 ADVSCRIPT2 檔案不需要 JSON，會原樣複製到輸出資料夾，避免重包時遺漏其他資源。

`main` 欄位可能保存與執行時文字相同的內容，也可能是另一種語言的原文／參照內容。匯入時只有在能確認文字對應關係時才同步更新；無法確認時會保留原始 `main`，避免用翻譯文字覆蓋不同語言的參照資料。

### 2.4 腳本字串編碼

`scr.py` 預設使用 `cp932`。可用 `--encoding` 指定 Python 支援的編碼名稱，例如 `cp932`、`shift_jis`、`utf-8`、`gbk` 或 `big5`。

日文場景指令與 GBK 對話文字混合的腳本，可以使用：

```bat
py -3 scr.py -e "D:\Game\script.txt" "D:\Game\script.json" --encoding cp932 --dialogue-encoding auto
```

`auto` 模式會依指令本文與 CP932 位元組的對應關係辨識 CP932 文字記錄；其他 opcode-5 對話記錄則按 GBK 處理。這不代表遊戲引擎一定支援所有輸出字元。CP932 無法表示的繁體中文字會在匯入時明確報錯，不會自動以問號取代。

---

## 3. BMP／ALP 圖像轉換

此功能處理 NekoSDK／Waffle 使用的 BMP+ALP 配對。BMP 儲存 RGB 圖像，ALP 儲存每像素 1 byte 的 Alpha 資料，合併後可輸出一般 RGBA 圖片。

合併單組：

```bat
py -3 bmp_alp.py -e "D:\Game\image.bmp" "D:\Game\image.alp" "D:\Game\image.png"
```

批次合併：

```bat
py -3 bmp_alp.py -e "D:\Game\bmp_alp" "D:\Game\png"
```

拆分單張：

```bat
py -3 bmp_alp.py -c "D:\Game\image.png" "D:\Game\image.bmp" "D:\Game\image.alp"
```

批次拆分：

```bat
py -3 bmp_alp.py -c "D:\Game\images" "D:\Game\bmp_alp"
```

來源圖片支援 PNG、TGA、BMP、JPG／JPEG、WebP、TIF／TIFF。批次合併固定輸出 PNG；單張合併可依輸出副檔名選擇格式。JPEG 不支援 Alpha，輸出 JPEG 時會轉成 RGB 並捨棄透明度。合併時可讀取緊密排列，以及每列以 4 bytes 對齊的 ALP 資料。

---

## 4. CP932 字元統計與替代字元管理

`char_manager.py` 可獨立執行，也由 `GUI.py` 內的「字元管理」頁籤呼叫。此功能統計翻譯文件中的漢字、標點及符號，找出 CP932 無法編碼的目標字元，再從 CP932 字形目錄建立替代來源配對。

### 4.1 掃描來源

翻譯文件資料夾是必要來源；原始遊戲腳本資料夾為選填。支援 JSON、TXT、CSV、TSV、PO、POT、FTL、INI、XML、HTML、Markdown、YAML 等格式。JSON 只統計值中的字串，不統計欄位名稱／鍵。

原始遊戲腳本必須先使用 `pak.py` 解包。掃描會重用 `scr.py`，依 `NEKOSDK_ADVSCRIPT2` 檔頭辨識腳本，統計可辨識的角色名稱、對話及選項；非腳本資源會跳過。

| 原始腳本選項 | 候選處理 |
|---|---|
| 未指定 | 仍可建立候選，但未檢查原文占用 |
| 指定且完整掃描 | 排除原始腳本已使用字元 |
| 指定但掃描不完整 | 不提供安全候選，不允許建立或匯出映射 |

原文掃描只涵蓋 `scr.py` 可辨識的文字欄位，不代表涵蓋遊戲所有資源。

### 4.2 候選排除規則

預設啟用下列排除條件。這些條件只影響 CP932 來源候選，不會影響翻譯統計、原文統計或待替換目標：

| 排除項目 | 來源 |
|---|---|
| 使用者自訂常用字 | `tool/常用字.txt` |
| 中文常用標點 | `tool/char_candidate_exclusions.json` |
| 日文常用標點 | `tool/char_candidate_exclusions.json` |
| 星號、圖形、箭頭、數學及單位符號 | `tool/char_candidate_exclusions.json` |
| 自訂排除字元 | `tool/char_candidate_exclusions.json` |
| 人工排除字元 | `tool/char_manager.json` |
| 翻譯或原始腳本已使用字元 | 本次掃描統計 |
| 目前工作映射已占用字元 | 本次工作區 |

`tool/常用字.txt` 是使用者自行整理的 UTF-8 單行字元集合；不是教育部 4,808 字表，不會下載或要求固定字數。其排除只作用於來源候選。

### 4.3 GUI 操作

1. 開啟 `GUI.py`，切換到「字元管理」頁籤。
2. 選擇翻譯文件資料夾。
3. 視需要選擇已解包的原始遊戲腳本資料夾。
4. 設定翻譯文字、原始指令及對話編碼。
5. 選擇要啟用的候選排除規則。
6. 按下「開始掃描與統計」。
7. 在目標清單選擇 CP932 無法編碼的字元，再從來源候選清單選擇替代字元，建立配對。
8. 可人工排除來源候選、保存工作草稿及繼續上次工作。
9. 按「匯出兩行替換字串」，產生只有兩行的 UTF-8 TXT。
10. 自行將第一行貼入設定的 `source_characters`，第二行貼入 `target_characters`。

### 4.4 匯出格式與 UIF 邊界

匯出檔只有兩行：第一行是來源字元排列，第二行是目標字元排列；相同位置一一對應。兩行數量必須相同，來源及目標各自不得重複。檔案不加入標題、欄位名稱、逗號、引號或說明文字。

**字元管理器不讀取、不修改、不合併、不備份任何 UIF 設定。** 不提供讀取既有 UIF 映射或直接套用的功能。所有配對只屬於本次工作區與工作草稿，因此不會將其他版本設定中既有的字元混入匯出結果。

舊版草稿可能曾包含從外部設定載入的映射。新版不會自動還原舊版草稿；請重新掃描並建立新工作，以避免舊映射混入。

### 4.5 命令列掃描

只掃描翻譯文件：

```bat
py -3 char_manager.py scan --translations "D:\Game\translation_json"
```

加入原始遊戲腳本：

```bat
py -3 char_manager.py scan --translations "D:\Game\translation_json" --source "D:\Game\original_scripts" --command-encoding cp932 --dialogue-encoding auto
```

| 參數 | 說明 | 預設值 |
|---|---|---|
| `--source` | 選填；已解包 ADVSCRIPT2 檔案或資料夾 | 不掃描 |
| `--command-encoding` | 原始指令／場景編碼 | `cp932` |
| `--dialogue-encoding` | 原始對話編碼 | `auto` |
| `--output` | 報表輸出資料夾 | 翻譯資料夾旁的 `*_char_report` |
| `--settings` | `char_manager.json` 路徑 | `tool/char_manager.json` |
| `--text-encoding` | TXT／CSV 等翻譯文字編碼 | `utf-8-sig` |

命令列只提供掃描與報表輸出，不提供直接套用設定檔的功能。

### 4.6 報表檔案

掃描完成後，預設在翻譯資料夾旁建立 `*_char_report`：

| 檔案 | 用途 |
|---|---|
| `README_報表說明.txt` | 本次掃描條件與報表說明 |
| `character_report.csv` | 翻譯字元統計與 CP932 狀態 |
| `source_character_report.csv` | 原始腳本字元統計；指定原文時才產生 |
| `cp932_glyphs.csv` | CP932 字形、使用狀態、排除命中及原因 |
| `safe_candidate_glyphs.csv` | 通過所有必要檢查的來源候選 |
| `unmapped_characters.txt` | 尚未映射的 CP932 不支援目標字元 |
| `mapping_pairs.csv` | 本次工作區的映射配對 |
| `scan_metadata.json` | 掃描條件、清單雜湊與完整性資訊 |
| `mapping_draft.json` | GUI 保存的工作草稿 |

翻譯文件或指定的原始腳本只要有必要檔案讀取／解析失敗，安全候選就會清空，避免將部分掃描結果誤認為完整結果。修正錯誤後請重新掃描。

---

## 5. 專案檔案與清理

| 檔案／資料夾 | 判定 | 說明 |
|---|---|---|
| `GUI.py`、`pak.py`、`scr.py`、`bmp_alp.py`、`char_manager.py` | 必要 | 各主功能程式 |
| `char_manager_README.md` | 建議保留 | 字元管理詳細操作文件 |
| `tool/常用字.txt` | 必要 | 使用者自訂的常用字清單 |
| `tool/char_manager.json` | 建議保留 | 人工排除及編碼設定 |
| `tool/char_candidate_exclusions.json` | 必要 | 標點、符號及自訂排除清單 |
| `requirements.txt` | 建議保留 | Pillow 依賴 |
| `tool/uif_config.json` | 與字元管理器分離 | 字元管理器不會讀取或修改此檔 |
| `mapping_draft.json`、`char_manager_last_work.json` | 工作狀態 | 可由 GUI 續作；分享前請檢查本機路徑 |

`tool/常用字.txt` 是使用者資料，更新程式時請保留原檔內容，不要以其他字表覆蓋。
