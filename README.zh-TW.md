<div align="center">

# 歌詞工具 LyricsTool

**整個音樂庫的同步歌詞，6 個免費來源同時查。**

[English](README.md) &nbsp;·&nbsp; **繁體中文**

![.lrc 外掛檔](https://img.shields.io/badge/-.lrc%20%E5%A4%96%E6%8E%9B%E6%AA%94-555?style=for-the-badge)
![LRCGET](https://img.shields.io/badge/-LRCGET-2f6f9f?style=for-the-badge)
![LRCLIB](https://img.shields.io/badge/-LRCLIB-1f8a70?style=for-the-badge)
![網易雲](https://img.shields.io/badge/-%E7%B6%B2%E6%98%93%E9%9B%B2-c20c0c?style=for-the-badge)
![QQ 音樂](https://img.shields.io/badge/-QQ%20%E9%9F%B3%E6%A8%82-31c27c?style=for-the-badge)
![酷狗](https://img.shields.io/badge/-%E9%85%B7%E7%8B%97-2ca2f9?style=for-the-badge)

![Windows](https://img.shields.io/badge/Windows-10%20%C2%B7%2011-0078D4?logo=windows&logoColor=white)
![Formats](https://img.shields.io/badge/%E5%AF%AB%E5%85%A5-FLAC%20%C2%B7%20MP3-38d591)
![Interface](https://img.shields.io/badge/%E4%BB%8B%E9%9D%A2-%E7%B9%81%E9%AB%94%E4%B8%AD%E6%96%87%20%C2%B7%20English-555)
[![License: MIT](https://img.shields.io/badge/%E6%8E%88%E6%AC%8A-MIT-yellow)](LICENSE)

<img src="docs/screenshots/zh-TW/overview.png" alt="音樂庫總覽" width="900">

</div>

---

很多音樂軟體只顯示內嵌在檔案裡的歌詞。歌詞工具會替沒有歌詞的歌找到同步歌詞，整理乾淨，你看過之後再寫進檔案。單一 `.exe`，不需安裝。

## 功能

<table>
<tr>
<td width="50%" valign="top">

### 🔎 6 個來源同時查

哪個先找到就用哪個，來源忙碌會自動重查。

<img src="docs/screenshots/zh-TW/lookup.png" alt="查詢歌詞" width="100%">

</td>
<td width="50%" valign="top">

### ✅ 你看過才寫入

找到的歌詞先進清單，可疑的會標出來。

<img src="docs/screenshots/zh-TW/review.png" alt="審閱與寫入" width="100%">

</td>
</tr>
<tr>
<td width="50%" valign="top">

### 🧹 乾淨的歌詞

刪掉製作名單和前奏標示，簡體轉台灣繁體。

<img src="docs/screenshots/zh-TW/missing.png" alt="沒有歌詞的歌與原因" width="100%">

</td>
<td width="50%" valign="top">

### ♻️ 隨時能還原

每次寫入前都會備份標籤。

<img src="docs/screenshots/zh-TW/backups.png" alt="備份還原" width="100%">

</td>
</tr>
<tr>
<td colspan="2" valign="top">

### ⚙️ 為大型音樂庫設計

歌手別名、略過資料夾、略過整張專輯、繁體中文或英文介面。

<img src="docs/screenshots/zh-TW/settings.png" alt="設定" width="70%">

</td>
</tr>
</table>

## 運作方式

```mermaid
flowchart LR
    lib[("音樂資料夾<br>FLAC · MP3")] -->|掃描| over["總覽<br>哪些有歌詞、哪些沒有"]
    over --> ask{{"所有來源同時查<br>.lrc 外掛檔 · LRCGET · LRCLIB<br>網易雲 · QQ 音樂 · 酷狗"}}
    ask -->|第一個符合的結果| clean["整理<br>刪除製作名單 · 簡體轉繁體"]
    clean --> review["審閱清單<br>可疑的地方會標出來"]
    review -->|你勾選並確認| write["備份、寫入、驗證"]
    write --> restore["備份還原<br>隨時可以還原"]
```

## 安全的寫入

```mermaid
flowchart TD
    a["重新讀取檔案"] --> b{"檔案被替換、已有同步歌詞，<br>或歌詞已被修改？"}
    b -->|是| skip["略過，留在清單上"]
    b -->|否| c["備份原本的標籤"]
    c --> d["在旁邊寫一份新檔"]
    d --> e{"音訊完全相同，<br>歌詞也讀得回來？"}
    e -->|否| fail["丟掉新檔，原檔沒動"]
    e -->|是| f["一次替換"]
    f --> g["再驗證一次"]
```

## 開始使用

1. 從 [Releases](../../releases) 下載 `LyricsTool.exe` 並執行。
2. **設定**：選擇音樂資料夾和備份資料夾，按「儲存設定」。
3. **查詢歌詞**：按「開始查詢」。
4. **審閱與寫入**：取消勾選不對的，再按「備份並寫入」。

設定和查詢紀錄存在 `%LOCALAPPDATA%\LyricsTool`。

## 開發

```bat
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
build.bat
```

`tests\` 裡的測試都不會寫入你的音樂庫：`test_core.py`（用真實音訊測寫入與還原）、`ui_shots.py`（實際操作視窗的每個流程）、`exe_smoke.py`（打包好的 exe）、`source_bench.py`（各線上來源的找到率）。

## 授權

[MIT](LICENSE) · 作者 machi，由 Claude 協助開發
