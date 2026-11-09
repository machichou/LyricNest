<div align="center">

# LyricsBox

**Synced lyrics for your whole music library, from 6 free sources at once.**

**English** &nbsp;·&nbsp; [繁體中文](README.zh-TW.md)

![.lrc file](https://img.shields.io/badge/-.lrc%20file-555?style=for-the-badge)
![LRCGET](https://img.shields.io/badge/-LRCGET-2f6f9f?style=for-the-badge)
![LRCLIB](https://img.shields.io/badge/-LRCLIB-1f8a70?style=for-the-badge)
![NetEase](https://img.shields.io/badge/-NetEase-c20c0c?style=for-the-badge)
![QQ Music](https://img.shields.io/badge/-QQ%20Music-31c27c?style=for-the-badge)
![Kugou](https://img.shields.io/badge/-Kugou-2ca2f9?style=for-the-badge)

![Windows](https://img.shields.io/badge/Windows-10%20%C2%B7%2011-0078D4?logo=windows&logoColor=white)
![Formats](https://img.shields.io/badge/writes-FLAC%20%C2%B7%20MP3-38d591)
![Interface](https://img.shields.io/badge/interface-English%20%C2%B7%20%E7%B9%81%E9%AB%94%E4%B8%AD%E6%96%87-555)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow)](LICENSE)

<img src="docs/screenshots/en/overview.png" alt="Overview of the library" width="900">

</div>

---

Many music players only show lyrics embedded in the file. LyricsBox finds time-synced lyrics for the songs that have none, cleans them up, and writes them in after you've checked them. One `.exe`, nothing to install.

## Features

<table>
<tr>
<td width="50%" valign="top">

### 🔎 6 sources at once

The first good match wins. Busy sources are retried on their own.

<img src="docs/screenshots/en/lookup.png" alt="Look Up Lyrics" width="100%">

</td>
<td width="50%" valign="top">

### ✅ You check before it writes

Every find waits in a list. Anything odd is flagged.

<img src="docs/screenshots/en/review.png" alt="Review & Write" width="100%">

</td>
</tr>
<tr>
<td width="50%" valign="top">

### 🧹 Clean lyrics

Credits and intro cues removed. Simplified Chinese turned Traditional.

<img src="docs/screenshots/en/missing.png" alt="Songs without lyrics, and why" width="100%">

</td>
<td width="50%" valign="top">

### ♻️ Undo any time

Tags are backed up before every write.

<img src="docs/screenshots/en/backups.png" alt="Backups" width="100%">

</td>
</tr>
<tr>
<td colspan="2" valign="top">

### ⚙️ Made for big libraries

Artist aliases, folders to skip, album skipping, English or 繁體中文.

<img src="docs/screenshots/en/settings.png" alt="Settings" width="70%">

</td>
</tr>
</table>

## How it works

```mermaid
flowchart LR
    lib[("Music folder<br>FLAC · MP3")] -->|scan| over["Overview<br>what has lyrics, what doesn't"]
    over --> ask{{"Look up, all at once<br>.lrc file · LRCGET · LRCLIB<br>NetEase · QQ Music · Kugou"}}
    ask -->|first good match| clean["Clean up<br>credits out · Simplified → Traditional"]
    clean --> review["Review list<br>flags for anything odd"]
    review -->|you tick and confirm| write["Back up, write, verify"]
    write --> restore["Backups<br>restore any time"]
```

## Safe writing

```mermaid
flowchart TD
    a["Re-read the file"] --> b{"Replaced, already synced,<br>or lyrics edited since?"}
    b -->|yes| skip["Skip it; it stays in the list"]
    b -->|no| c["Back up the original tags"]
    c --> d["Write a new copy beside it"]
    d --> e{"Audio identical and<br>lyrics read back correctly?"}
    e -->|no| fail["Discard the copy; the file is untouched"]
    e -->|yes| f["Swap it in, in one step"]
    f --> g["Check it once more"]
```

## Getting started

1. Download `LyricsBox.exe` from [Releases](../../releases) and run it.
2. **Settings**: pick your music folder and a backup folder, then **Save settings**.
3. **Look Up Lyrics**: click **Start lookup**.
4. **Review & Write**: untick anything wrong, then **Back up and write**.

Settings and lookup history live in `%LOCALAPPDATA%\LyricsBox`.

## Development

```bat
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
build.bat
```

Tests in `tests\` never write to your library: `test_core.py` (writing and restoring on real audio), `ui_shots.py` (every flow in the real window), `exe_smoke.py` (the built exe), `source_bench.py` (each online source's hit rate).

## License

[MIT](LICENSE) · by machi, built with help from Claude
