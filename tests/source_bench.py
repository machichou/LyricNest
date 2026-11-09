"""Ask every online source about every song in bench_songs.csv, each source on its own rather than first answer wins, to see how often
and how fast each one finds synced lyrics, and how that changes from day to day. Touches no music, settings or lookup history; only the
counts and matched names are saved, under build/. Run it while the app is not looking anything up: both draw on the same quota."""
import csv
import itertools
import os
import sys
import threading
import time

from rapidfuzz import fuzz

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lyricstool import jobs
from lyricstool.clean import squash, sung_lines
from lyricstool.sources import Busy, Kugou, NetEase, OnlineLrclib, Paused, QQMusic, make_query

HERE = os.path.dirname(os.path.abspath(__file__))
OUTCOMES = ("找到", "純音樂", "找不到", "被拒絕", "出錯")


def ask(source, songs, results, stats):
    """A resting source is waited for and asked the same song again, as the app asks it again later: every song gets a real answer."""
    began, pauses = time.monotonic(), 0
    for n, song in enumerate(songs):
        q = make_query(song["歌手"], song["歌名"], song["專輯"], float(song["長度秒"]), "", {})
        while True:
            start = time.monotonic()
            hit = None
            try:
                hit = source.find(q)
                outcome = "純音樂" if hit and hit.instrumental else "找到" if hit else "找不到"
            except Paused:
                pauses += 1
                time.sleep(max(0, source.lim.suspended_until - time.monotonic()) + 1)
                continue
            except Busy:
                outcome = "被拒絕"
            except Exception as e:  # one malformed answer should not end a run of hundreds
                outcome = "出錯"
                print(f"{source.label} 出錯：{song['歌手']} - {song['歌名']}：{e!r}", file=sys.stderr)
            break
        results[n][source.label] = (outcome, time.monotonic() - start, hit)
        if (n + 1) % 25 == 0:
            print(f"{source.label} {n + 1}/{len(songs)}", file=sys.stderr)
    stats[source.label] = (pauses, (time.monotonic() - began) / 60)


def bench(songs):
    """One thread per source, as in the app, so each keeps its own pace. Returns per song {source label: (outcome, seconds, hit)},
    and per source (times it was rested, minutes it took)."""
    ctx = jobs.Ctx(lambda kind, payload: print(payload[0], file=sys.stderr) if kind == "log" else None)
    sources = [NetEase(ctx), OnlineLrclib(ctx), QQMusic(ctx), Kugou(ctx)]
    results, stats = [{} for _ in songs], {}
    threads = [threading.Thread(target=ask, args=(s, songs, results, stats)) for s in sources]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return [s.label for s in sources], results, stats


def pct(part, whole):
    return f"{part * 100 / whole:.0f}%" if whole else "-"


def report(songs, labels, results, stats):
    found = [{lab for lab, (outcome, _, _) in r.items() if outcome == "找到"} for r in results]
    print("\n各來源（每首都各自查詢）")
    print("來源\t" + "\t".join(OUTCOMES) + "\t只有它找到\t平均秒數\t被暫停次數\t花了幾分鐘")
    for lab in labels:
        got = [r[lab][0] for r in results]
        only = sum(f == {lab} for f in found)
        pauses, minutes = stats[lab]
        print(lab + "\t" + "\t".join(pct(got.count(o), len(got)) for o in OUTCOMES)
              + f"\t{only}\t{sum(r[lab][1] for r in results) / len(results):.1f}\t{pauses}\t{minutes:.0f}")

    print("\n找到同步歌詞的比例，依語言與熱門程度")
    print("語言\t熱門程度\t首數\t任一來源\t" + "\t".join(labels))
    groups = {}
    for song, f in zip(songs, found):
        groups.setdefault((song["語言"], song["熱門程度"]), []).append(f)
    for (lang, tier), fs in groups.items():
        print(f"{lang}\t{tier}\t{len(fs)}\t{pct(sum(bool(f) for f in fs), len(fs))}\t" + "\t".join(pct(sum(lab in f for f in fs), len(fs)) for lab in labels))
    print(f"全部\t\t{len(found)}\t{pct(sum(bool(f) for f in found), len(found))}")

    odd = []
    for song, r in zip(songs, results):
        texts = {lab: squash("".join(sung_lines(hit.lrc))) for lab, (outcome, _, hit) in r.items() if outcome == "找到"}
        for a, b in itertools.combinations(texts, 2):
            ratio = fuzz.ratio(texts[a], texts[b])
            if ratio < 50:
                odd.append(f"  {song['歌手']} - {song['歌名']}：{a} 與 {b} 相似度 {ratio:.0f}%")
    print("\n不同來源的歌詞差很多（可能有一邊抓錯歌），值得人工看：" + ("" if odd else "沒有"))
    print("\n".join(odd))


def save(path, songs, labels, results):
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["語言", "熱門程度", "歌手", "歌名", "長度秒"] + [f"{lab}{col}" for lab in labels for col in ("結果", "秒數", "對到")])
        for song, r in zip(songs, results):
            cells = []
            for lab in labels:
                outcome, seconds, hit = r[lab]
                cells += [outcome, f"{seconds:.1f}", f"{hit.matched_artist} - {hit.matched_title}（{hit.matched_length:g} 秒）" if hit else ""]
            w.writerow([song["語言"], song["熱門程度"], song["歌手"], song["歌名"], song["長度秒"]] + cells)


if __name__ == "__main__":
    with open(os.path.join(HERE, "bench_songs.csv"), encoding="utf-8") as fh:
        songs = list(csv.DictReader(fh))
    started = time.monotonic()
    labels, results, stats = bench(songs)
    report(songs, labels, results, stats)
    out = os.path.join(HERE, "..", "build", time.strftime("bench-%Y%m%d-%H%M.csv"))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    save(out, songs, labels, results)
    print(f"\n共 {len(songs)} 首，花了 {(time.monotonic() - started) / 60:.0f} 分鐘；每首的結果在 {os.path.abspath(out)}")
