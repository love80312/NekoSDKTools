# NekoSDKTools 字元管理工具

`char_manager.py` 是獨立的 CP932 字元統計與替代字元管理工具，也提供 `GUI.py` 使用的 `CharacterManagerFrame`。使用 Python 3.10 以上版本，僅使用 Python 標準函式庫；原始腳本解析重用同目錄的 `scr.py`。

**本工具不讀取、不修改、不合併任何 UIF 設定。** 所有配對只存在於本次工作區與工作草稿。完成後只能匯出兩行替換字串，由使用者自行貼入要使用的設定檔。

## 主要功能

- 掃描 JSON、TXT、CSV、TSV、PO、POT、FTL、INI、XML、HTML、Markdown、YAML 等翻譯文件。
- 統計漢字、標點及符號；排除英文、日文假名、數字及空白。
- 找出 CP932 無法編碼的翻譯目標字元。
- 建立 CP932 可編碼的來源候選目錄，並追蹤每個候選的使用狀態與排除原因。
- `tool/常用字.txt` 作為使用者自訂常用字排除清單。
- 可選擇掃描已解包的原始 ADVSCRIPT2 腳本，排除原文已使用的字元。
- 支援人工排除、建立／移除配對、保存工作草稿及繼續上次工作。
- 匯出只有兩行的 UTF-8 替換字串。

## 常用字清單

`tool/常用字.txt` 是本專案使用者自行整理的常用字清單，不是教育部 4,808 字表。檔案採 UTF-8 單行字元集合格式；每個字元視為一個集合成員，重複字元會自然去重，空白及換行不納入。

預設啟用「排除 tool/常用字.txt」。程式會將清單與 CP932 字形目錄比對，只排除有出現在清單中的 CP932 來源候選。此規則：

- 只影響來源候選，不影響翻譯字元統計。
- 不影響待替換目標清單。
- 不影響原始遊戲腳本統計。
- 不會連線下載、更新或驗證固定字數。
- 清單內容改變後，必須重新掃描；舊掃描結果不能直接匯出。

需要更改排除字元時，直接編輯 `tool/常用字.txt` 即可，不必修改 Python 程式。

## 其他候選排除清單

`tool/char_candidate_exclusions.json` 保存標點、符號及自訂排除字元：

| 分組 | 涵蓋範圍 |
|---|---|
| `zh_punctuation` | 中文常用標點、引號、括號、刪節號及破折號 |
| `ja_punctuation` | 日文句讀點、引號、括號及中點 |
| `common_symbols` | 星號、圖形、箭頭、數學符號及單位符號 |
| `custom_excluded_characters` | 使用者自行指定的排除字元 |

前三組可在 GUI 分別啟用或停用。`custom_excluded_characters` 固定排除，不受前三組開關影響。

人工排除字元保存在 `tool/char_manager.json` 的 `excluded_source_characters`，也只影響來源候選。

## GUI 操作

1. 啟動 `GUI.py`，開啟「字元管理」頁籤。
2. 選擇翻譯文件資料夾。這是必要來源。
3. 視需要選擇已解包的原始遊戲腳本資料夾。此欄位為選填。
4. 設定翻譯文字編碼、原始腳本指令／場景編碼及對話編碼。
5. 設定常用字、中文標點、日文標點及常用符號排除開關。
6. 按「開始掃描與統計」。
7. 在左側選擇 CP932 不支援的目標字元，在右側選擇來源候選字元，按「建立配對」。
8. 可移除配對、人工排除來源候選、清空人工排除，或保存工作草稿。
9. 按「匯出兩行替換字串」，將本次工作區的配對匯出成 TXT。
10. 將匯出檔自行貼入要使用的設定檔。程式不會替你讀取或修改設定檔。

### 匯出格式

匯出檔固定為 UTF-8，內容只有兩行：

```text
來源字元排列
目標字元排列
```

第一行是 `source_characters`，第二行是 `target_characters`，同一欄位置的兩個字元互相對應。來源及目標字元各自不得重複，兩行字元數量必須相同。檔案不會加入欄位名稱、引號、逗號或說明文字。

## 原始遊戲腳本（選填）

原始遊戲資源必須先使用 `pak.py` 解包。字元管理器不會直接掃描 PAK 封包。

原始腳本資料夾會遞迴搜尋檔案，依 `NEKOSDK_ADVSCRIPT2` 檔頭辨識，不會只憑 `.txt` 副檔名判斷。解析工作重用 `scr.py`，統計它能辨識的角色名稱、對話及選項文字。

| 原始腳本選項 | 候選處理 |
|---|---|
| 未指定 | 仍可建立候選，但未檢查原文占用 |
| 指定且完整掃描 | 排除原始腳本中已使用的字元 |
| 指定但掃描不完整 | 安全候選清空，不允許建立或匯出映射 |

原文掃描只涵蓋 `scr.py` 可辨識的文字欄位，不代表涵蓋遊戲所有資源、圖片文字、UI 字串或字型。混合 CP932／GBK 腳本預設使用指令 `cp932`、對話 `auto`；`auto` 會使用 `scr.py` 現有的辨識方式。

## 掃描不完整時的處理

翻譯文件是必要來源。若部分翻譯文件讀取失敗，程式仍會產生統計報表供檢查，但 `safe_candidate_glyphs.csv` 會清空，GUI 也不會允許建立或匯出映射。若指定了原始腳本而其中有檔案解析失敗，處理方式相同。

這可避免未讀取的文件內其實已使用某個來源字元，卻被誤判為安全候選。修正讀取錯誤後，請重新掃描。

## 報表資料夾

掃描完成後，預設在翻譯資料夾旁建立 `*_char_report`。可用 CLI 的 `--output` 指定其他位置。

| 檔案 | 用途 |
|---|---|
| `README_報表說明.txt` | 本次掃描條件及報表用途 |
| `character_report.csv` | 翻譯字元統計、出現次數、檔案數及 CP932 狀態 |
| `source_character_report.csv` | 原始腳本可辨識文字統計；只有指定原始腳本時產生 |
| `cp932_glyphs.csv` | CP932 字形目錄、翻譯／原文使用狀態、排除命中及排除原因 |
| `safe_candidate_glyphs.csv` | 通過本次所有必要掃描與排除條件的來源候選 |
| `unmapped_characters.txt` | 尚未映射的 CP932 不支援目標字元；GUI 更新配對後會同步更新 |
| `mapping_pairs.csv` | 本次工作區目前建立的配對 |
| `scan_metadata.json` | 掃描路徑、編碼、排除設定、清單雜湊及掃描完整性 |
| `mapping_draft.json` | GUI 保存的工作草稿；保存後才產生 |
| `mapping_draft.json.bak` | 草稿上一版備份；覆寫既有草稿時產生 |

`source_character_report.csv` 在未指定原始腳本時不會產生；若報表資料夾內有上次留下的同名舊檔，程式會移除，避免誤認為本次掃描結果。

`cp932_glyphs.csv` 的「排除原因」可同時列出多個原因。原文未掃描時，「原文已使用」會標示為「未掃描」，不會誤顯示為「否」。

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
| `--source` | 選填；已解包的原始 ADVSCRIPT2 腳本檔案或資料夾 | 不掃描 |
| `--command-encoding` | 原始腳本指令／場景編碼 | `cp932` |
| `--dialogue-encoding` | 原始腳本對話編碼 | `auto` |
| `--output` | 報表輸出資料夾 | 翻譯資料夾旁的 `*_char_report` |
| `--settings` | 字元管理設定檔 | `tool/char_manager.json` |
| `--text-encoding` | TXT／CSV 等翻譯文件編碼 | `utf-8-sig` |

命令列只提供掃描及報表輸出，不提供直接套用設定檔的功能。

## UIF 使用方式與安全原則

- 本工具不讀取任何既有 UIF 設定或替代映射。
- 本工具不寫入、合併、覆蓋或備份 UIF 設定。
- 不提供載入外部映射的 GUI 功能。
- 工作草稿只保存本次掃描後在工作區建立的配對。
- 舊版工作草稿可能包含過去載入的外部映射；新版不會自動還原舊版草稿，以免其他版本的字元混入。
- 匯出檔只有兩行替換字串；由使用者自行貼入選定設定檔的 `source_characters` 與 `target_characters`。
- CP932 可編碼不代表遊戲字型一定具有該字形，仍須在遊戲內實際測試。

## 設定與檔案

| 路徑 | 用途 |
|---|---|
| `tool/常用字.txt` | 使用者自訂的常用字清單；UTF-8 單行字元集合 |
| `tool/char_manager.json` | 人工排除、文字編碼及候選排除開關 |
| `tool/char_candidate_exclusions.json` | 中文／日文標點、常用符號及自訂排除字元 |
| `tool/char_manager_last_work.json` | GUI 上次工作索引 |
| 報表資料夾內的 `mapping_draft.json` | GUI 工作草稿 |

設定檔、工作索引及草稿可能包含本機路徑或工作狀態；分享專案前請先檢查是否要一併提交。
