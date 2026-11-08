# NekoSDKTools 字元管理工具

`char_manager.py` 是獨立的 CP932 字元統計與 UIF 替代字元管理工具，也提供 `GUI.py` 使用的 `CharacterManagerFrame`。使用 Python 3.10 以上版本，僅使用 Python 標準函式庫；原始腳本解析重用同目錄的 `scr.py`。

## 功能

本工具會統計翻譯文件中的漢字、標點及符號，列出 CP932 無法編碼的目標字元，再從 CP932 字形目錄找出可作為替代來源的候選字元。

候選來源會依下列條件過濾：

1. 翻譯文件已使用的字元。
2. 選填的原始遊戲 ADVSCRIPT2 腳本已使用的字元。
3. 教育部《常用國字標準字體表》4,808 個常用國字。
4. 中文常用標點、引號及括號。
5. 日文常用標點及中點。
6. 星號、圖形、箭頭、數學及單位符號。
7. 自訂排除清單、人工排除字元及目前工作映射已占用的字元。

**排除規則只影響 CP932 來源候選，不影響翻譯字元統計或待替換目標清單。**

## GUI 操作

1. 啟動 `GUI.py`，開啟「字元管理」頁籤。
2. 選擇翻譯文件資料夾。這是必要來源。
3. 視需要選擇已解包的原始遊戲腳本資料夾。此欄位為選填。
4. 設定翻譯文字編碼；JSON 固定以 UTF-8／UTF-8 BOM 讀取。
5. 設定原始腳本指令／場景及對話編碼。預設為指令 `cp932`、對話 `auto`。
6. 勾選要啟用的候選排除項目。
7. 按「開始掃描與統計」。
8. 從左側選擇 CP932 不支援的目標字元，從右側選擇來源候選，按「建立配對」。
9. 不適合使用的來源字元可人工排除；排除清單會保存於 `tool/char_manager.json`。
10. 可保存工作草稿、繼續上次工作、手動載入既有 UIF 映射，或匯出兩行替換字串。
11. 按「套用至 uif_config.json」後，程式會重新掃描翻譯文件；若本次曾提供原始腳本，也會重新掃描原始腳本。驗證完成後才會要求確認及更新 UIF。

### 原始遊戲腳本（選填）

原始遊戲資源必須先使用 `pak.py` 解包。字元管理器不會直接掃描 PAK 封包。

原始腳本欄位接受已解包的資料夾；掃描會遞迴尋找檔案，依 `NEKOSDK_ADVSCRIPT2` 檔頭辨識，不會只憑 `.txt` 副檔名判斷。

解析工作重用 `scr.py`，統計它能辨識的角色名稱、對話及選項文字。這不代表已掃描遊戲中的所有資源、圖片文字、UI 字串或字型。

- 未指定原始腳本：仍可建立候選，但不檢查候選是否出現在原始遊戲文字。
- 已指定且完整掃描：排除原始腳本中已使用的字元。
- 已指定但掃描不完整：候選清單會保持空白；不允許將部分掃描結果當成安全候選，也不允許套用映射。

對話編碼 `auto` 使用 `scr.py` 現有的混合 CP932／GBK 辨識方式。若遊戲資料有不同編碼，請先確認後再指定。

## 候選排除清單

### 教育部 4,808 常用國字

預設啟用，採用教育部《常用國字標準字體表》4,808 字。只排除其中與 CP932 字形目錄重疊的字元，不會影響翻譯統計。

教育部資料頁：
<https://language.moe.gov.tw/material/info?m=9fe3ff5a-5a8c-4817-9e60-6337dd55a509>

第一次掃描且啟用此項時，程式會：

1. 優先讀取 `tool/MOE_4808_常用國字.txt`。
2. 若沒有文字快取，檢查 `tool/教育部4808個常用字.ods`。
3. 若本機也沒有 ODS，嘗試從教育部官方 ODS 下載。
4. 驗證清單必須恰好包含 4,808 個不重複漢字。
5. 成功後建立 `tool/MOE_4808_常用國字.txt`，後續掃描使用此快取，不必再次下載。

官方 ODS 直接下載網址：
<https://language.moe.gov.tw/uploads/files/17694982751710.ods>

若電腦無法連線下載，請手動下載官方檔案，放到 `tool/教育部4808個常用字.ods`，再重新掃描。程式不會在字數驗證失敗時靜默使用不完整清單。

### 常用符號

以下三組可以分別開關，清單內容存放於 `tool/char_candidate_exclusions.json`：

| 分組 | 涵蓋範圍 |
|---|---|
| `zh_punctuation` | 中文常用標點、引號、括號、刪節號及破折號 |
| `ja_punctuation` | 日文句讀點、引號、括號及中點 |
| `common_symbols` | 星號、圖形、箭頭、數學符號及單位符號 |

該 JSON 可自行補充或移除字元。`custom_excluded_characters` 可填入自訂排除字元字串，無須修改 Python 程式。自訂清單不受前三個分類開關影響，會固定排除。

### 人工排除

GUI 的「排除選取來源字元」及「清空人工排除」會修改 `tool/char_manager.json` 的 `excluded_source_characters`。人工排除只作用於來源候選，不會刪除翻譯統計中的字元。

## 獨立命令列掃描

只掃描翻譯文件：

```bat
py -3 char_manager.py scan --translations "D:\Game\translation_json"
```

同時掃描已解包的原始腳本：

```bat
py -3 char_manager.py scan --translations "D:\Game\translation_json" --source "D:\Game\original_scripts"
```

指定混合編碼：

```bat
py -3 char_manager.py scan --translations "D:\Game\translation_json" --source "D:\Game\original_scripts" --command-encoding cp932 --dialogue-encoding auto
```

可選參數：

| 參數 | 說明 | 預設值 |
|---|---|---|
| `--source` | 選填；已解包的 ADVSCRIPT2 檔案或資料夾 | 不掃描 |
| `--command-encoding` | 原始腳本指令／場景編碼 | `cp932` |
| `--dialogue-encoding` | 原始腳本對話編碼 | `auto` |
| `--output` | 報表輸出資料夾 | 翻譯資料夾旁的 `*_char_report` |
| `--settings` | 字元管理設定檔 | `tool/char_manager.json` |
| `--text-encoding` | TXT／CSV 等翻譯文件編碼 | `utf-8-sig` |

排除選項由 `tool/char_manager.json` 的 `candidate_filters` 控制。命令列模式沒有額外的互動式勾選介面。

## 掃描報表資料夾

掃描完成後，預設在翻譯資料夾旁建立 `*_char_report`。可使用 `--output` 指定其他位置。

| 檔案 | 用途 |
|---|---|
| `README_報表說明.txt` | 記錄本次掃描條件、原文掃描狀態及各報表用途 |
| `character_report.csv` | 翻譯文件中的漢字及特殊符號統計、出現次數、檔案數及 CP932 狀態 |
| `source_character_report.csv` | 原始腳本已辨識文字的字元統計；只有指定原始腳本時產生 |
| `cp932_glyphs.csv` | CP932 完整字形目錄，附翻譯／原文使用狀態、排除分類、映射占用、可用候選及排除原因 |
| `safe_candidate_glyphs.csv` | 通過本次全部啟用條件的 CP932 來源候選清單 |
| `mapping_template.csv` | 命令列／試算表工作流程使用的映射範本，填入來源字元後可交給 `apply` |
| `unmapped_characters.txt` | 尚未映射的 CP932 不支援目標字元純文字清單 |
| `scan_metadata.json` | 掃描路徑、編碼、排除設定、清單雜湊及掃描完整性；CSV 套用時用來重新驗證 |
| `mapping_draft.json` | GUI 保存的工作草稿；執行保存後才產生 |
| `mapping_draft.json.bak` | 草稿上一版備份；有覆寫過草稿才產生 |

`source_character_report.csv` 在未指定原始腳本時不會產生；若同一報表資料夾原本有舊檔，程式會刪除舊檔，避免誤認為本次掃描結果。

`cp932_glyphs.csv` 的「排除原因」可同時列出多個原因。原文未掃描時，「原文已使用」會標示為「未掃描」，不會誤顯示為「否」。

`mapping_template.csv` 是掃描時產生的命令列範本。GUI 後續工作映射會更新 `cp932_glyphs.csv` 與候選報表，但不會覆寫使用者可能已手動編輯的 `mapping_template.csv`。

## CSV 手動建立映射

1. 開啟完整報表資料夾內的 `mapping_template.csv`。
2. 在「來源候選字元（自行填入）」欄位，為每個「目標中文字」填入一個 CP932 來源字元。
3. 來源字元不可重複，目標字元也不可重複；保留 CSV 原有欄位名稱。
4. 保存 CSV，並保留同一資料夾的 `scan_metadata.json`。
5. 執行：

```bat
py -3 char_manager.py apply --mapping "D:\Game\translation_json_char_report\mapping_template.csv"
```

可選參數：

| 參數 | 說明 | 預設值 |
|---|---|---|
| `--config` | UIF `uif_config.json` 路徑 | `tool/uif_config.json` |
| `--settings` | `char_manager.json` 路徑 | `tool/char_manager.json` |

套用時會重新掃描翻譯文件；初次掃描若有指定原始腳本，也會重新掃描原始腳本。候選排除設定或教育部清單快取在掃描後發生變更時，必須重新掃描，不可直接套用舊 CSV。

不可只搬走 `mapping_template.csv`；必須保留完整報表資料夾，且原始翻譯資料夾及已指定的原始腳本資料夾仍須存在。

## UIF 替代映射安全原則

- 目標字元是翻譯中 CP932 無法編碼、需要替代顯示的文字。
- 來源字元必須是 CP932 可編碼的非 ASCII 漢字、標點或符號。
- 翻譯文件已使用的來源字元不得作為新映射。
- 指定原始腳本時，原文已使用的來源字元不得作為新映射。
- 新映射必須符合目前啟用的常用字及符號排除設定。
- 套用前會重新掃描並驗證；任何必要掃描不完整都會拒絕寫入 UIF。
- CP932 可編碼不代表遊戲字型一定具有該字形，仍須在遊戲內實際測試。
- 本工具只更新 UIF 的 `character_substitution` 區段，不會自動將舊格式轉換為 `text_processor`。

## 設定與檔案

| 路徑 | 用途 |
|---|---|
| `tool/char_manager.json` | 人工排除、翻譯文字編碼、原始腳本編碼及四組候選排除開關 |
| `tool/char_candidate_exclusions.json` | 中文／日文標點、常用符號及自訂排除字元 |
| `tool/MOE_4808_常用國字.txt` | 首次取得官方字表後建立的 4,808 字快取 |
| `tool/教育部4808個常用字.ods` | 官方 ODS 的手動下載備援位置 |
| `tool/uif_config.json` | GUI 與命令列預設更新的 UIF 設定檔 |
| `tool/char_manager_last_work.json` | GUI 上次工作索引 |
| 報表資料夾內的 `mapping_draft.json` | GUI 工作草稿 |

設定檔及工作草稿可能包含本機路徑或個人工作狀態；分享專案前請先檢查是否要一併提交。
