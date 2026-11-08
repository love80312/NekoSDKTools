# UIF 字元配對與翻譯文件替換工具

`uif_char_helper.py` 是獨立輔助工具，不匯入 `char_manager.py`，也不會自動讀取 GitHub 專案內的 `tool/uif_config.json`。

## 啟動

```bat
py -3 uif_char_helper.py
```

工具會開啟獨立 GUI。請分別選取：

1. 遊戲資料夾內實際使用的 `uif_config.json`。
2. `char_manager.py` 掃描後產生的 `*_char_report` 資料夾。

按「讀取設定與報表」後，工具會比對本次翻譯的待替換字元與遊戲 UIF 既有映射；摘要會列出本次已經由 UIF 處理的字元及其在 `character_report.csv` 中的出現次數。再按「自動一對一配對」即可使用剩餘安全候選字建立新配對。

## 自動配對規則

- 目標字依 `unmapped_characters.txt` 的原始順序處理。
- 來源候選字依 `safe_candidate_glyphs.csv` 的原始順序處理。
- UIF 已經使用的來源字不會再次分配。
- 會另外讀取同一報表的 `mapping_pairs.csv`，保留 `char_manager.py` 已人工建立的映射，避免這些目標字從待替換清單消失後被漏掉。
- UIF 已經映射的目標字不會再次配對。
- 新增映射的來源與目標各自不得重複。
- 候選不足時只建立可完成的配對，並顯示剩餘數量；不會重複使用候選字。
- 既有 UIF 目標字重複時仍保留原始順序及內容；`repeat` 不會併入兩行映射。
- 只要翻譯掃描不完整，或指定的原始腳本掃描不完整，就會停止載入並要求重新掃描。

## 匯出兩行映射

「匯出完整兩行字串」與「複製完整兩行」會輸出完整結果：

```text
source_characters 的完整來源字串
target_characters 的完整目標字串
```

實際檔案第二行不會有前置空白。第一行包含原 UIF 的來源字及本次新增來源字；第二行同位置放置對應目標字。複製後分別貼到遊戲 UIF 的 `source_characters` 與 `target_characters`。

工具不會修改、覆寫或備份原始 `uif_config.json`。

## 翻譯文件替換

「開始替換並另存」會使用完整 UIF 映射，將翻譯文件中的目標字替換成對應來源字，並輸出到另一個資料夾。原始資料夾不可作為輸出位置，已存在且非空的輸出資料夾也會被拒絕。

- JSON：只替換字串值，不修改鍵；保留原始縮排、空白及其他 JSON 格式。
- JSON 一律依字元管理器既有行為使用 UTF-8 讀取，不受一般 TXT 編碼欄位影響。
- 其他文字格式依 `scan_metadata.json` 的 `translation_text_encoding` 讀寫，也可在 GUI 指定覆寫編碼。
- 不支援的非文字檔會原樣複製。
- 遇到讀取、解碼或 JSON 格式錯誤時，該檔原樣複製並列出警告，不會靜默略過。
- 原始檔不會被修改。

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

若需要覆寫一般文字檔編碼，可加入 `--encoding cp932`、`--encoding gbk` 或其他 Python 支援的編碼名稱。JSON 仍固定以 UTF-8 處理。

## 與 char_manager.py 的關係

`char_manager.py` 仍只負責掃描、候選篩選、人工配對及報表輸出，不依賴本工具，也不讀取 UIF。

這版同時修正 `unmapped_characters.txt`：會排除 `char_manager.json` 的 `excluded_target_characters`，並將排除清單寫入 `scan_metadata.json`。更新 `char_manager.py` 後，請重新掃描一次，再把新報表提供給 `uif_char_helper.py`。
