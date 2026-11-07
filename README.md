# NekoSDKTools

NekoSDK／Waffle 遊戲資源的個人工具組，提供 **NEKOPACK4A／4S 封包處理、NEKOSDK_ADVSCRIPT2 腳本文字匯出／匯入，以及 BMP／ALP 圖像轉換**。

> 本專案不是通用 PAK 解包器，也不是一般 TXT 編輯器。只有符合本工具所支援資料格式的檔案才能處理。修改前請備份原始資源；工具處理成功不代表遊戲一定能載入，仍須以實際遊戲測試。

## 目錄

- [功能與檔案](#功能與檔案)
- [環境需求與啟動](#環境需求與啟動)
- [建議工作流程](#建議工作流程)
- [1. PAK 封包：解包與重包](#1-pak-封包解包與重包)
- [2. ADVSCRIPT2 腳本與 JSON](#2-advscript2-腳本與-json)
- [3. BMP／ALP 圖像轉換](#3-bmpalp-圖像轉換)
- [測試素材與驗證紀錄](#測試素材與驗證紀錄)
- [限制與注意事項](#限制與注意事項)

## 功能與檔案

| 檔案 | 功能 | 相依套件 |
|---|---|---|
| `GUI.py` | Tkinter 圖形介面，整合 PAK、腳本／JSON、BMP／ALP 三組操作 | Python、Tkinter |
| `pak.py` | 解包 NEKOPACK4A／4S；重包時可指定 4A 或 4S | Python 標準函式庫 |
| `scr.py` | 解析 NEKOSDK_ADVSCRIPT2，匯出可辨識的對話／選項至 UTF-8 JSON，並將 JSON 匯回腳本 | Python 標準函式庫 |
| `bmp_alp.py` | 合併 BMP+ALP 為一般 RGBA 圖片，或將圖片拆成 BMP+ALP | Pillow |
| `requirements.txt` | 列出圖像轉換所需套件 | — |
| `TEST/` | 範例 PAK 與腳本素材 | — |

## 環境需求與啟動

- Python **3.10 以上**。
- 使用圖形介面需要 Tkinter；Windows 安裝 Python 時需包含 Tcl/Tk。
- `pak.py`、`scr.py` 不需要額外 Python 套件。
- `bmp_alp.py` 需要 Pillow **10.0.0 以上**。GUI 本身不依賴 Pillow，但執行圖像轉換時會使用它。

安裝 Pillow：

```bat
py -3 -m pip install -r requirements.txt
```

啟動圖形介面：

```bat
py -3 GUI.py
```

也可以直接執行各個命令列工具，不必啟動 GUI。若系統沒有 Python Launcher，可將 `py -3` 改成已加入 PATH 的 `python`。

**檔案位置：** `GUI.py`、`pak.py`、`scr.py`、`bmp_alp.py` 請放在同一個資料夾。GUI 會使用啟動 GUI 時的同一個 Python 解譯器呼叫子工具，在執行記錄中顯示完整命令與輸出；長行會自動折行，也可停止正在執行的工作。

---

## 建議工作流程

1. 備份遊戲原始 PAK，不要直接在唯一一份原始資料上操作。
2. 使用 PAK 功能解包至獨立資料夾。
3. 使用腳本／JSON 功能掃描解包資料夾，匯出有效的 ADVSCRIPT2 腳本。
4. 編輯 UTF-8 JSON，保留陣列項目數量與順序。
5. 將 JSON 匯入原始解包資料夾，輸出至另一個資料夾。匯入時非 ADVSCRIPT2 資源會一併複製。
6. 將匯入後的完整資料夾重包成 PAK。格式應依原始封包選擇 4A 或 4S。
7. 將新封包放回遊戲測試；確認文字、選項、圖片、載入與流程均正常後，再決定是否替換原始資源。

---

## 1. PAK 封包：解包與重包

`pak.py` 支援兩種已辨識的 NekoSDK 封包格式：

| 格式 | 封包索引 | 解包 | 重包 |
|---|---|---:|---:|
| `NEKOPACK4A` | 多筆檔案紀錄，可包含多個檔案 | 支援 | 支援 |
| `NEKOPACK4S` | 單筆檔案紀錄 | 支援 | 支援，來源資料夾必須恰好有一個檔案 |

兩種格式的索引配置不同，**不能只修改檔頭就把 4A 當成 4S，或反向轉換**。重包時會依所選格式重新建立索引及資料區。

目前程式處理的封包資料包含 Zlib 壓縮資料、未壓縮大小資訊，以及資料開頭的 XOR 遮罩。封包內檔名使用 **CP932**；檔名無法以 CP932 編碼時，重包會報錯。

### 1.1 解包

解包會自動辨識 PAK 檔頭中的 4A／4S 類型：

```bat
py -3 pak.py -u "D:/Game/data.pak" "D:/Work/data_unpacked"
```

### 1.2 重包

預設輸出 `NEKOPACK4A`，可包含一個或多個檔案：

```bat
py -3 pak.py -p "D:/Work/data_unpacked" "D:/Game/data_new.pak"
```

明確指定 4A：

```bat
py -3 pak.py -p "D:/Work/data_unpacked" "D:/Game/data_new.pak" --format 4A
```

指定 4S：

```bat
py -3 pak.py -p "D:/Work/single_file" "D:/Game/single_new.pak" --format 4S
```

**4S 限制：** 來源資料夾及其子資料夾合計必須恰好有一個檔案。零個或多個檔案都會停止重包，不會產生不符合格式的封包。

建議依原始 PAK 的格式選擇輸出格式。即使工具能解析兩種格式，也不代表特定遊戲或資源載入器接受兩者互換。

### 1.3 壓縮設定

重包可透過環境變數調整 Zlib 壓縮等級及並行工作數：

| 環境變數 | 用途 | 預設值 |
|---|---|---:|
| `NEKO_PAK_LEVEL` | Zlib 壓縮等級，範圍 0–9 | `9` |
| `NEKO_PAK_WORKERS` | 壓縮工作執行緒數，必須是正整數 | `1` |

範例：

```bat
set NEKO_PAK_LEVEL=6
set NEKO_PAK_WORKERS=1
py -3 pak.py -p "D:/Work/data_unpacked" "D:/Game/data_new.pak" --format 4A
```

壓縮等級越高通常越偏向縮小封包，但需要更多 CPU 時間。增加工作執行緒可能提高多檔案重包速度，也會增加 CPU 與記憶體使用量。GUI 的工作執行緒選項限制為 1–16；命令列則接受任何正整數。

輸出 PAK 必須放在來源資料夾之外。程式會檢查不安全路徑、重複路徑、檔案／資料夾衝突及符號連結；解包會先將內容寫入暫存區，完成後才提交至輸出位置。若遇到損壞資料或路徑衝突，會停止並回報錯誤。

---

## 2. ADVSCRIPT2 腳本與 JSON

### 2.1 TXT 副檔名不代表純文字

`NEKOSDK_ADVSCRIPT2` 腳本常使用 `.txt` 副檔名，但內容是二進位記錄資料，檔案開頭具有下列識別字串：

```text
NEKOSDK_ADVSCRIPT2
```

不要將這類檔案當成一般文字檔開啟後另存。即使檔名是 `.txt`，也必須先確認檔案簽章與內部結構。其他遊戲的純文字 TXT 不屬於 `scr.py` 的處理範圍。

### 2.2 匯出 JSON

單一腳本：

```bat
py -3 scr.py -e "D:/Game/scripts/ep_000_001_000.txt" "D:/Work/ep_000_001_000.json" --encoding cp932 --dialogue-encoding auto
```

整個資料夾：

```bat
py -3 scr.py -e "D:/Game/scripts" "D:/Work/scripts_json" --encoding cp932 --dialogue-encoding auto
```

資料夾模式會遞迴掃描子資料夾，保留相對路徑，並將有效 ADVSCRIPT2 腳本的副檔名改為 `.json`。沒有 ADVSCRIPT2 簽章的一般資源會略過，不會匯出成 JSON；若檔案具有 ADVSCRIPT2 簽章但內部結構損壞，則會回報錯誤，不會當成一般資源靜默跳過。

匯出的 JSON 使用 **UTF-8**，可使用 Notepad++ 等文字編輯器修改。工具只匯出已辨識的對話及選項，不會把所有腳本指令、控制資料或其他欄位轉成完整純文字。

目前辨識的文字類型包括：

- **對話記錄：** 讀取說話者名稱及對話文字。沒有說話者名稱時，JSON 項目不包含 `name`。
- **場景選項：** 可解析 `選択肢` 後逐行排列的選項，以及 `選択肢へ` 後以括號／引號和斜線分隔的選項。
- **獨立選項記錄：** 依可辨識的選項標記及文字欄位擷取選項。

這是針對目前已分析的 ADVSCRIPT2 結構所做的解析，不代表所有遊戲腳本變體都能完整辨識。

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

JSON 是依原始腳本記錄順序輸出的單一陣列。匯入時也是依相同順序逐項對應，並非依文字內容或自訂 ID 配對。編輯時請遵守：

- **不要排序、增加或刪除陣列項目。** 匯入時會檢查項目是否不足、欄位型別是否正確，以及是否有未使用的多餘項目。
- 對話項目必須有字串型別的 `message`；`name` 可省略，省略時保留原說話者名稱。
- 選項項目必須有字串型別的 `choice`。
- 說話者名稱不可包含換行；單一選項不可包含換行。
- 保留 JSON 陣列與項目的結構，不要將對話項目改成選項項目，反之亦然。

### 2.4 匯入 JSON

單一腳本：

```bat
py -3 scr.py -i "D:/Game/scripts/ep_000_001_000.txt" "D:/Work/ep_000_001_000.json" "D:/Work/imported/ep_000_001_000.txt" --encoding cp932 --dialogue-encoding auto
```

整個資料夾：

```bat
py -3 scr.py -i "D:/Game/scripts" "D:/Work/scripts_json" "D:/Work/scripts_imported" --encoding cp932 --dialogue-encoding auto
```

資料夾匯入時：

1. 原始腳本資料夾與 JSON 資料夾的相對路徑必須對應。
2. 每個有效 ADVSCRIPT2 腳本都必須有相對應的 JSON。
3. 非 ADVSCRIPT2 檔案不需要 JSON，會原樣複製到輸出資料夾，讓輸出資料夾可作為後續 PAK 重包的完整來源。
4. 在輸出前會先驗證腳本、JSON 項目與編碼轉換；批次處理不會在尚有其他檔案未驗證時就先提交部分結果。

匯入時會盡可能保留原始腳本記錄、指令標頭及對話尾段。部分文字也會出現在 `main` 欄位的顯示指令中；只有在能確認原始文字對應位置時才同步更新。若無法安全確認，就保留原本的 `main` 內容，避免覆蓋不相關資料。

### 2.5 腳本字串編碼：請注意 GUI 與 CLI 的預設差異

腳本中的命令／場景欄位、對話／說話者欄位不一定使用相同編碼。JSON 本身固定使用 UTF-8；這與腳本二進位欄位內的字串編碼是不同層次。

| 設定 | GUI 預設 | CLI 預設 |
|---|---|---|
| `--encoding`：指令／場景編碼 | `cp932` | `cp932` |
| `--dialogue-encoding`：對話／說話者編碼 | `auto` | 未指定時沿用 `--encoding`，因此預設為 `cp932` |

**重要：GUI 的對話編碼預設是 `auto`，但命令列不會自動套用這個設定。** 針對本分支所處理的 CP932 指令／場景、混合 CP932／GBK 對話素材，命令列請明確加上：

```bat
--encoding cp932 --dialogue-encoding auto
```

目前 `auto` 模式的判定方式如下：

- 先以指令／場景編碼（此處為 CP932）讀取 opcode-5 對話記錄的 `main` 欄位。
- 若 `main` 中 `[テキスト表示]` 後的文字內容，經換行正規化後可與對話欄位相符，該筆對話及說話者使用 CP932。
- 其他 opcode-5 對話及說話者使用 GBK。
- 獨立選項文字在 `auto` 模式下使用 GBK；場景選項仍依指令／場景編碼處理。

這是依目前腳本欄位間的對應關係所做的**特定混合編碼判定**，不是適用於任意檔案的通用編碼偵測器。若素材不是這種混合格式，請改成明確的編碼設定。

指定固定編碼的例子：

```bat
py -3 scr.py -e "D:/Game/scripts" "D:/Work/scripts_json" --encoding cp932 --dialogue-encoding cp932
```

程式會先驗證編碼名稱，並以嚴格模式解碼及編碼。若翻譯文字無法由指定編碼表示，會明確報錯，不會自動以問號取代。**改用 GBK、UTF-8 或其他編碼，不代表遊戲引擎會以該編碼讀取，也不代表遊戲字型具備所需字形。** 請先確認遊戲實際使用的編碼、引擎支援及字型資源，再決定匯入設定。

---

## 3. BMP／ALP 圖像轉換

此功能處理 NekoSDK／Waffle 使用的 BMP+ALP 配對：BMP 儲存 RGB 圖像，ALP 儲存每像素 1 byte 的 Alpha 透明度資料。合併後可輸出一般 RGBA 圖片。

### 3.1 合併 BMP+ALP

單組：

```bat
py -3 bmp_alp.py -e "D:/Game/image.bmp" "D:/Game/image.alp" "D:/Work/image.png"
```

批次：

```bat
py -3 bmp_alp.py -e "D:/Game/bmp_alp" "D:/Work/png"
```

單組合併可依輸出副檔名選擇 PNG、TGA、BMP、JPEG、WebP 或 TIFF；批次合併固定輸出 PNG。JPEG 不支援 Alpha，輸出 JPEG 時會轉為 RGB 並捨棄透明度。

批次模式會依相對路徑及不區分大小寫的檔名尋找同名 BMP／ALP：

- 缺少對應 ALP 的 BMP 會提示並略過。
- 不會拿其他檔案的 ALP 代替。
- 若實際轉換發生錯誤，整批不會提交部分輸出。
- 輸出資料夾必須位於來源資料夾之外。

ALP 可讀取兩種資料排列：每列緊密排列（stride = 圖片寬度），或每列依 4 bytes 對齊。程式會依 ALP 檔案大小判斷；若兩者都不符合，會回報尺寸錯誤。

### 3.2 將一般圖片拆成 BMP+ALP

單張：

```bat
py -3 bmp_alp.py -c "D:/Game/image.png" "D:/Work/image.bmp" "D:/Work/image.alp"
```

批次：

```bat
py -3 bmp_alp.py -c "D:/Game/images" "D:/Work/bmp_alp"
```

來源圖片支援 PNG、TGA、BMP、JPG／JPEG、WebP、TIF／TIFF。拆分時輸出：

- BMP：RGB 圖像資料。
- ALP：緊密排列的每像素 1 byte Alpha 資料，大小為「寬 × 高」bytes。

批次拆分會保留來源子資料夾結構。若多張來源圖片會對應到相同的 BMP／ALP 輸出名稱（例如同名但不同副檔名），會在寫入前停止，避免覆蓋。輸出資料夾也必須位於來源資料夾之外。

---

## 測試素材與驗證紀錄

儲存庫中的 `TEST/` 目前包含：

- `ep_000_001_000116120116.pak`
- `ep_000_001_000.txt`
- `ep_000_004_000.txt`
- `sys_v1.pak`

其他大型測試封包及完整遊戲素材未全部放入儲存庫。

以下是專案既有的**手動測試紀錄**，不是隨儲存庫附帶的自動化測試套件，也不是本次 README 編輯時重新執行的測試。位元組一致代表工具層級的往返比對符合預期，不代表已確認所有輸出都能在遊戲內正常載入。

| 測試素材 | 測試內容 | 既有紀錄 |
|---|---|---|
| `scr.pak` | 解包 245 個腳本；未修改匯出／匯入後逐位元組比較 | 245／245 相同 |
| `scr.pak` | 34,043 筆對話及 2 筆選項加入測試文字，匯入後再次匯出 JSON | 245／245 份 JSON 完全一致 |
| `ep_300_000_000.txt` | 501 筆底層記錄，匯出 118 筆文字；修改後匯入並再次匯出 | 118 筆完全一致 |
| `scr.pak` | PAK 解包、重包、再次解包後比較全部檔案 | 245／245 相同 |
| `sys_v1.pak` | PAK 解包、重包、再次解包後比較全部檔案 | 14／14 相同 |
| `NEKOPACK4S` 範例 | 原始封包解包、4S 重包、再次解包，比對腳本 SHA-256 | 461,008 bytes 完全一致 |
| `NEKOPACK4A` 測試封包 | 兩個檔案重包、再次解包後逐位元組比較 | 完全一致 |
| BMP／ALP 範例 | 合併有對應 ALP 的 BMP；處理缺少 ALP 的 BMP | 5 組合併成功；2 個缺少 ALP 的 BMP 提示並跳過 |

---

## 限制與注意事項

1. **格式範圍有限：** PAK 僅支援已辨識的 NEKOPACK4A／4S；腳本僅支援具有正確簽章及可解析結構的 NEKOSDK_ADVSCRIPT2；圖像功能僅處理 BMP／ALP 配對及列出的常見圖片格式。
2. **不要覆寫唯一原始檔：** PAK、腳本及批次圖像操作都應使用獨立輸出位置。程式會做部分路徑衝突檢查，但仍應自行保留備份。
3. **文字能編碼不等於遊戲能顯示：** 腳本匯入成功只代表文字可依指定編碼寫入資料；遊戲引擎、字型、字形表及文字繪製流程仍可能不支援該文字。
4. **工具成功不等於遊戲相容：** 封包能再次解包、腳本能再次匯出，屬於工具層級的往返驗證；最終仍須在目標遊戲中測試。
5. **批次輸出要保留完整資料：** 匯出時只為有效 ADVSCRIPT2 建立 JSON；匯入時則會把非腳本檔案複製到輸出資料夾。請使用原始解包資料夾作為匯入來源，不要只拿 JSON 資料夾重包。
