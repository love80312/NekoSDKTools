# NekoSDKTools（個人優化版 by AI）

用於 NekoSDK／Waffle 遊戲資源的個人工具組，包含 NEKOPACK4A／NEKOPACK4S 封包、NEKOSDK_ADVSCRIPT2 腳本、BMP+ALP 圖像，以及 CP932 字元統計與 UIF 替代字元管理。

> 本專案不是通用的 PAK／TXT 解包工具。只有符合對應格式的檔案才能使用相關功能。修改後資源仍須在實際遊戲中測試。

## 功能與檔案

| 檔案／資料夾 | 用途 |
|---|---|
| GUI.py | Tkinter 圖形介面，整合 PAK、腳本、BMP／ALP 及字元管理 |
| pak.py | 解包 NEKOPACK4A／NEKOPACK4S，並可重包成 4A 或 4S |
| scr.py | 解析 NEKOSDK_ADVSCRIPT2，將可辨識的對話／選項匯出為 UTF-8 JSON，再匯入修改內容 |
| bmp_alp.py | BMP+ALP 與一般圖片之間的合併／拆分工具 |
| char_manager.py | 獨立命令列工具，也提供 GUI 使用的 CharacterManagerFrame；統計翻譯字元並管理 CP932 替代映射 |
| requirements.txt | 圖像轉換工具的 Pillow 依賴 |
| char_manager_README.md | 字元管理工具的詳細操作及命令列說明 |
| TEST/ | 手動回歸測試素材，不是程式執行所需檔案 |
| tool/ | UIF 設定、字元管理設定及 CP932 字形參考資料 |

## 環境需求與啟動

- Python 3.10 以上版本。
- 使用圖形介面需要 Tkinter；Windows 版 Python 安裝時需包含 Tcl/Tk。
- pak.py、scr.py、char_manager.py 使用 Python 標準函式庫。
- bmp_alp.py 需要 Pillow 10.0 以上版本。

以下命令使用 Windows Python Launcher。py -3 會呼叫系統已註冊的 Python 3；也可以改用 python，前提是 Python 已加入 PATH。

安裝圖像工具依賴：

```bat
py -3 -m pip install -r requirements.txt
```

啟動圖形介面：

```bat
py -3 GUI.py
```

GUI.py、pak.py、scr.py、bmp_alp.py、char_manager.py 請放在同一個資料夾。字元管理功能直接由 GUI.py 載入 char_manager.py，不需要另外啟動第二套 GUI，也不需要 char_manager_session.py。

---

## 1. PAK 封包：解包與重包

pak.py 支援以下兩種 NekoSDK 封包：

| 格式 | 結構 | 解包 | 重包 |
|---|---|---:|---:|
| NEKOPACK4A | 索引區可包含多筆檔案紀錄 | 支援 | 支援 |
| NEKOPACK4S | 單一檔案紀錄 | 支援 | 支援，來源資料夾必須恰好有一個檔案 |

兩種格式的索引配置不同，不能只替換檔頭就互相轉換。程式會依格式分別建立索引，並共用檔案資料的 Zlib 壓縮、大小資訊及 XOR 遮罩處理。

### 1.1 解包

A、S 格式使用相同命令，程式會自動辨識封包檔頭：

```bat
py -3 pak.py -u "D:\Game\scr.pak" "D:\Game\scr_unpacked"
```

### 1.2 重包

預設輸出 NEKOPACK4A，可以包含一個或多個檔案：

```bat
py -3 pak.py -p "D:\Game\scr_imported" "D:\Game\scr_new.pak"
```

指定輸出 NEKOPACK4A：

```bat
py -3 pak.py -p "D:\Game\scr_imported" "D:\Game\scr_new.pak" --format 4A
```

指定輸出 NEKOPACK4S：

```bat
py -3 pak.py -p "D:\Game\single_file_folder" "D:\Game\single_new.pak" --format 4S
```

**4S 格式限制：** 來源資料夾內必須恰好有一個檔案，可位於子資料夾。若有零個或多個檔案，程式會停止並回報數量，不會輸出不符合格式的封包。

GUI 的「PAK 封包」頁籤也可在重包時選擇 4A 或 4S。解包時會自動辨識格式；選擇 4S 重包時，GUI 會先檢查來源檔案數量，pak.py 仍會再次檢查。

### 1.3 重包設定

重包可透過環境變數調整：

| 環境變數 | 說明 | 預設值 |
|---|---|---:|
| NEKO_PAK_LEVEL | Zlib 壓縮等級，範圍 0–9 | 9 |
| NEKO_PAK_WORKERS | 壓縮工作執行緒，需為正整數 | 1 |

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

NEKOSDK_ADVSCRIPT2 腳本雖然常使用 .txt 副檔名，內容實際上是二進位資料，檔案開頭具有：

```text
NEKOSDK_ADVSCRIPT2
```

不要因為副檔名是 .txt，就直接用文字編輯器開啟後另存；這可能破壞二進位腳本。

scr.py 只解析 NEKOSDK_ADVSCRIPT2，不是一般 TXT 閱讀器，也不會自動辨識其他遊戲的文字格式。若其他漢化 TXT 是一般純文字，應直接用文字編輯器檢視；若也是二進位格式，則需先確認其檔頭及資料結構，不能只靠副檔名判斷。

### 2.2 從腳本匯出 JSON

單一檔案：

```bat
py -3 scr.py -e "D:\Game\scr_unpacked\ep_300_000_000.txt" "D:\Game\ep_300_000_000.json" --encoding cp932
```

整個資料夾：

```bat
py -3 scr.py -e "D:\Game\scr_unpacked" "D:\Game\scr_json" --encoding cp932
```

匯出後的 JSON 使用 UTF-8，可用 Notepad++ 等文字編輯器檢視及編輯。資料夾模式會保留相對子資料夾結構，並將腳本副檔名改為 .json。

目前會匯出的項目包括：

- 對話：讀取說話者名稱及對話文字；沒有名稱時，JSON 項目只會有 message。
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
- 對話項目必須保留 message 字串；name 可依原項目有無而保留或修改。
- 選項項目使用 choice 字串；單一選項不可含換行。
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

main 欄位可能保存與執行時文字相同的內容，也可能是另一種語言的原文／參照內容。匯入時只有在能確認文字對應關係時才同步更新；無法確認時會保留原始 main，避免用翻譯文字覆蓋不同語言的參照資料。原始對話指令標頭及尾段也會盡可能保留。

### 2.5 腳本字串編碼

scr.py 預設使用 cp932。可用 --encoding 指定 Python 支援的編碼名稱，例如：

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

auto 模式會依指令本文與 CP932 位元組的對應關係辨識 CP932 文字記錄；其他 opcode-5 對話記錄則按 GBK 處理。這是針對混合編碼資料的辨識方式，不代表遊戲引擎一定支援所有輸出字元。

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

char_manager.py 可獨立執行，也由 GUI.py 內的「字元管理」頁籤呼叫。此功能用於統計翻譯文件中的字元，找出 CP932 無法編碼的目標字元，再建立 UIF character_substitution 替代映射。

### 4.1 掃描來源

**只需要翻譯文件資料夾，不需要原始遊戲腳本，也不需要先把原始 PAK 再交給字元管理器掃描。**

支援的副檔名：

- JSON、TXT、CSV、TSV
- PO、POT、FTL
- INI、XML、HTML、HTM
- Markdown、YAML、YML

JSON 只統計值中的字串，不統計欄位名稱／鍵。其他支援格式會依指定文字編碼讀取。

字元統計保留漢字、Unicode 標點及符號，排除英文、日文假名、數字及空白。預設安全候選為 CP932 可編碼的非 ASCII 漢字、標點或符號。

### 4.2 GUI 操作流程

1. 開啟 GUI.py，切換到「字元管理」頁籤。
2. 選擇翻譯文件資料夾，確認文字編碼。
3. 按下「開始掃描與統計」。
4. 在目標字元清單選擇 CP932 無法編碼的繁體中文字，再從安全候選清單選擇替代來源字元，建立映射。
5. 可排除不希望使用的來源候選；排除設定會保存於 tool/char_manager.json。
6. 可保存工作草稿、繼續上次工作、手動載入既有 UIF 映射，或匯出兩行替換字串。
7. 確認映射後，按下「套用至 uif_config.json」。程式會重新掃描翻譯文件，檢查來源字元及映射衝突，再要求確認並建立 .bak 備份。

套用後仍需重新啟動遊戲，讓 UIF 重新載入設定。

### 4.3 替代字元的重要限制

候選字元會排除翻譯文件已使用的字元、既有映射及人工排除字元；但**目前版本不會掃描原始遊戲腳本**。因此安全候選只代表未出現在本次翻譯文件及已知排除清單中，不代表原始遊戲文字沒有使用該字元。

正式套用前，仍應自行確認來源候選沒有與原始遊戲文字、其他資源或控制字元衝突。CP932 可編碼也不代表遊戲字型一定具有該字形，必須在遊戲內實際測試。

詳細操作、CSV 欄位及命令列參數請參閱 [char_manager_README.md](char_manager_README.md)。

### 4.4 命令列掃描

```bat
py -3 char_manager.py scan --translations "D:\Game\translation_json"
```

可選參數：

| 參數 | 用途 | 預設值 |
|---|---|---|
| --output | 報表輸出資料夾 | 翻譯資料夾旁的 *_char_report |
| --settings | 字元管理設定檔 | tool/char_manager.json |
| --text-encoding | TXT／CSV 等文字文件編碼 | utf-8-sig |

### 4.5 命令列套用 CSV

掃描後，開啟報表資料夾內的 mapping_template.csv，在「來源候選字元（自行填入）」欄填入替代來源字元，再執行：

```bat
py -3 char_manager.py apply --mapping "D:\Game\translation_json_char_report\mapping_template.csv"
```

可用 --config 指定 UIF uif_config.json；可用 --settings 指定字元管理設定。套用時會依 scan_metadata.json 找回原翻譯資料夾並重新掃描，因此不可只搬走 mapping_template.csv，必須保留完整報表資料夾。

GUI 與命令列預設使用 tool/uif_config.json 作為 UIF 設定檔。若要套用至其他遊戲，請先選擇或指定該遊戲實際使用的 UIF 設定檔。

---

## 5. 字元管理報表與資料檔

掃描完成後，預設在翻譯資料夾旁建立 *_char_report 資料夾：

| 檔案 | 內容 |
|---|---|
| character_report.csv | 翻譯中出現的漢字及特殊符號、使用次數、檔案數、CP932 可編碼狀態 |
| cp932_glyphs.csv | CP932 字形目錄，包含 Unicode、字元名稱、CP932 位元組及候選狀態 |
| safe_candidate_glyphs.csv | 已排除翻譯占用、既有映射及人工排除項目的候選來源字元 |
| mapping_template.csv | 待替換目標字元及手動填入來源候選的範本 |
| unmapped_characters.txt | 尚未映射的目標字元 |
| scan_metadata.json | 翻譯資料夾、編碼、掃描數量與完整性資訊；CSV 套用時會使用 |
| mapping_draft.json | GUI 保存的工作草稿；執行保存後才會產生 |

tool/ 內另外附有 CP932_漢字符號字形清單.csv 與 CP932_漢字符號字形清單.txt，作為靜態參考資料。程式掃描時會自行建立 CP932 字形目錄，並不依賴這兩個檔案才能執行。

---

## 6. 專案檔案整理與可刪除項目

以下是依 T2 程式引用關係整理的判定。這個清單是清理建議，不代表所有項目都已從分支刪除。

| 檔案／資料夾 | 判定 | 原因 |
|---|---|---|
| char_manager_session.py | 可刪除候選 | GUI.py 已直接匯入 char_manager.py；工作草稿、續作、預覽、載入 UIF 及匯出兩行字串等功能已存在於 char_manager.py，沒有必要再載入這個繼承擴充模組 |
| TEST/ | 可移出公開分支 | 內含手動測試用 PAK／腳本素材，不是程式執行依賴，也沒有自動測試程式會讀取；移除前應本機備份，並確認是否有素材散布權限 |
| tool/CP932_漢字符號字形清單.csv、tool/CP932_漢字符號字形清單.txt | 可選參考資料 | Python 程式會自行建立字形目錄，不會讀取這兩份靜態清單；若仍需要完整字形參考，建議保留 |
| tool/winmm.dll | 與本工具程式無直接依賴 | Python 原始碼沒有引用此 DLL；若它是遊戲端 UIF／注入環境所需檔案，且本專案仍打算一併提供該環境，才需要保留。公開前應確認其授權與散布條件 |
| tool/char_manager.json | 建議保留 | 保存人工排除來源字元與文字編碼；刪除後會使用程式預設值，可能失去既有設定 |
| tool/uif_config.json | 不建議刪除 | 是字元管理器預設讀寫的 UIF 設定檔；套用替代映射會更新此檔並建立 .bak |
| char_manager_README.md | 保留 | 作為字元管理器的詳細操作手冊，由主 README 連結 |
| GUI.py、pak.py、scr.py、bmp_alp.py、char_manager.py | 不可刪除 | 分別是 GUI、封包、腳本、圖像及字元管理的主要程式 |

另請勿將執行時產生的 mapping_draft.json、char_manager_last_work.json、*.bak、__pycache__ 或翻譯報表誤當成必要原始碼。這些屬於工作狀態、備份或輸出資料，應依是否需要續作／還原再決定是否保留。

---

## 7. 已驗證項目

以下是目前程式搭配測試素材的工具層級驗證紀錄。位元組一致代表解包／重包或腳本匯入／匯出後資料符合比對條件，不代表已在遊戲內確認所有重包檔均能正常載入。

| 測試素材 | 測試內容 | 結果 |
|---|---|---|
| scr.pak | 解包 245 個腳本；未修改匯出／匯入後逐位元組比較 | 245／245 相同 |
| scr.pak | 34,043 筆對話及 2 筆選項加入測試文字，匯入後再次匯出 JSON | 245／245 份 JSON 完全一致 |
| ep_300_000_000.txt | 501 筆底層記錄，匯出 118 筆文字；修改後匯入並再次匯出 | 118 筆完全一致 |
| scr.pak | PAK 解包、重包、再次解包後比較全部檔案 | 245／245 相同 |
| sys_v1.pak | PAK 解包、重包、再次解包後比較全部檔案 | 14／14 相同 |
| NEKOPACK4S 範例 | 原始封包解包、4S 重包、再次解包，比對腳本 SHA-256 | 461,008 bytes 完全一致 |
| NEKOPACK4A 測試封包 | 兩個檔案重包、再次解包後逐位元組比較 | 完全一致 |
| BMP／ALP 範例 | 合併有對應 ALP 的 BMP；處理缺少 ALP 的 BMP | 5 組合併成功；2 個缺少 ALP 的 BMP 提示並跳過 |

上述為人工執行的測試紀錄，不是每次啟動或提交時都會自動執行的測試。TEST/ 目前保存部分測試用素材，並非所有歷史測試素材都包含在儲存庫中。

實際使用前仍請保留原始 PAK／腳本備份，並在遊戲內測試修改後資源。
