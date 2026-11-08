# NekoSDKTools 字元管理工具

char_manager.py 是獨立的 CP932 字元統計與 UIF 替代字元管理工具，也提供 GUI.py 使用的 CharacterManagerFrame。使用 Python 3.10 以上版本，僅使用 Python 標準函式庫。

## 功能與限制

本工具掃描翻譯文件，統計其中的漢字、標點及符號，列出 CP932 無法編碼的目標字元，並提供 CP932 安全候選供手動建立替代映射。

**目前版本只掃描翻譯文件，不需要也不會掃描原始遊戲腳本。** 因此候選來源只保證未出現在本次翻譯文件、既有映射及人工排除清單中；使用前仍須自行確認原始遊戲文字沒有使用候選字元。

支援副檔名：

- JSON、TXT、CSV、TSV
- PO、POT、FTL
- INI、XML、HTML、HTM
- Markdown、YAML、YML

JSON 只統計值中的字串，不統計欄位名稱／鍵。其他格式依指定文字編碼讀取。統計會排除英文、日文假名、數字及空白，保留漢字與 Unicode 標點／符號。

## GUI 操作

1. 啟動 GUI.py，開啟「字元管理」頁籤。
2. 選擇翻譯文件資料夾，指定 TXT／CSV 等文字文件的編碼。
3. 按「開始掃描與統計」。
4. 在目標字元清單選取 CP932 無法編碼的字元，再從 CP932 來源候選清單選取替代字元，建立映射。
5. 不適合使用的候選字元可加入人工排除清單；排除狀態保存於 tool/char_manager.json。
6. 可保存工作草稿、繼續上次工作、手動載入 UIF 既有映射或匯出兩行替換字串。
7. 按「套用至 uif_config.json」後，程式會重新掃描翻譯文件、驗證新來源字元及映射衝突，要求使用者確認，再更新 UIF 設定。

套用 UIF 前會建立原設定的 .bak 備份。更新後應重新啟動遊戲，使 UIF 重新載入設定。

## 獨立命令列掃描

```bat
py -3 char_manager.py scan --translations "D:\Game\translation_json"
```

可選參數：

| 參數 | 說明 | 預設值 |
|---|---|---|
| --output | 報表輸出資料夾 | 翻譯資料夾旁的 *_char_report |
| --settings | char_manager.json 路徑 | tool/char_manager.json |
| --text-encoding | TXT／CSV 等翻譯文件編碼 | utf-8-sig |

範例：

```bat
py -3 char_manager.py scan --translations "D:\Game\translation_json" --output "D:\Game\char_report" --text-encoding utf-8
```

## 報表內容

掃描完成後會在翻譯資料夾旁建立 *_char_report 資料夾，或使用 --output 指定位置。

| 檔案 | 內容 |
|---|---|
| character_report.csv | 翻譯實際使用的漢字及特殊符號、次數、檔案數、CP932 可否編碼及映射狀態 |
| cp932_glyphs.csv | CP932 字形目錄，包含 Unicode、字元名稱、類別、CP932 位元組及候選狀態 |
| safe_candidate_glyphs.csv | 排除翻譯已用字元、既有映射及人工排除項目後的來源候選 |
| mapping_template.csv | 尚未映射的目標字元及手動填入來源候選的欄位 |
| unmapped_characters.txt | 尚未映射的目標字元清單 |
| scan_metadata.json | 翻譯來源路徑、編碼、掃描數量、完整性及錯誤資訊 |
| mapping_draft.json | GUI 保存的工作草稿；只有執行保存後才會產生 |

程式會在掃描時依 Python 的 CP932 codec 動態建立字形目錄，不依賴 tool/ 內的靜態字形清單。

## CSV 手動建立映射

1. 開啟完整報表資料夾內的 mapping_template.csv。
2. 在「來源候選字元（自行填入）」欄位，為每個「目標中文字」填入一個 CP932 來源字元。
3. 來源字元不可重複，目標字元也不可重複；保留 CSV 原有欄位名稱。
4. 保存 CSV，並保留同一資料夾的 scan_metadata.json。
5. 執行下列套用命令。

```bat
py -3 char_manager.py apply --mapping "D:\Game\translation_json_char_report\mapping_template.csv"
```

可選參數：

| 參數 | 說明 | 預設值 |
|---|---|---|
| --config | UIF uif_config.json 路徑 | tool/uif_config.json |
| --settings | char_manager.json 路徑 | tool/char_manager.json |

範例：

```bat
py -3 char_manager.py apply --mapping "D:\Game\char_report\mapping_template.csv" --config "D:\Game\UIF\uif_config.json"
```

套用時會依 scan_metadata.json 找回原翻譯資料夾並重新掃描，檢查來源字元是否已出現在最新翻譯文字中。因此不可只搬走 mapping_template.csv；必須保留完整報表資料夾，且原翻譯資料夾仍須存在。

## 替代映射安全原則

- 目標字元是翻譯中 CP932 無法編碼、需要替代顯示的文字。
- 來源字元必須是 CP932 可編碼的非 ASCII 漢字、標點或符號。
- 安全候選會排除翻譯文件已使用的字元、既有映射及人工排除字元。
- 套用前會重新掃描翻譯文件，檢查掃描完整性及來源字元占用情形。
- 原始遊戲腳本不在目前掃描範圍內。請自行確認來源候選沒有出現在原始遊戲文字、其他資源或控制資料中。
- CP932 可編碼不代表遊戲字型一定具有該字形，仍須在遊戲內實際測試。
- 本工具只更新 UIF 的 character_substitution 區段，不會自動將舊格式轉換為 text_processor。

## 設定與檔案

- tool/char_manager.json：保存人工排除的來源字元及文字文件編碼。
- tool/uif_config.json：GUI 與命令列預設更新的 UIF 設定檔；可自行指定其他設定檔。
- tool/CP932_漢字符號字形清單.csv、tool/CP932_漢字符號字形清單.txt：額外附帶的靜態參考清單，不是程式執行依賴。
- mapping_draft.json：保存於報表資料夾的 GUI 工作草稿。
- tool/char_manager_last_work.json：GUI 上次工作索引；執行續作功能時會使用。

設定檔及工作草稿可能包含本機路徑或個人工作狀態；分享專案前請先檢查是否要一併提交。
