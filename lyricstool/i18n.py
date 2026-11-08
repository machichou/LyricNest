"""Interface text. The Chinese text is written where it is used and is its own key; EN gives the English for each.
The language is picked once at start: the window is built in one language, so a change shows after a restart."""
import ctypes

LANGUAGES = {"zh-TW": "繁體中文", "en": "English"}
LANG = "zh-TW"

# Kept in the database as written, so a lookup can still read them after the language changes; only what is shown gets translated.
STORED = {
    ".lrc 外掛檔": ".lrc file",
    "LRCLIB（本機）": "LRCLIB (local)",
    "LRCLIB（線上）": "LRCLIB (online)",
    "網易雲": "NetEase",
    "QQ 音樂": "QQ Music",
    "酷狗": "Kugou",
    "（已轉繁體）": " (converted to Traditional)",
    "現有歌詞": "Existing lyrics",
    "整張專輯略過": "Album skipped",
    "；純音樂：": "; instrumental: ",
    "、": ", ",
}


def system_language():
    """Chinese of any kind gets Traditional Chinese, the only Chinese there is; anything else gets English."""
    return "zh-TW" if ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF == 0x04 else "en"  # 0x04: LANG_CHINESE


def use(lang):
    """`lang` comes from the settings file, which a person may have edited."""
    global LANG
    LANG = lang if lang in LANGUAGES else system_language()


def tr(text, **values):
    out = text if LANG == "zh-TW" else EN[text]
    return out.format(**values) if values else out


def stored_text(text):
    """Source names and other stored notes, as shown."""
    if LANG == "zh-TW":
        return text
    for zh, en in STORED.items():
        text = text.replace(zh, en)
    return text


EN = {
    # window, tabs, status bar
    "歌詞工具": "LyricsTool",
    "總覽": "Overview",
    "查詢歌詞": "Look Up Lyrics",
    "審閱與寫入": "Review & Write",
    "審閱與寫入（{n}）": "Review & Write ({n})",
    "備份還原": "Backups",
    "設定": "Settings",
    "待命中": "Ready",
    "停止": "Stop",
    "正在停止（完成目前這首後結束）…": "Stopping (after the current song)…",
    "剩餘 {n:.1f} 小時": "{n:.1f} h left",
    "剩餘 {n:.0f} 分鐘": "{n:.0f} min left",
    "✓": "✓",
    "，": ", ",
    "、": ", ",
    "；": "; ",
    "　": "   ",
    "（{extra}）": " ({extra})",
    # overview
    "重新掃描": "Rescan",
    "匯出缺少同步歌詞的歌曲": "Export songs without synced lyrics",
    "音樂資料夾：{folder}": "Music folder: {folder}",
    "尚未設定，請至「設定」分頁選擇": "not set; choose one on the Settings tab",
    "同步歌詞（動態顯示）": "Synced lyrics",
    "僅有純文字歌詞": "Plain lyrics only",
    "沒有歌詞": "No lyrics",
    "不支援或已損壞": "Unsupported or damaged",
    "缺少同步歌詞的原因": "Why songs lack synced lyrics",
    "查無歌詞最多的歌手": "Artists with the most songs not found",
    "原因": "Reason",
    "首數": "Songs",
    "返回": "Back",
    "搜尋：": "Search: ",
    "{name}　{n:,} 首": "{name}   {n:,}",
    "歌手": "Artist",
    "歌名": "Title",
    "狀況": "Status",
    "專輯": "Album",
    "格式": "Format",
    "音質": "Quality",
    "從左側選取歌曲，即可查看詳細資料與歌詞。": "Select a song on the left to see its details and lyrics.",
    "\n狀況：{why}": "\nStatus: {why}",
    "錯誤：{error}\n": "Error: {error}\n",
    "\n內嵌歌詞：\n": "\nEmbedded lyrics:\n",
    "（無）": "(none)",
    "尚未查詢": "Not looked up yet",
    "已找到，待審閱": "Found, waiting for review",
    "查無歌詞": "No lyrics found",
    "來源標示為純音樂或無歌詞": "A source says instrumental or no lyrics",
    "已從清單移除": "Removed from the list",
    "來源忙碌，待重新查詢": "Source busy, to be asked again",
    "不支援寫入此格式": "Format not supported for writing",
    "檔案損壞或無法讀取": "Damaged or unreadable file",
    # look up
    "查詢範圍": "What to look up",
    "尚未查詢（含先前來源忙碌的歌曲）": "Not looked up yet (and songs a busy source left)",
    "先前查無歌詞（含已從清單移除的歌曲）": "Not found before (and songs removed from the list)",
    "標示為純音樂的歌曲": "Songs marked instrumental",
    "僅查詢歌手、專輯、歌名或路徑包含：": "Only where artist, album, title or path contains:",
    "符合 {n} 首": "Matches: {n}",
    "開始查詢": "Start lookup",
    "查詢結果會先列入「審閱與寫入」，不會直接寫入檔案。中途停止或關閉程式不會遺失進度，下次會接續查詢。":
        "Results go to Review & Write first; no file is written yet. Stopping or closing loses nothing: the next lookup carries on from there.",
    # review
    "全部": "All",
    "新歌詞": "New lyrics",
    "清理製作名單": "Credit cleanup",
    "只顯示需留意的歌曲": "Only songs to check",
    "共 {total} 首，已勾選 {n} 首": "{total} in list, {n} ticked",
    "行為類型": "Type",
    "來源": "Source",
    "行數": "Lines",
    "行為": "Action",
    "從左側選取歌曲，即可查看來源、需留意事項、將刪除的內容與完整歌詞。\n\n點選 ☑ 或按空白鍵可切換勾選。":
        "Select a song on the left to see its source, anything to check, what will be removed and the full lyrics.\n\nClick ☑ or press Space to tick or untick.",
    "來源：{source}\n比對結果：{matched}（{found:.1f} 秒，檔案為 {length:.1f} 秒）\n": "Source: {source}\nMatched: {matched} ({found:.1f} s; the file is {length:.1f} s)\n",
    "需留意：{flag}\n": "Check: {flag}\n",
    "\n將刪除的內容：\n": "\nTo be removed:\n",
    "\n寫入後的歌詞：\n": "\nLyrics after writing:\n",
    "全部勾選": "Tick all",
    "全部取消": "Untick all",
    "移除選取的歌曲": "Remove selected",
    "匯出清單": "Export list",
    "備份並寫入": "Back up and write",
    "寫入": "Write",
    "備份並寫入 {n} 首": "Back up and write ({n})",
    "寫入 {n} 首": "Write ({n})",
    "寫入前先備份": "Back up first",
    "寫入新歌詞": "Write new lyrics",
    "取代純文字歌詞": "Replace plain lyrics",
    "刪除 {n} 行製作名單": "Remove credits: {n}",
    "長度差 {n:.1f} 秒": "Length differs by {n:.1f} s",
    "僅 {n} 行": "Only {n} lines",
    "歌詞在歌曲中段前就結束": "Lyrics end before the middle of the song",
    "部分文字被遮蔽（*）": "Some words are masked (*)",
    "歌名不完全相同": "Title is not an exact match",
    "會取代純文字歌詞": "Replaces plain lyrics",
    # backups
    "重新整理": "Refresh",
    "批次": "Batch",
    "時間": "Time",
    "此批次的檔案（可多選，只還原選取的項目）": "Files in this batch (select some to restore only those)",
    "還原會將歌詞等標籤恢復為寫入前的狀態；音訊與備份時不同的檔案會略過。":
        "Restoring puts lyrics and other tags back as they were before writing; files whose audio differs from the backup are skipped.",
    "還原整批": "Restore batch",
    "還原選取項目": "Restore selected",
    "備份資料夾：{folder}": "Backup folder: {folder}",
    "　備份已關閉，之後寫入的歌曲不會列在此處": "   Backups are off; songs written from now on will not be listed here",
    # settings
    "歌詞工具 {version}　作者：{author}　由 Claude 協助開發": "LyricsTool {version}   by {author}   built with help from Claude",
    "儲存設定": "Save settings",
    "開啟記錄檔資料夾": "Open log folder",
    "音樂資料夾": "Music folder",
    "備份資料夾": "Backup folder",
    "LRCLIB 本機資料庫（選填，未設定時查詢線上資料）": "LRCLIB local database (optional; looked up online when not set)",
    "LRCGET 資料庫（選填）": "LRCGET database (optional)",
    "瀏覽": "Browse",
    "寫入前先備份原本的標籤": "Back up the original tags before writing",
    "略過的資料夾名稱": "Folder names to skip",
    "例如：有聲書, Podcast, downloading": "e.g. Audiobooks, Podcast, downloading",
    "介面語言：": "Language:",
    "歌詞來源": "Lyrics sources",
    "歌詞處理": "Lyrics processing",
    "轉換為台灣繁體（含假名的歌詞除外）": "Convert Chinese to Traditional (Taiwan), except lyrics with kana",
    "移除製作名單等非歌詞內容": "Remove credits and other lines that are not lyrics",
    "包括作曲、版權、開頭的歌名與歌手、前奏標示": "Composer, copyright, the title and artist at the top, intro cues",
    "專輯前 {n} 首都找不到時，略過整張專輯": "Skip an album when its first {n} songs are all not found",
    "純音樂也算找不到；前 {n} 首有任何一首找到，整張都會查；略過的歌記成查無歌詞":
        "Instrumental counts as a miss; a hit in the first {n} keeps the album; skipped songs count as not found",
    "歌手別名": "Artist aliases",
    "複製給 AI 的提示詞": "Copy prompt for an AI",
    "歌手標籤包含等號左側的文字時，也會用右側的名稱查詢。每行一位歌手。": "One artist per line. Tags containing the left side are also searched as the right.",
    "例如：lisa ono = 小野リサ, Lisa Ono, 小野丽莎, 小野麗莎, Ono Lisa": "e.g. lisa ono = 小野リサ, Lisa Ono, 小野丽莎, 小野麗莎, Ono Lisa",
    "　　　Ichiko Aoba = 青葉市子, 青叶市子, あおばいちこ": "       Ichiko Aoba = 青葉市子, 青叶市子, あおばいちこ",
    # dialogs
    "確定": "OK",
    "否": "No",
    "是": "Yes",
    "取消": "Cancel",
    "歌詞工具已在執行中。": "LyricsTool is already running.",
    "目前有工作正在進行，請等待完成或先按「停止」。": "A job is running. Wait for it to finish, or click Stop first.",
    "目前有工作正在進行，請等待完成或先按「停止」，再儲存設定。": "A job is running. Wait for it to finish, or click Stop, before saving settings.",
    "目前有工作正在進行，要停止並關閉程式嗎？\n已完成的部分會保留。": "A job is running. Stop it and close?\nWhat is done so far is kept.",
    "{name}時發生錯誤：{e}\n詳細資訊請見記錄檔。": "{name} failed: {e}\nSee the log file for details.",
    "發生錯誤：{e}\n詳細資訊請見記錄檔。": "Something went wrong: {e}\nSee the log file for details.",
    "來源被限流": "Source is limiting lookups",
    "{label} 一直拒絕查詢，可能是短時間內查太多次，已暫停使用 10 分鐘。\n\n"
    "其他來源會繼續查，這段期間的歌會留到最後再補問。建議先停止，過一段時間（例如一小時）再查。\n\n要現在停止查詢嗎？":
        "{label} keeps refusing lookups, probably after too many in a short time, and is paused for 10 minutes.\n\n"
        "The other sources carry on, and songs from this time are asked again at the end. It is best to stop now and look up again later (in an hour, say).\n\n"
        "Stop the lookup now?",
    "掃描音樂資料夾": "Scan music folder",
    "請至少勾選一種查詢範圍。": "Tick at least one kind of song to look up.",
    "尚未勾選要寫入的歌曲。": "No songs are ticked for writing.",
    "確定要寫入 {n} 首歌曲嗎？\n\n": "Write lyrics to the ticked songs ({n})?\n\n",
    "原本的標籤會先備份至：\n{folder}": "The original tags will be backed up to:\n{folder}",
    "備份已關閉，寫入後無法還原。": "Backups are off; this cannot be undone.",
    "\n\n其中 {n} 首需要留意。": "\n\nSongs to check among them: {n}.",
    "寫入歌詞": "Write lyrics",
    "寫入完成": "Write complete",
    "已成功寫入 {n} 首，歌詞與音訊皆通過驗證。": "Written: {n}. Lyrics and audio all passed verification.",
    "\n{failed} 首寫入失敗，{stale} 首因檔案已變動而略過，這些檔案都沒有被更動。詳細資訊請見「查詢歌詞」分頁的紀錄。":
        "\nFailed: {failed}. Skipped because the file changed: {stale}. None of these files were touched. Details are in the log on the Look Up Lyrics tab.",
    "請先選取一個備份批次。": "Select a backup batch first.",
    "請先在右側選取要還原的檔案。": "Select the files to restore on the right first.",
    "還原": "Restore",
    "確定要將 {n} 首歌曲還原為此批次寫入前的狀態嗎？": "Restore these songs ({n}) to how they were before this batch was written?",
    "還原完成": "Restore complete",
    "已還原 {done} 首，略過 {skipped} 首。": "Restored: {done}. Skipped: {skipped}.",
    "曲庫裡的歌手都已經有別名了。": "Every artist in the library already has aliases.",
    "已複製提示詞，列出 {n} 位還沒有別名的歌手。\n\n貼給你常用的 AI（例如 ChatGPT、Claude），把它回覆的每一行貼到下方的歌手別名，再按「儲存設定」。":
        "Prompt copied. It lists the artists without aliases ({n}).\n\nPaste it into the AI you use (ChatGPT or Claude, say), paste each line of its reply into Artist aliases below, then click Save settings.",
    "找不到音樂資料夾。": "Music folder not found.",
    "請設定備份資料夾。": "Set a backup folder.",
    "設定已儲存。": "Settings saved.",
    "關閉備份": "Turn off backups",
    "關閉備份後，寫入歌詞前將不會保存原本的標籤：\n\n• 寫入錯誤的歌詞時，無法透過「備份還原」復原，只能手動修正。\n• 被取代的純文字歌詞與刪除的製作名單將無法復原。\n\n"
    "音訊不受影響，寫入前後的檢查也會照常進行。\n現有的備份不會受到影響。":
        "With backups off, the original tags are not saved before lyrics are written:\n\n• Wrong lyrics, once written, cannot be undone from Backups; they have to be fixed by hand.\n"
        "• Replaced plain lyrics and removed credits cannot be recovered.\n\nAudio is not affected, and the checks before and after writing still run.\nExisting backups are not affected.",
    "重新開啟程式後就會套用新的語言。": "The new language is used from the next time LyricsTool starts.",
    "缺少同步歌詞的歌曲.csv": "songs without synced lyrics.csv",
    "審閱清單.csv": "review list.csv",
    # jobs: scan
    "找不到音樂資料夾：{folder}": "Music folder not found: {folder}",
    "正在列出檔案…": "Listing files…",
    "{folder} 中找不到任何音樂檔，可能是磁碟未連接。原有資料已保留。": "No music files found in {folder}; the drive may not be connected. Existing data was kept.",
    "已讀取 {n}/{total} 首": "Read {n}/{total}",
    "掃描完成：{total} 首，更新 {changed} 首，移除 {gone} 首，建議清理製作名單 {clean} 首": "Scan done. Songs: {total}, updated: {changed}, removed: {gone}, credit cleanups suggested: {clean}",
    # jobs: lookup
    "{n} 首缺少歌手或歌名標籤，已略過：": "Skipped for lack of an artist or title tag ({n}):",
    "沒有需要查詢的歌曲": "No songs to look up",
    "沒有可用的歌詞來源，請至「設定」開啟。": "No lyrics sources are on. Turn some on in Settings.",
    "同時查詢的來源：{sources}": "Sources asked together: {sources}",
    "已停止。查詢結果已儲存，下次會接續查詢：{summary}": "Stopped. Results are saved, and the next lookup carries on from here: {summary}",
    "{sources} 仍在休息，剩下 {n} 首留到下一輪": "{sources} still resting; the songs left ({n}) wait for the next round",
    "{sources} 仍在休息，剩下 {n} 首留到下次查詢": "{sources} still resting; the songs left ({n}) wait for the next lookup",
    "等待來源恢復，{} 秒後重查": "Waiting for sources to recover; asking again in {} s",
    "（{reasons}）": " ({reasons})",
    "{status}{why}：{artist} - {title}": "{status}{why}: {artist} - {title}",
    "略過整張專輯（前 {n} 首都找不到）：{album}": "Skipping the album (first {n} songs not found): {album}",
    "{n} 首因來源忙碌需要重查，約 {minutes:.0f} 分鐘後自動重試（第 {round} 輪）": "Songs to ask again because a source was busy: {n}. Trying again in about {minutes:.0f} min (round {round})",
    "查詢完成：{summary}": "Lookup done: {summary}",
    "{label} {n} 首": "{label}: {n}",
    "沒有結果": "no results",
    "找到": "Found",
    "純音樂": "Instrumental",
    "待重查": "To ask again",
    "整張專輯略過": "Album skipped",
    "與先前移除的歌詞相同": "Same as lyrics removed before",
    "逾時": "timed out",
    "暫停中": "paused",
    "忙碌": "busy",
    "出錯": "error",
    # jobs: write and restore
    "尚未勾選要寫入的歌曲": "No songs are ticked for writing",
    "備份資料夾：{folder}": "Backup folder: {folder}",
    "備份已關閉，本次寫入後無法還原": "Backups are off; this write cannot be undone",
    "已停止。已寫入的歌曲皆通過驗證，其餘仍保留在清單上": "Stopped. Every song written passed verification; the rest stay on the list",
    "寫入完成：成功 {ok} 首、失敗 {failed} 首、因檔案已變動略過 {stale} 首": "Write done: {ok} written, {failed} failed, {stale} skipped because the file changed",
    "  {why}：{path}": "  {why}: {path}",
    "檔案不存在": "File not found",
    "位於略過的資料夾內": "In a skipped folder",
    "無法讀取檔案（{error}）": "Cannot read the file ({error})",
    "已經有同步歌詞": "Already has synced lyrics",
    "檔案已被替換": "File was replaced",
    "歌詞已被修改": "Lyrics were changed",
    "還原完成：{done} 首，略過 {skipped} 首": "Restore done: {done} restored, {skipped} skipped",
    # jobs: exports
    "勾選": "Ticked",
    "比對結果": "Matched",
    "標記": "Flags",
    "刪除的內容": "Removed lines",
    "路徑": "Path",
    "目前歌詞": "Current lyrics",
    "查詢結果": "Lookup result",
    "純文字": "Plain text",
    "沒有": "None",
    "不支援的格式": "Unsupported format",
    "無法讀取": "Unreadable",
    "純音樂或無歌詞": "Instrumental or no lyrics",
    "來源忙碌待重查": "Source busy, to ask again",
    "待審閱": "Waiting for review",
    # audio
    "不是標準的 FLAC 檔案": "Not a standard FLAC file",
    "FLAC 標頭損壞": "FLAC header is damaged",
    "不支援 {ext} 格式": "{ext} is not supported",
    "檔案被其他程式占用（可能正在播放或上傳）": "The file is in use by another program (playing or uploading, perhaps)",
    "歌詞內容為空": "The lyrics are empty",
    "檢查未通過，未寫入檔案": "Check failed; the file was not written",
    "寫入後驗證失敗，已還原": "Check after writing failed; the original was put back",
    "備份檔不存在": "Backup file not found",
    "音訊與備份時不同": "Audio differs from the backup",
    # sources
    "{source} 暫時無法查詢（來源忙碌），休息 {wait} 秒，其他來源繼續查詢": "{source} is busy; resting {wait} s while the other sources carry on",
    "{source} 暫時無法查詢（來源忙碌），{{}} 秒後重試（第 {n} 次）": "{source} is busy; trying again in {{}} s (attempt {n})",
    "{source} 重試中（第 {n} 次）…": "{source} trying again (attempt {n})…",
    "{source} 連續無法查詢，暫停使用 10 分鐘，其他來源繼續查詢；這段期間的歌曲之後會自動重查":
        "{source} keeps refusing, so it is paused for 10 minutes while the other sources carry on; songs from this time are asked again automatically",
    "無法開啟 LRCGET 資料庫，已略過：{e}": "Cannot open the LRCGET database, skipped: {e}",
    "無法開啟 LRCLIB 本機資料庫，改為查詢線上資料：{e}": "Cannot open the local LRCLIB database, looking up online instead: {e}",
    # the prompt for an AI, jobs.ALIAS_PROMPT
    """請幫我整理歌手的別名，給歌詞查詢工具使用。工具會拿這些名稱到 LRCLIB、網易雲音樂、QQ 音樂、酷狗等平台搜尋歌詞，但每位歌手只會用前兩個名稱搜尋。

輸出格式：每行一位歌手，寫成「歌手 = 名稱1, 名稱2, 名稱3」。
- 等號左邊：照抄我給的名稱，一個字都不要改。
- 名稱1：這位歌手在音樂平台上最常用、而且和左邊不同的名稱，例如原文名稱、官方英文名稱或官方中文名稱。
- 名稱2：照抄左邊的名稱。
- 名稱3 以後：其他確實有平台在用的寫法，沒有就不用寫。

只列出真的有另一個名稱的歌手。下面這些情況不要列：
- 只差在簡體和繁體、大小寫、重音符號或空格（工具會自己處理）。
- 西洋歌手的中文音譯，除非唱片公司或平台正式使用。
- 本名、綽號、團員、舊團名，或只是相似的名字。
- 你不確定的。寧可少列也不要猜：錯的名稱會讓工具抓到別人的歌詞。

可以上網查證，但輸出只要這些行，不要加標題、編號、說明、連結或資料來源。清單太長的話可以分幾次輸出，我會回「繼續」。

正確的例子：
Masayoshi Takanaka = 高中正義, Masayoshi Takanaka
TOUMING MAGAZINE = 透明雜誌, TOUMING MAGAZINE
椎名林檎 = Sheena Ringo, 椎名林檎, Shiina Ringo
HYUKOH = 혁오, HYUKOH

不要輸出的例子（括號裡是原因）：
Arctic Monkeys = 北極猴子, Arctic Monkeys（只是中文音譯）
Frank Sinatra = Francis Albert Sinatra, Frank Sinatra（本名，平台不用）

以下是我的歌手：

""":
        """Please put together other names for the artists below, for a lyrics lookup tool. The tool searches LRCLIB, NetEase Cloud Music, QQ Music, Kugou and similar platforms with these names, but searches with only the first two names of each artist.

Output format: one artist per line, written as "artist = name1, name2, name3".
- Left of the equals sign: the name exactly as I give it, not changed in any way.
- name1: the name music platforms most often list this artist under that differs from the left, such as the name in its original script, the official English name or the official Chinese name.
- name2: the name on the left, copied exactly.
- name3 onwards: other spellings platforms really use; leave them out if there are none.

Only list artists that really have another name. Do not list:
- Names that differ only in Simplified or Traditional Chinese, letter case, accents or spaces (the tool handles those).
- Chinese transliterations of Western artists, unless the record label or platforms officially use them.
- Birth names, nicknames, band members, former band names, or names that are merely similar.
- Anything you are not sure of. Better to list too few than to guess: a wrong name makes the tool pick up someone else's lyrics.

You may look things up online, but output only these lines: no headings, numbering, explanations, links or sources. If the list is long, output it in parts and I will reply "continue".

Correct examples:
Masayoshi Takanaka = 高中正義, Masayoshi Takanaka
TOUMING MAGAZINE = 透明雜誌, TOUMING MAGAZINE
椎名林檎 = Sheena Ringo, 椎名林檎, Shiina Ringo
HYUKOH = 혁오, HYUKOH

Examples not to output (the reason in brackets):
Arctic Monkeys = 北極猴子, Arctic Monkeys (only a Chinese transliteration)
Frank Sinatra = Francis Albert Sinatra, Frank Sinatra (a birth name platforms do not use)

Here are my artists:

""",
}
