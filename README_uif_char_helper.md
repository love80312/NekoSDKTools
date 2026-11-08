# UIF 字元配對與翻譯文件替換工具

`uif_char_helper.py` 是 UIF 輔助工具的功能核心。它可獨立啟動，也可由主程式 `GUI.py` 直接嵌入為「字元管理」右側的同層級分頁。新版只需要一個主 GUI，不必使用 `GUI_with_uif.py`。

## 啟動

主程式（PAK、腳本／JSON、BMP／ALP、字元管理及 UIF 輔助工具）：

```bat
py -3 GUI.py
```

單獨啟動 UIF 輔助工具：

```bat
py -3 uif_char_helper.py
```

主程式的 UIF 分頁和獨立 GUI 使用同一個 `UIFCharHelperApp`，不會複製另一套功能邏輯。 主程式內的 UIF 頁面可使用右側捲軸或滑鼠滾輪；摘要文字框與配對清單的內部滾動不會被外層頁面攔截。

## 輸入資料

請在 UIF 分頁分別選取：

1. 遊戲資料夾內實際使用的 `uif_config.json`。
2. `char_manager.py` 掃描後產生的 `*_char_report` 資料夾。

不要把專案內的 `tool/uif_config.json` 當成遊戲實際使用的 UIF 設定。工具不會自動讀取它，也不會自行修改遊戲的 UIF 設定。

按「讀取設定與報表」後，工具會保留 UIF 既有映射，再合併報表 `mapping_pairs.csv` 中的 char_manager 人工映射。接著才處理尚未映射的目標字與安全候選字。GUI 與 CLI 都使用相同合併順序。

若翻譯掃描不完整，或指定的原始腳本掃描不完整，工具會停止載入並要求重新掃描。切換 UIF／報表路徑後，必須重新讀取；載入失敗時會清除舊資料，不會沿用上一個遊戲的配對。

## 自動配對規則

- UIF 既有映射依原順序保留。
- char_manager `mapping_pairs.csv` 人工映射接續在 UIF 既有映射之後。
- 目標字依 `unmapped_characters.txt` 的原始順序處理。
- 來源候選字依 `safe_candidate_glyphs.csv` 的原始順序處理。
- UIF 與人工映射已使用的來源字不會再次分配。
- UIF 與人工映射已處理的目標字不會再次配對。
- 新增映射的來源與目標各自不得重複。
- 候選不足時只建立可完成的配對，並顯示剩餘數量；不會重複使用候選字。
- 既有 UIF 目標字重複時仍保留原始順序及內容；`repeat` 不會併入雙行映射。

## 匯出兩行映射

「匯出完整兩行字串」與「複製完整兩行」會輸出完整結果：

```text
source_characters 的完整來源字串
target_characters 的完整目標字串
```

第一行排列順序是 UIF 既有來源字、char_manager 人工映射來源字、本次新增來源字；第二行相同位置放置對應目標字。複製後分別貼到遊戲 UIF 的 `source_characters` 與 `target_characters`。

工具不會修改、覆寫或備份原始 `uif_config.json`。匯出只產生兩行字串，不會直接套用到遊戲設定。

## 翻譯文件替換

「開始替換並另存」會使用完整 UIF 映射，將翻譯文件中的目標字替換成對應來源字，並輸出到另一個資料夾。原始資料夾不可作為輸出位置，輸出資料夾不可位於來源資料夾內，已存在且非空的輸出資料夾也會被拒絕。

- JSON：只替換字串值，不修改鍵；保留原始縮排、空白及其他 JSON 格式，固定以 UTF-8 處理。
- 其他支援文字格式：依 `scan_metadata.json` 的 `translation_text_encoding` 讀寫，也可在 GUI／CLI 指定覆寫編碼。
- 不支援的非文字檔會原樣複製。
- 遇到讀取、解碼或 JSON 格式錯誤時，該檔原樣複製並列出警告，不會靜默略過。
- 處理完成後會檢查翻譯文字值中仍不能以 CP932 編碼的字元，列出檔案、字元、Unicode 編碼及次數。

**CP932 檢查只會提出警告，不會強制把 UTF-8 或其他翻譯檔重新編碼為 CP932。** 未配對的字元仍保留在輸出檔案中，需回到 char_manager 報表補配或人工處理。JSON 的 CP932 檢查只掃描字串值，不掃描鍵；JSON 中即使使用 `\\u` escape 表示字元，也會先解碼後檢查。

處理資料夾時，工具會跳過來源子目錄及檔案中的符號連結／Junction。若指定的翻譯來源根目錄本身是符號連結或 Junction，則會停止處理。

UIF 若有多個來源字對應同一目標字，反向替換無法判斷原本意圖。替換時採用 UIF 內第一個來源字，並在結果中列出歧義目標字。

## 命令列

自動配對並匯出完整兩行：

```bat
py -3 uif_char_helper.py --no-gui --uif "D:\Game\uif_config.json" --report "D:\Translation_char_report" --auto-pair --export-two-line "D:\Game\uif_mapping.txt"
```

自動配對並替換翻譯資料夾：

```bat
py -3 uif_char_helper.py --no-gui --uif "D:\Game\uif_config.json" --report "D:\Translation_char_report" --auto-pair --translations "D:\Translation" --output "D:\Translation_replaced"
```

若需要覆寫一般文字檔編碼，可加入 `--encoding cp932`、`--encoding gbk` 或其他 Python 支援的編碼名稱。JSON 仍固定以 UTF-8 處理。CLI 會合併 UIF 既有映射、char_manager 人工映射及本次新增配對。

## 與 char_manager.py 的關係

`char_manager.py` 的掃描、候選篩選、人工配對及報表邏輯仍不讀取或修改 UIF。主 GUI 只是在「字元管理」右側新增本工具的獨立分頁；兩套資料仍各自獨立，UIF 既有映射不會寫回 char_manager 工作草稿。

`char_manager.py` 的 `unmapped_characters.txt` 會排除 `char_manager.json` 的 `excluded_target_characters`，並將排除清單寫入 `scan_metadata.json`。更新 `char_manager.py` 後，請重新掃描一次，再載入新報表。
