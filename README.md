# NekoSDKTools（個人優化版）

用於 NekoSDK 遊戲資源的個人工具組，涵蓋 NEKOPACK4A 封包、NEKOSDK_ADVSCRIPT2 腳本，以及 Waffle BMP+ALP 圖像。

> 本專案不是通用的 PAK／TXT 解包工具。只有符合上述格式的檔案，才能使用對應功能處理。

## 工具功能

| 檔案 | 功能 |
|---|---|
| `GUI.py` | Tkinter 圖形介面，整合封包、腳本及圖像操作 |
| `pak.py` | 解包及重包 `NEKOPACK4A` 格式的 `.pak` |
| `scr.py` | 讀取 `NEKOSDK_ADVSCRIPT2` 腳本，將可辨識的對話／選項匯出為 UTF-8 JSON，並可匯入修改內容 |
| `bmp_alp.py` | 合併 BMP+ALP 為一般 RGBA 圖片，或將一般圖片拆成 BMP+ALP |
| `requirements.txt` | 列出圖像工具使用的 Pillow 依賴 |

## 環境需求與啟動

- Python 3.10 以上版本。
- 使用圖形介面需要 Tkinter；Windows 版 Python 安裝時需包含 Tcl/Tk。
- `pak.py` 與 `scr.py` 使用 Python 標準函式庫，不需要額外套件。
- `bmp_alp.py` 需要 Pillow 10.0 以上版本。

安裝圖像工具依賴：

```bat
py -3.13 -m pip install -r requirements.txt
```

啟動圖形介面：

```bat
py -3.13 GUI.py
```

`GUI.py`、`pak.py`、`scr.py`、`bmp_alp.py` 請放在同一個資料夾。GUI 透過目前使用的 Python 執行子工具，並在下方顯示執行指令、處理訊息及錯誤。

## 圖形介面功能

| 頁籤 | 操作 |
|---|---|
| PAK 封包 | PAK 解包、資料夾重包；重包可設定 Zlib 壓縮等級及工作執行緒 |
| 腳本／JSON | ADVSCRIPT2 腳本匯出 JSON、JSON 匯入腳本；可選腳本字串編碼 |
| BMP／ALP 圖像 | 單組／批次合併 BMP+ALP，以及單張／批次拆分圖片 |

GUI 的 PAK 重包設定範圍為壓縮等級 0–9、工作執行緒 1–16；預設等級 9、單執行緒。舊電腦建議先維持單執行緒。

---

## 1. PAK 封包：解包與重包

`pak.py` 僅針對 `NEKOPACK4A` 格式。封包內檔名使用 CP932，檔案資料使用 Zlib 壓縮，並有封包格式本身的索引及資料遮罩處理；不代表能處理其他遊戲或其他種類的 `.pak`。

解包：

```bat
py -3.13 pak.py -u "D:\Game\scr.pak" "D:\Game\scr_unpacked"
```

重包：

```bat
py -3.13 pak.py -p "D:\Game\scr_imported" "D:\Game\scr_new.pak"
```

重包設定可透過環境變數調整：

| 環境變數 | 說明 | 預設值 |
|---|---|---:|
| `NEKO_PAK_LEVEL` | Zlib 壓縮等級，範圍 0–9 | `9` |
| `NEKO_PAK_WORKERS` | 壓縮工作執行緒，需為正整數 | `1` |

例如：

```bat
set NEKO_PAK_LEVEL=6
set NEKO_PAK_WORKERS=1
py -3.13 pak.py -p "D:\Game\scr_imported" "D:\Game\scr_new.pak"
```

輸出 PAK 必須放在來源資料夾之外。封包內檔名依 CP932 編碼寫入；若檔名無法使用 CP932 表示，重包會報錯。

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
py -3.13 scr.py -e "D:\Game\scr_unpacked\ep_300_000_000.txt" "D:\Game\ep_300_000_000.json" --encoding cp932
```

整個資料夾：

```bat
py -3.13 scr.py -e "D:\Game\scr_unpacked" "D:\Game\scr_json" --encoding cp932
```

匯出後的 JSON 使用 UTF-8，可用 Notepad++ 等文字編輯器檢視及編輯。資料夾模式會保留相對子資料夾結構，並將腳本副檔名改為 `.json`。

目前會匯出的項目包括：

- 對話：讀取說話者名稱及對話文字；沒有名稱時，JSON 項目只會有 `message`。
- 可辨識選項：包含 `選択肢` 逐行格式，以及 `選択肢へ` 搭配引號、斜線分隔的選項列。

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
py -3.13 scr.py -i "D:\Game\scr_unpacked\ep_300_000_000.txt" "D:\Game\ep_300_000_000.json" "D:\Game\ep_300_000_000_imported.txt" --encoding cp932
```

整個資料夾：

```bat
py -3.13 scr.py -i "D:\Game\scr_unpacked" "D:\Game\scr_json" "D:\Game\scr_imported" --encoding cp932
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

CP932 無法表示的繁體中文字會在匯入時明確報錯，不會自動以問號取代。改成 UTF-8 也不代表遊戲引擎、字型或其他資源一定支援 UTF-8／繁體中文字；實際可用編碼仍須依遊戲而定。

---

## 3. BMP／ALP 圖像轉換

此功能處理 NekoSDK／Waffle 使用的 BMP+ALP 配對。BMP 儲存 RGB 圖像，ALP 儲存每像素 1 byte 的 Alpha 資料，合併後可輸出一般 RGBA 圖片。

### 3.1 合併 BMP+ALP

單組：

```bat
py -3.13 bmp_alp.py -e "D:\Game\image.bmp" "D:\Game\image.alp" "D:\Game\image.png"
```

批次：

```bat
py -3.13 bmp_alp.py -e "D:\Game\bmp_alp" "D:\Game\png"
```

單張合併可依輸出副檔名選擇 PNG、TGA、BMP、JPEG、WebP 或 TIFF；批次合併固定輸出 PNG。JPEG 不支援 Alpha，輸出 JPEG 時會轉成 RGB 並捨棄透明度。

批次模式會依相對路徑尋找同名 BMP／ALP；缺少 ALP 的 BMP 會提示並跳過，不會拿其他檔案的 ALP 代替。

### 3.2 將一般圖片拆成 BMP+ALP

單張：

```bat
py -3.13 bmp_alp.py -c "D:\Game\image.png" "D:\Game\image.bmp" "D:\Game\image.alp"
```

批次：

```bat
py -3.13 bmp_alp.py -c "D:\Game\images" "D:\Game\bmp_alp"
```

來源圖片支援 PNG、TGA、BMP、JPG／JPEG、WebP、TIF／TIFF。拆分時輸出 BMP 與 ALP；輸出 ALP 使用緊密排列的每像素 1 byte Alpha 資料。

合併時可讀取緊密排列，以及每列以 4 bytes 對齊的 ALP 資料。批次拆分若發現不同格式的來源圖片會對應到相同輸出檔名，會停止並回報，避免覆蓋其中一張圖片。

---

## 已驗證項目

以下為目前 `T1` 分支程式搭配測試素材的往返驗證紀錄。往返一致代表解包／重包或腳本匯入／匯出後資料符合比對條件，不代表已在遊戲內確認所有重包檔均能正常載入。

| 測試素材 | 測試內容 | 結果 |
|---|---|---|
| `scr.pak` | 解包 245 個腳本；未修改匯出／匯入後逐位元組比較 | 245／245 相同 |
| `scr.pak` | 34,043 筆對話及 2 筆選項加入測試文字，匯入後再次匯出 JSON | 245／245 份 JSON 完全一致 |
| `ep_300_000_000.txt` | 501 筆底層記錄，匯出 118 筆文字；修改後匯入並再次匯出 | 118 筆完全一致 |
| `scr.pak` | PAK 解包、重包、再次解包後比較全部檔案 | 245／245 相同 |
| `sys_v1.pak` | PAK 解包、重包、再次解包後比較全部檔案 | 14／14 相同 |
| BMP／ALP 範例 | 合併有對應 ALP 的 BMP；處理缺少 ALP 的 BMP | 5 組合併成功；2 個缺少 ALP 的 BMP 提示並跳過 |

測試素材未全部包含在此儲存庫中。實際使用前仍請保留原始 PAK／腳本備份，並在遊戲內測試修改後資源。
