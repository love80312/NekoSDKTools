# NekoSDKTools（個人優化版）

用於 `NEKOPACK4A` 封包與 `NEKOSDK_ADVSCRIPT2` 腳本的 Python 工具組。

## 功能

| 檔案 | 功能 |
|---|---|
| `pak.py` | 解包及重包 `NEKOPACK4A` 格式的 `.pak` |
| `scr.py` | 解析 `NEKOSDK_ADVSCRIPT2` 腳本，將對話／可辨識選項匯出成 UTF-8 JSON，再匯入修改內容 |
| `bmp_alp.py` | 合併 BMP+ALP 為一般圖片，或將 RGBA 圖片拆成 BMP+ALP |

需要 Python 3.10 以上版本。只有 `bmp_alp.py` 需要 Pillow。

## 安裝

```bat
py -3.13 -m pip install -r requirements.txt
```

## 完整流程：解包、匯出翻譯檔、匯入、重包

以下以遊戲的 `scr.pak` 為例：

```bat
py -3.13 pak.py -u "D:\Game\scr.pak" "D:\Game\scr_unpacked"
py -3.13 scr.py -e "D:\Game\scr_unpacked" "D:\Game\scr_json"
```

編輯 `scr_json` 內的 JSON 檔案後：

```bat
py -3.13 scr.py -i "D:\Game\scr_unpacked" "D:\Game\scr_json" "D:\Game\scr_imported"
py -3.13 pak.py -p "D:\Game\scr_imported" "D:\Game\scr_new.pak"
```

匯入時會保留原始腳本中未處理的欄位；資料夾模式下，非 ADVSCRIPT2 檔案也會原樣複製到輸出資料夾，避免重包時遺漏其他資源。

## 文字編碼注意事項

- `scr.py` 預設使用 `cp932`，與目前測試的遊戲腳本相符。
- JSON 一律使用 UTF-8；這只代表翻譯檔的文字編碼，不代表遊戲內部腳本也是 UTF-8。
- `--encoding` 指定的是腳本原始字串所使用的編碼，讀取與寫入會使用同一編碼。它不是自動轉換遊戲引擎編碼的功能。
- CP932 無法表示的繁體中文字會明確報錯，不會用問號或替代字元悄悄取代。要在遊戲中顯示完整繁體中文，必須先確認引擎支援的字元編碼與字型；單純把參數改成 UTF-8，不能保證遊戲能正確讀取。

## BMP／ALP 圖片

合併單組：

```bat
py -3.13 bmp_alp.py -e "D:\Game\obj\image.bmp" "D:\Game\obj\image.alp" "D:\Game\png\image.png"
```

批次合併：

```bat
py -3.13 bmp_alp.py -e "D:\Game\unpacked" "D:\Game\png"
```

批次模式只處理有對應 ALP 的 BMP；沒有配對的 BMP 會列出並跳過，不會讓整批作業中斷。

拆分一般圖片：

```bat
py -3.13 bmp_alp.py -c "D:\Game\png" "D:\Game\bmp_alp"
```

建立 ALP 時使用緊密排列的每像素 1 byte alpha 資料；解包時則同時接受緊密排列與每列 4-byte 對齊兩種布局。

## PAK 重包設定

預設使用 zlib level 9、單執行緒，以降低舊電腦的 CPU／記憶體尖峰。可用環境變數調整：

```bat
set NEKO_PAK_LEVEL=6
set NEKO_PAK_WORKERS=1
py -3.13 pak.py -p "D:\Game\scr_imported" "D:\Game\scr_new.pak"
```

## 已完成的測試

- `scr.pak`：245 個項目全部解包成功，且 245 個檔案均具有 `NEKOSDK_ADVSCRIPT2` 檔頭。
- 腳本：245 個檔案可匯出共 34,043 個對話項目；未修改內容重新匯入後，245 個腳本逐位元組相同。
- 封包：將 `scr.pak` 與另一個 `NEKOPACK4A` 範例解包、重包，再次解包後，全部檔案內容相同。
- 小型 payload：空檔、1 byte、30 bytes 及一般檔案的封包／解包往返測試通過。
- `bmp_alp.py`：目前 `sys_v1.pak` 中的 5 組 BMP+ALP 配對成功合併；2 個沒有 ALP 的 BMP 會提示並跳過。

上述測試驗證的是封包及檔案資料往返一致，不等於已在遊戲中驗證重包檔一定能正常載入；實際使用前仍應保留原始 PAK 備份並在遊戲內測試。
