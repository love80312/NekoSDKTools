# NekoSDKTools（個人優化版 by AI）

用於 NekoSDK／Waffle 遊戲資源的個人工具組，包含 NEKOPACK4A／NEKOPACK4S 封包、NEKOSDK_ADVSCRIPT2 腳本、BMP+ALP 圖像，以及 CP932 字元統計與 UIF 替代字元管理。

> 本專案不是通用的 PAK／TXT 解包工具。只有符合對應格式的檔案才能使用相關功能。修改後資源仍須在實際遊戲中測試。

## 功能與檔案

| 檔案／資料夾 | 用途 |
|---|---|
| `GUI.py` | Tkinter 圖形介面，整合 PAK、腳本、BMP／ALP 及字元管理 |
| `pak.py` | 解包 NEKOPACK4A／NEKOPACK4S，並可重包成 4A 或 4S |
| `scr.py` | 解析 NEKOSDK_ADVSCRIPT2，將可辨識的對話／選項匯出為 UTF-8 JSON，再匯入修改內容 |
| `bmp_alp.py` | BMP+ALP 與一般圖片之間的合併／拆分工具 |
| `char_manager.py` | CP932 字元統計及 UIF 替代字元管理；提供 GUI 與命令列功能 |
| `requirements.txt` | 圖像轉換工具的 Pillow 依賴 |
| `char_manager_README.md` | 字元管理工具的詳細操作、排除規則及命令列說明 |
| `tool/char_manager.json` | 字元管理設定、人工排除及候選排除開關 |
| `tool/char_candidate_exclusions.json` | 中文／日文標點、常用符號及自訂排除字元 |
| `tool/uif_config.json` | GUI 與命令列預設更新的 UIF 設定檔 |

## 環境需求與啟動

- Python 3.10 以上版本。
- 使用圖形介面需要 Tkinter；Windows 版 Python 安裝時需包含 Tcl/Tk。
- `pak.py`、`scr.py`、`char_manager.py` 使用 Python 標準函式庫。
- `bmp_alp.py` 需要 Pillow 10.0 以上版本。

以下命令使用 Windows Python Launcher。`py -3` 會呼叫系統已註冊的 Python 3；也可以改用 `python`，前提是 Python 已加入 PATH。

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

重包可透過環境變數調整：

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

`NEKOSDK_ADVSCRIPT2` 腳本雖然常使用 `.txt` 副檔名，內容實際上是二進位資料，檔案開頭具有：

```text
NEKOSDK_ADVSCRIPT2
```

不要因為副檔名是 `.txt`，就直接用文字編輯器開啟後另存；這可能破壞二進位腳本。

`scr.py` 只解析 `NEKOSDK_ADVSCRIPT2`，不是一般 TXT 閱讀器，也不會自動辨識其他遊戲的文字格式。若其他漢化 TXT 是一般純文字，應直接用文字編輯器檢視；若也是二進位格式，則需先確認其檔頭及資料結構，不能只靠副檔名判斷。

### 2.2 從腳本匯出 JSON

單一檔案：

```bat
py -3 scr.py -e "D:\Game\scr_unpacked\ep_300_000_000.txt" "D:\Game\ep_300_000_000.json" --encoding cp932
```

整個資料夾：

```bat
py -3 scr.py -e "D:\Game\scr_unpacked" "D:\Game\scr_json" --encoding cp932
```

匯出後的 JSON 使用 UTF-8，可用 Notepad++ 等文字編輯器檢視及編輯。資料夾模式會保留相對子資料夾結構，並將腳本副檔名改為 `.json`。

目前會匯出的項目包括：

- 對話：讀取說話者名稱及對話文字；沒有名稱時，JSON 項目只會有 `message`。
- 可辨識選項：包含「選択肢」逐行格式，以及「選択肢へ」搭配引號、斜線分隔的選項列。

只會匯出程式能辨識的文字項目，不等於將整個腳本轉成可閱讀的純文字。其他指令、控制資料及未辨識欄位不會列入翻譯 JSON。

### 2.3 JSON 項目格式

一般對話：

```json
[
  {
    "name": "角色名稱",
    "message": "對話內容"
  },
  {
    "message": "沒有說話者名稱的對話"
  }
]
```

選項：

```json
[
  {
    "choice": "選項文字 A"
  },
  {
    "choice": "選項文字 B"
  }
]
```

編輯時請注意：

- 保留 JSON 陣列的項目順序，不要任意排序。
- 不要任意增加或刪除項目；匯入時會依原始腳本的文字項目順序逐項對應。
- 對話項目必須保留 `message` 字串；`name` 可依原項目有無而保留或修改。
- 選項項目使用 `choice` 字串；單一選項不可含換行。
- JSON 使用 UTF-8；這與腳本本身使用的 CP932 等字串編碼是兩回事。

### 2.4 將修改後的 JSON 匯入腳本

單一檔案：

```bat
py -3 scr.py -i "D:\Game\scr_unpacked\ep_300_000_000.txt" "D:\Game\ep_300_000_000.json" "D:\Game\ep_300_000_000_imported.txt" --encoding cp932
```

整個資料夾：

```bat
py -3 scr.py -i "D:\Game\scr_unpacked" "D:\Game\scr_json" "D:\Game\scr_imported" --encoding cp932
```

資料夾匯入時，原始腳本與 JSON 資料夾的相對路徑必須對應；每個 ADVSCRIPT2 腳本都必須有相對應的 JSON。非 ADVSCRIPT2 檔案不需要 JSON，會原樣複製到輸出資料夾，避免重包時遺漏其他資源。

`main` 欄位可能保存與執行時文字相同的內容，也可能是另一種語言的原文／參照內容。匯入時只有在能確認文字對應關係時才同步更新；無法確認時會保留原始 `main`，避免用翻譯文字覆蓋不同語言的參照資料。原始對話指令標頭及尾段也會盡可能保留。

### 2.5 腳本字串編碼

`scr.py` 預設使用 `cp932`。可用 `--encoding` 指定 Python 支援的編碼名稱，例如：

```bat
--encoding cp932
--encoding shift_jis
--encoding utf-8
--encoding gbk
--encoding big5
```

程式會先驗證編碼名稱是否有效，再讀取或處理腳本。這個參數指定的是腳本二進位欄位中原始字串的編碼，不是 JSON 編碼，也不是遊戲語言切換功能。

對於日文場景指令與 GBK 對話文字混合的腳本，可以使用：

```bat
py -3 scr.py -e "D:\Game\script.txt" "D:\Game\script.json" --encoding cp932 --dialogue-encoding auto
```

`auto` 模式會依指令本文與 CP932 位元組的對應關係辨識 CP932 文字記錄；其他 opcode-5 對話記錄則按 GBK 處理。這是針對混合編碼資料的辨識方式，不代表遊戲引擎一定支援所有輸出字元。

CP932 無法表示的繁體中文字會在匯入時明確報錯，不會自動以問號取代。改成 UTF-8 也不代表遊戲引擎、字型或其他資源一定支援 UTF-8／繁體中文字；實際可用編碼仍須依遊戲而定。

---

## 3. BMP／ALP 圖像轉換

此功能處理 NekoSDK／Waffle 使用的 BMP+ALP 配對。BMP 儲存 RGB 圖像，ALP 儲存每像素 1 byte 的 Alpha 資料，合併後可輸出一般 RGBA 圖片。

### 3.1 合併 BMP+ALP

單組：

```bat
py -3 bmp_alp.py -e "D:\Game\image.bmp" "D:\Game\image.alp" "D:\Game\image.png"
```

批次：

```bat
py -3 bmp_alp.py -e "D:\Game\bmp_alp" "D:\Game\png"
```

單張合併可依輸出副檔名選擇 PNG、TGA、BMP、JPEG、WebP 或 TIFF；批次合併固定輸出 PNG。JPEG 不支援 Alpha，輸出 JPEG 時會轉成 RGB 並捨棄透明度。

批次模式會依相對路徑尋找同名 BMP／ALP；缺少 ALP 的 BMP 會提示並跳過，不會拿其他檔案的 ALP 代替。個別配對發生轉換錯誤時會列出錯誤並回報非零結束代碼，即使其他圖片已成功轉換，也不會把部分失敗誤報為完全成功。

### 3.2 將一般圖片拆成 BMP+ALP

單張：

```bat
py -3 bmp_alp.py -c "D:\Game\image.png" "D:\Game\image.bmp" "D:\Game\image.alp"
```

批次：

```bat
py -3 bmp_alp.py -c "D:\Game\images" "D:\Game\bmp_alp"
```

來源圖片支援 PNG、TGA、BMP、JPG／JPEG、WebP、TIF／TIFF。拆分時輸出 BMP 與 ALP；輸出 ALP 使用緊密排列的每像素 1 byte Alpha 資料。

合併時可讀取緊密排列，以及每列以 4 bytes 對齊的 ALP 資料。批次拆分若發現不同格式的來源圖片會對應到相同輸出檔名，會停止並回報，避免覆蓋其中一張圖片。

---

## 4. CP932 字元統計與替代字元管理

`char_manager.py` 可獨立執行，也由 `GUI.py` 內的「字元管理」頁籤呼叫。此功能用於統計翻譯文件中的字元，找出 CP932 無法編碼的目標字元，再建立 UIF `character_substitution` 替代映射。

### 4.1 掃描來源

翻譯文件資料夾是必要來源；原始遊戲腳本資料夾為選填。

支援翻譯文件副檔名：

- JSON、TXT、CSV、TSV
- PO、POT、FTL
- INI、XML、HTML、HTM
- Markdown、YAML、YML

JSON 只統計值中的字串，不統計欄位名稱／鍵。其他支援格式會依指定文字編碼讀取。

原始遊戲腳本必須先使用 `pak.py` 解包。選填掃描會重用 `scr.py`，依 `NEKOSDK_ADVSCRIPT2` 檔頭辨識腳本，統計可辨識的角色名稱、對話及選項文字；非腳本資源會跳過。

| 原始腳本選項 | 候選處理 |
|---|---|
| 未指定 | 仍可建立候選，但未檢查原文占用 |
| 指定且完整掃描 | 排除原始腳本已使用字元 |
| 指定但掃描不完整 | 不提供候選，避免將部分結果誤判為安全 |

原文掃描只涵蓋 `scr.py` 可辨識的文字欄位，不代表涵蓋遊戲所有資源。

### 4.2 候選排除規則

預設啟用四組排除條件，且只影響 CP932 來源候選，不會影響翻譯統計或待替換目標：

| 排除項目 | 來源 |
|---|---|
| 教育部 4,808 常用國字 | 教育部《常用國字標準字體表》 |
| 中文常用標點、引號及括號 | `tool/char_candidate_exclusions.json` |
| 日文常用標點及中點 | `tool/char_candidate_exclusions.json` |
| 星號、圖形、箭頭、數學及單位符號 | `tool/char_candidate_exclusions.json` |

教育部資料頁：
<https://language.moe.gov.tw/material/info?m=9fe3ff5a-5a8c-4817-9e60-6337dd55a509>

第一次啟用 4,808 字排除時，程式會下載官方 ODS 並驗證 4,808 個不重複漢字，之後快取在 `tool/MOE_4808_常用國字.txt`。若電腦無法下載，請手動下載教育部 ODS，放到 `tool/教育部4808個常用字.ods`。若清單數量不符，程式會停止並顯示錯誤，不會靜默使用不完整字表。

各組開關儲存在 `tool/char_manager.json`。符號清單及自訂排除字元儲存在 `tool/char_candidate_exclusions.json`。

### 4.3 GUI 操作流程

1. 開啟 `GUI.py`，切換到「字元管理」頁籤。
2. 選擇翻譯文件資料夾。
3. 視需要選擇已解包的原始遊戲腳本資料夾。
4. 設定翻譯文字、原始指令及對話編碼。
5. 選擇要啟用的四組候選排除規則。
6. 按下「開始掃描與統計」。
7. 在目標清單選擇 CP932 無法編碼的繁體中文字，再從來源候選清單選擇替代字元，建立映射。
8. 可人工排除來源候選、保存工作草稿、繼續上次工作、載入既有 UIF 映射或匯出兩行替換字串。
9. 確認映射後，按下「套用至 uif_config.json」。程式會重新掃描本次指定的來源，通過驗證後才會要求確認並更新 UIF。

套用前會建立原設定的 `.bak` 備份。更新後應重新啟動遊戲，使 UIF 重新載入設定。

### 4.4 命令列掃描

只掃描翻譯文件：

```bat
py -3 char_manager.py scan --translations "D:\Game\translation_json"
```

加入原始遊戲腳本：

```bat
py -3 char_manager.py scan --translations "D:\Game\translation_json" --source "D:\Game\original_scripts"
```

混合 CP932／GBK 腳本：

```bat
py -3 char_manager.py scan --translations "D:\Game\translation_json" --source "D:\Game\original_scripts" --command-encoding cp932 --dialogue-encoding auto
```

可選參數：

| 參數 | 說明 | 預設值 |
|---|---|---|
| `--source` | 選填；已解包的 ADVSCRIPT2 檔案或資料夾 | 不掃描 |
| `--command-encoding` | 指令／場景編碼 | `cp932` |
| `--dialogue-encoding` | 對話編碼 | `auto` |
| `--output` | 報表輸出資料夾 | 翻譯資料夾旁的 `*_char_report` |
| `--settings` | `char_manager.json` 路徑 | `tool/char_manager.json` |
| `--text-encoding` | TXT／CSV 等翻譯文字編碼 | `utf-8-sig` |

### 4.5 命令列套用 CSV

掃描後，開啟報表資料夾內的 `mapping_template.csv`，在「來源候選字元（自行填入）」欄位填入來源字元，再執行：

```bat
py -3 char_manager.py apply --mapping "D:\Game\translation_json_char_report\mapping_template.csv"
```

可用 `--config` 指定 UIF `uif_config.json`；可用 `--settings` 指定字元管理設定。套用時會依 `scan_metadata.json` 找回原翻譯及選填原始腳本資料夾並重新掃描。候選排除清單或教育部字表快取若在掃描後變更，必須重新掃描，不可直接套用舊 CSV。

**不可只搬走 `mapping_template.csv`，必須保留完整報表資料夾。**

### 4.6 報表檔案用途

掃描完成後，預設在翻譯資料夾旁建立 `*_char_report`：

| 檔案 | 用途 |
|---|---|
| `README_報表說明.txt` | 本次掃描條件及各報表說明 |
| `character_report.csv` | 翻譯字元統計、出現次數、檔案數及 CP932 狀態 |
| `source_character_report.csv` | 原始腳本已辨識文字的統計；僅指定原文時產生 |
| `cp932_glyphs.csv` | CP932 字形目錄及各字的使用狀態、排除原因與候選狀態 |
| `safe_candidate_glyphs.csv` | 最終可用來源候選 |
| `mapping_template.csv` | 命令列／試算表映射範本 |
| `unmapped_characters.txt` | 尚未映射的目標字元 |
| `scan_metadata.json` | 路徑、編碼、排除設定、字表雜湊及完整性資訊 |
| `mapping_draft.json` | GUI 工作草稿；執行保存後才產生 |

原始腳本未指定時，`source_character_report.csv` 不會產生；若報表資料夾內有上次留下的同名舊檔，程式會移除，避免誤認為本次結果。

---

## 5. 專案檔案與清理

| 檔案／資料夾 | 判定 | 說明 |
|---|---|---|
| `GUI.py`、`pak.py`、`scr.py`、`bmp_alp.py`、`char_manager.py` | 必要 | 各主功能程式 |
| `char_manager_README.md` | 建議保留 | 字元管理詳細操作文件 |
| `tool/char_manager.json` | 建議保留 | 保存使用者排除項目及編碼設定 |
| `tool/char_candidate_exclusions.json` | 必要 | 預設標點、符號及自訂排除清單 |
| `tool/uif_config.json` | 依使用方式保留 | 預設 UIF 設定；套用映射會更新此檔 |
| `tool/MOE_4808_常用國字.txt` | 自動產生 | 教育部清單快取；刪除後下次啟用會重新取得 |
| `tool/教育部4808個常用字.ods` | 選填 | 官方 ODS 手動下載備援 |
| `tool/CP932_漢字符號字形清單.csv`、`.txt` | 參考資料 | 不屬於程式執行依賴，可依需要保留 |
| `tool/winmm.dll` | 需另行確認 | Python 程式沒有直接引用，但可能供遊戲端注入環境使用，不建議未確認用途就刪除 |

`mapping_draft.json`、`mapping_draft.json.bak`、`char_manager_last_work.json`、`*.bak`、`__pycache__` 及掃描報表屬於工作狀態、備份或輸出資料，不是主程式原始碼。

---

## 6. 已驗證項目

以下是既有程式搭配測試素材的工具層級驗證紀錄。位元組一致代表封包解包／重包或腳本匯入／匯出後資料符合比對條件，不代表已在遊戲內確認所有重包檔均能正常載入。

| 測試素材 | 測試內容 | 結果 |
|---|---|---|
| `scr.pak` | 解包 245 個腳本；未修改匯出／匯入後逐位元組比較 | 245／245 相同 |
| `scr.pak` | 34,043 筆對話及 2 筆選項加入測試文字，匯入後再次匯出 JSON | 245／245 份 JSON 完全一致 |
| `ep_300_000_000.txt` | 501 筆底層記錄，匯出 118 筆文字；修改後匯入並再次匯出 | 118 筆完全一致 |
| `scr.pak` | PAK 解包、重包、再次解包後比較全部檔案 | 245／245 相同 |
| `sys_v1.pak` | PAK 解包、重包、再次解包後比較全部檔案 | 14／14 相同 |
| NEKOPACK4S 範例 | 原始封包解包、4S 重包、再次解包，比對腳本 SHA-256 | 461,008 bytes 完全一致 |
| NEKOPACK4A 測試封包 | 兩個檔案重包、再次解包後逐位元組比較 | 完全一致 |
| BMP／ALP 範例 | 合併有對應 ALP 的 BMP；處理缺少 ALP 的 BMP | 5 組合併成功；2 個缺少 ALP 的 BMP 提示並跳過 |

上述為既有人工測試紀錄，不是每次啟動或提交時都會自動執行的測試。實際使用前仍請保留原始 PAK／腳本備份，並在遊戲內測試修改後資源。
