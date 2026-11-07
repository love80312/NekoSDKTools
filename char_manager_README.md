# NekoSDKTools 字元管理工具

`char_manager.py` 是獨立工具模組，也包含可嵌入 `GUI.py` 的 `CharacterManagerFrame`。僅使用 Python 標準函式庫，需求 Python 3.10+。

## GUI 整合

請參考 `GUI_整合修改.txt`，在 `GUI.py` 匯入 `CharacterManagerFrame` 並新增「字元管理」頁籤。無須另做一套完整 GUI。

## GUI 操作流程

1. 選擇翻譯 JSON／TXT 資料夾。
2. 選擇原始遊戲腳本資料夾。ADVSCRIPT2 檔案會交給同目錄的 `scr.py` 解析。
3. 按「開始掃描與統計」。
4. 左側選擇待替換中文字，右側選擇來源候選字元，按「建立配對」。
5. 不想使用的來源候選可選取後按「排除選取來源字元」。排除清單會存入 `tool/char_manager.json`。
6. 按「套用至 uif_config.json」後，程式會先建立 `tool/uif_config.json.bak`，再更新現有 `character_substitution` 的兩個字串。

## 獨立命令列掃描

```bat
python char_manager.py scan --translations "D:\Game\translation_json" --source "D:\Game\original_scripts"
```

可選參數：

```text
--output              報表輸出資料夾
--config              UIF uif_config.json 路徑
--settings            char_manager.json 路徑
--text-encoding       TXT／CSV 等翻譯文檔編碼，預設 utf-8-sig
--command-encoding    ADVSCRIPT2 指令／場景編碼，預設 cp932
--dialogue-encoding   ADVSCRIPT2 對話編碼，預設 auto
```

## 獨立套用 CSV

掃描後，開啟報表資料夾的 `mapping_template.csv`，在「來源候選字元（自行填入）」欄位填入對應的 CP932 替代來源字元，儲存後執行：

```bat
python char_manager.py apply --mapping "D:\Game\translation_json_char_report\mapping_template.csv" --source "D:\Game\original_scripts"
```

套用前會重新掃描原始腳本，拒絕使用原文已出現的來源字元；也會依 `scan_metadata.json` 重新掃描當初的翻譯資料夾，拒絕使用翻譯中已出現的字元。請保留完整報表資料夾，不要只搬走 `mapping_template.csv`。

## 完整 CP932 字形目錄

本次另附 `CP932_漢字符號字形清單.csv`（含 Unicode、名稱、類別及 CP932 位元組）及 `CP932_漢字符號字形清單.txt`（純字元清單）。本次 Python CP932 codec 掃描得到 6,968 個漢字／標點／符號字元。這是 CP932 編碼表清單，不代表每款遊戲的字型都具備全部字形；實際替換仍需避開該遊戲原文與翻譯已使用字元。

## 報表

| 檔案 | 內容 |
|---|---|
| `character_report.csv` | 翻譯實際使用的漢字及特殊符號、次數、檔案數、CP932 能否編碼、目前映射狀態 |
| `cp932_glyphs.csv` | Python CP932 codec 能解碼的漢字及標點／符號清單，含 Unicode 與 CP932 位元組、原文占用與候選狀態 |
| `safe_candidate_glyphs.csv` | 已排除原文占用、既有映射及人工排除的非 ASCII 候選來源字元 |
| `mapping_template.csv` | 尚未映射的 CP932 不支援字元，供手動填入來源候選字元 |
| `unmapped_characters.txt` | 尚未映射的目標字元清單 |
| `scan_metadata.json` | 掃描來源、編碼、檔案數及掃描完整性；供套用時重新驗證 |

## 字元篩選原則

- 翻譯文字只統計漢字及 Unicode 標點／符號類別。
- 英文字母、日文假名、數字、空白不列入統計。
- `cp932_glyphs.csv` 保留 CP932 中的標點／符號，包括 ASCII 標點；但預設安全候選排除 ASCII 字元，避免 `%`、`&`、括號或括號類符號干擾腳本語法或格式控制。
- 安全候選還會排除原始腳本已使用字元、翻譯文檔本身已使用字元、既有來源／目標映射及人工排除字元。來源字元若已出現在翻譯中，UIF 會連正常翻譯一起替換，因此也必須排除。
- 原始腳本或翻譯文檔任一方掃描不完整時，不會提供安全候選，也不允許套用映射。

## 注意

1. CP932 可編碼不代表遊戲字型一定有該字形；仍需在遊戲中實際驗證。
2. 目前對應 T1 的 `character_substitution` 設定；不會自動改寫成 UIF 新版 `text_processor`。
3. 掃描支援 JSON、TXT、CSV、TSV、PO、FTL、INI、XML、HTML、Markdown、YAML 等翻譯文檔；JSON 固定使用 UTF-8／UTF-8 BOM，其他文字文件使用指定編碼。
4. 原始 ADVSCRIPT2 `.txt`／`.bin`／`.scr` 由 `scr.py` 解析，不能只選擇尚未解包的 PAK 封包。
