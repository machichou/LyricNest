"""Run with: python -m unittest discover tests. Uses ffmpeg to make real audio in a temp folder; never touches the music library."""
import base64
import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import wave
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lyricstool import audio, jobs
from lyricstool.clean import clean_lrc, is_credit, strip_credits
from lyricstool.sources import Busy, Hit, Lane, Limiter, NetEase, Paused, OnlineLrclib, QQMusic, Retry, build_sources, Sidecar, make_query, matches, names_for, usable
from lyricstool.store import Store, default_config, text_hash


class Fake:
    """A source that gives a fixed answer and remembers what it was asked."""
    label = "假來源"

    def __init__(self, reply):
        self.reply = reply

    def find(self, q):
        self.q = q
        return self.reply


LRC ="\n".join(f"[00:{i:02d}.50]第 {i} 行" for i in range(10, 25))
CREDITS = """作曲 : 陳綺貞|作词 : 青葉市子|编曲 : 青葉市子|制作和编曲 : 方大同|所有乐器和编程 : Soulboy|数字编辑 : Jeff Li|混音 : Eric Lau|母带处理 : Randy Merrill@Sterling Sound
制作人 : 青葉市子/zAK|音頻編輯Audio Edited by：李卓@TC Faders|Recording Engineering：馬昀迪|Drum Recording Engineering：郭子敬|Recording Studio：南京指音錄音棚
Mixing Engineering：Eric|Mastering Engineering：時俊峰|編曲 Arrangement : 鍾成虎 Tiger|贝斯 : Fred Sablan|吉他 : Chino Moreno/Stephen Carpenter|键盘 : Frank Delgado
母带工程师 : Howie Weinberg/Will Borza|鼓 : Abe Cunningham|录音工程师 : Nathan Yarborough|混音师 : Rich Costey|絃樂編寫Strings Arranged by：張晁毓
樂器錄音Instruments Recorded by：李楊/宋禹@55T.E.C Studio|錄音師（樂器）Recording Engineer (Instruments) : 蔡周翰|錄音室（人聲）Recording Studio (Vocal) : 亞爸的山
母帶後期工作室 Mastering Studio : 有禾錄音室|詞：陳綺貞|曲：陳綺貞|製作人 Producer：鍾成虎 Tiger|木吉他 Acoustic guitar：陳綺貞 Cheer|鼓手 Dummer：陳柏州 Mr. Q
和聲＋和聲編寫 All chorus：陳綺貞 Cheer|Producer Assistant 製作助理：周谷淳 Gibson|Mix Assistant 混音助理：柯宗佑|手風琴：張瀚中|小提琴：王心為|專輯製作人：陳建騏
母帶後期錄音室：鈺德科技|監製：方大同|Feat：黃韻玲|唸白 : Anchi|唱片公司 : 賦音乐|监制 : Khalil Fong|Piano: Febian Reza Pane|Recorded At: Soundtrack Studios
Mixed At: Audio City|Mastered At: Onkio Haus|Photography By: Shoji Uchida|Manufactured By: Toshiba EMI Ltd|Liner Notes: Lisa Ono/Oscar Castro-Neves|Trumpet: Luis Valle
Co-producer/Acoustic Guitar/Backing Vocals/Basic Arrangement Arranged By: Toninho Horta|Phonographic Copyright ℗: Toshiba EMI Ltd|【版權所有，未經許可請勿使用】|©️版權所有，未經許可請勿使用""".replace("\n", "|").split("|")
# Every one of these is a sung line found in the real library.
SUNG = """讓你不斷拍案著說：「喔幹！」|押韻不斷 讓你不斷拍案著說：「喔幹！」|但他拍手 求我：拜托再继续！|她說: "去你媽的花海"|我說: "妳這麼說好帥"|Te disse: aloha, haole na|Dizer: aloha
Bem me avisaram: Não facilite|Each one she passes goes: Ah!|Maybe every 2:30, I go to rest|說個笑話：「世界和平。」|happy happy 谁想要sad:))|Spread love and cc: the whole society
And the wise man say: "I don't want to hear your voice"|The message spirals: Don't get saggy, don't get grey|Los Angeles, 3:33, nothing on the stereo|Four words: forwards, can't we rewind?|她會說：「謝謝你愛我」
Me dices: "ciеlo, cada momento|Lessons learned:  no one's concern|提示: 月光下 答案: 我和你|Thursday, 12:30|And then she asks me, Do I look all right?  然後她問我：我看起來還好嗎？|I could swear I heard someone say: "Please"
"The important thing is: we're awake now.|Dating Courtesy: A girl is right to assume her date|That I'm loving Halo 3: ODST and these Newports that I smoke|裝在透明噴砂的玻璃罐 細看 這牌子寫著：「Soft Lipa」|對 這感覺就是:「Soft Lipa」
la mia voce che ti dice:|Ты, зажмурившись, кричал:|А я скажу в ответ: ты не напугал|My father said: "don't be so scared"|They got one thing in common: they always come back""".replace("\n", "|").split("|")


class Credits(unittest.TestCase):
    def test_real_credit_lines_are_caught(self):
        self.assertEqual([c for c in CREDITS if not is_credit(c)], [])

    def test_real_sung_lines_survive(self):
        self.assertEqual([s for s in SUNG if is_credit(s)], [])

    def test_strip_credits_changes_nothing_else(self):
        raw = "[ti:x]\n[00:01.00]作曲 : A\n[00:02.00]  hello \n\n[00:03.00]Piano: B\n[00:04.00]"
        out, gone = strip_credits(raw)
        self.assertEqual(out, "[ti:x]\n[00:02.00]  hello \n\n[00:04.00]")
        self.assertEqual(gone, ["作曲 : A", "Piano: B"])


class Clean(unittest.TestCase):
    def test_switched_off_keeps_credits_but_never_counts_them(self):
        raw = "[00:00.50]距離 - 陳綺貞\n[00:01.00]作曲 : 某人\n[00:02.00]前奏\n[00:03.00]壞�字\n[00:10.00]第一句\n[00:20.00]第二句\n[00:30.00]第三句"
        c = clean_lrc(raw, "距離", ["陳綺貞"], strip=False)
        for kept in ("距離 - 陳綺貞", "作曲 : 某人", "前奏"):
            self.assertIn(kept, c.lrc)
        self.assertEqual((c.removed, c.sung), (["壞�字"], 3))
        self.assertEqual(clean_lrc(raw, "距離", ["陳綺貞"]).sung, 3)

    def test_order_multistamp_and_tw(self):
        c = clean_lrc("[00:05.00]后来\n[00:01.00][00:03.50]头发\n[00:07.123]软件", "x")
        self.assertEqual(c.lrc, "[00:01.00]頭髮\n[00:03.50]頭髮\n[00:05.00]後來\n[00:07.12]軟件")
        self.assertTrue(c.converted)

    def test_japanese_is_not_converted(self):
        c = clean_lrc("[00:01.00]どこか遠くへ\n[00:02.00]发\n[00:03.00]行きたい", "x")
        self.assertIn("发", c.lrc)
        self.assertFalse(c.converted)

    def test_headers_cues_and_mojibake_go(self):
        raw = "[00:00.38]陳綺貞\n[00:01.96]距離(distance)\n[00:09.58]進一步\n[00:20.00]（前奏）\n[00:21.00]a�b\n[00:30.00]第二句\n[00:40.00]music\n[00:50.00]第三句"
        c = clean_lrc(raw, "距離", ["陳綺貞"])
        self.assertEqual(c.lrc.count("\n"), 2)
        self.assertEqual(c.sung, 3)

    def test_title_later_in_song_is_kept(self):
        c = clean_lrc("[00:30.00]會不會\n[00:40.00]離開\n[00:50.00]會不會", "會不會")
        self.assertEqual(c.sung, 3)

    def test_title_dash_artist_header(self):
        c = clean_lrc("[00:00.10]Undecided - 小野麗莎\n[00:10.00]First\n[00:20.00]Second\n[00:30.00]Third", "Undecided")
        self.assertEqual(c.sung, 3)
        self.assertEqual(c.removed, ["Undecided - 小野麗莎"])

    def test_header_with_censored_title(self):
        c = clean_lrc("[00:00.00]*王 - 蛋堡\n[00:19.06]當個屎王\n[00:21.18]b\n[00:23.50]c", "屎王", ["蛋堡", "Soft Lipa"])
        self.assertEqual(c.removed, ["*王 - 蛋堡"])

    def test_kangxi(self):
        self.assertIn("一", clean_lrc("[00:01.00]⼀個\n[00:02.00]b\n[00:03.00]c", "x", convert=False).lrc)

    def test_unusual_stamps_are_kept_and_normalized(self):
        self.assertEqual(clean_lrc("[00:12]a\n[00:13.5]b\n[00:14:123]c", "x").lrc, "[00:12.00]a\n[00:13.50]b\n[00:14.12]c")

    def test_first_line_that_starts_with_the_title_is_sung(self):
        self.assertEqual(clean_lrc("[00:02.00]愛是一道光\n[00:10.00]b\n[00:20.00]c", "愛").sung, 3)


class Matching(unittest.TestCase):
    def setUp(self):
        self.q = make_query("Lisa Ono", "Tea for Two (Remastered)", "", 200, "", {"lisa ono": ["小野リサ", "Lisa Ono"]})

    def test_alias_and_noise(self):
        self.assertTrue(matches(self.q, "小野リサ", "Tea For Two", 201))

    def test_rejects_length_artist_title(self):
        self.assertFalse(matches(self.q, "小野リサ", "Tea For Two", 204))
        self.assertFalse(matches(self.q, "Someone Else", "Tea For Two", 200))
        self.assertFalse(matches(self.q, "小野リサ", "Night and Day", 200))

    def test_a_language_version_is_not_the_original(self):
        q = make_query("HUNTR/X", "Golden (Tagalog Version)", "", 192, "", {})
        self.assertFalse(matches(q, "HUNTR/X", "Golden", 193))
        self.assertTrue(matches(q, "HUNTR/X", "Golden (Tagalog Version)", 193))
        self.assertFalse(matches(make_query("HUNTR/X", "Golden", "", 192, "", {}), "HUNTR/X", "Golden (Tagalog Version)", 193))
        self.assertTrue(matches(make_query("IVE", "Will (Korean Ver.)", "", 200, "", {}), "IVE", "Will -Korean ver.-", 200))
        self.assertTrue(matches(make_query("鄧紫棋", "喜歡你 (粵語版)", "", 200, "", {}), "邓紫棋", "喜欢你 粤语", 200))

    def test_a_long_wait_rests_the_source_instead_of_holding_the_song(self):
        lim = Limiter("網易雲", 0, [60], jobs.Ctx())

        def refuse():
            raise Retry()

        started = time.monotonic()
        with self.assertRaises(Busy):
            lim.call(refuse)
        self.assertLess(time.monotonic() - started, 1, "the song is not kept waiting a minute")
        with self.assertRaises(Paused):
            lim.call(lambda: "ok")
        self.assertGreater(lim.suspended_until - time.monotonic(), 55, "the source rests for the wait instead")

    def test_lrclib_searches_at_once_when_the_exact_lookup_is_refused(self):
        def reply(url, **headers):
            if "/api/get?" in url:
                raise Retry()  # what a 503 becomes
            return [{"artistName": "小野リサ", "trackName": "Tea For Two", "duration": 200, "syncedLyrics": LRC, "instrumental": False}]

        src = OnlineLrclib(jobs.Ctx())
        src.lim.delay = 0
        started = time.monotonic()
        with mock.patch("lyricstool.sources._get_json", side_effect=reply):
            hit = src.find(self.q)
        self.assertEqual((hit.lrc, src.lim.fails), (LRC, 0))
        self.assertLess(time.monotonic() - started, 1, "no waiting before trying search")

    def test_countdown_reaches_one_then_says_it_is_retrying(self):
        seen = []
        replies = iter([Retry(), "ok"])

        def ask():
            reply = next(replies)
            if isinstance(reply, Exception):
                raise reply
            return reply

        lim = Limiter("來源", 0, [2], jobs.Ctx(lambda kind, text: seen.append(text) if kind == "status" else None))
        self.assertEqual(lim.call(ask), "ok")
        self.assertEqual(seen, ["來源 暫時無法查詢（來源忙碌），2 秒後重試（第 1 次）", "來源 暫時無法查詢（來源忙碌），1 秒後重試（第 1 次）", "來源 重試中（第 1 次）…"])

    def test_qq_music_checks_length_before_taking_lyrics(self):
        def reply(url, **headers):
            if "smartbox" in url:
                return {"data": {"song": {"itemlist": [{"mid": "other", "singer": "別人", "name": "Tea For Two"}, {"mid": "short", "singer": "小野リサ", "name": "Tea For Two"},
                                                       {"mid": "right", "singer": "小野リサ", "name": "Tea For Two"}]}}}
            if "other" in url:
                raise AssertionError("a song by someone else must not cost a length request")
            if "play_single_song" in url:
                return {"data": [{"name": "Tea For Two", "singer": [{"name": "小野リサ"}], "interval": 150 if "short" in url else 200}]}
            return {"lyric": base64.b64encode(LRC.encode()).decode()} if "right" in url else {}

        src = QQMusic(jobs.Ctx())
        src.lim.delay = 0
        with mock.patch("lyricstool.sources._get_json", side_effect=reply):
            hit = src.find(self.q)
        self.assertEqual((hit.lrc, hit.matched_length), (LRC, 200))

    def test_netease_answering_result_as_text_is_a_refusal(self):
        def reply(url, **headers):
            if "search" in url:
                return {"code": 200, "result": songs}
            return {"code": 200, "lrc": {"lyric": LRC}}  # the lyric request has no `result` at all

        src = NetEase(jobs.Ctx())
        src.lim.delay = 0
        songs = "x"
        with mock.patch("lyricstool.sources._get_json", side_effect=reply):
            with self.assertRaises(Busy):
                src.find(self.q)
            self.assertGreater(src.lim.suspended_until, time.monotonic(), "it rests like after any refusal")
            src.lim.suspended_until = 0
            songs = {"songs": [{"id": 1, "name": "Tea For Two", "artists": [{"name": "小野リサ"}], "duration": 200000}]}
            self.assertEqual(src.find(self.q).lrc, LRC)

    def test_sources_ask_netease_before_lrclib(self):
        cfg = {"sources": {"sidecar": True, "lrcget": True, "lrclib": True, "netease": True, "qq": True, "kugou": True}, "lrcget_db": "", "lrclib_db": ""}
        self.assertEqual([s.label for s in build_sources(cfg, jobs.Ctx())], [".lrc 外掛檔", "網易雲", "LRCLIB（線上）", "QQ 音樂", "酷狗"])

    def test_multi_artist_tag(self):
        self.assertIn("蛋堡 Soft Lipa", names_for("黃玠/蛋堡 Soft Lipa", {}))

    def test_usable(self):
        self.assertTrue(usable(LRC, 100))
        self.assertFalse(usable(LRC, 10), "stamps past the end")
        self.assertFalse(usable("[00:01.00]纯音乐，请欣赏\n[00:02.00]b\n[00:03.00]c", 100))
        self.assertFalse(usable("[offset:500]\n" + LRC, 100))
        self.assertFalse(usable("[00:01.00]a", 100))


@unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
class Files(unittest.TestCase):
    """Real audio, because the promise is about real audio: it must decode to the same samples after every write."""

    @classmethod
    def setUpClass(cls):
        cls.src = tempfile.mkdtemp()
        for name, extra in (("a.flac", []), ("b.mp3", ["-b:a", "128k"]), ("c.mp3", ["-b:a", "128k", "-id3v2_version", "3"])):
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-metadata", "artist=歌手", "-metadata", "title=歌名",
                            "-metadata", "lyrics=old words", *extra, os.path.join(cls.src, name)], check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.src)

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.lib = os.path.join(self.d, "music")
        shutil.copytree(self.src, self.lib)
        self.backup = audio.Backup(os.path.join(self.d, "bak", "one"))

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)  # the test's sqlite connection may still hold its file

    def pcm(self, path):
        return hashlib.sha1(subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "s16le", "-"], check=True, capture_output=True).stdout).hexdigest()

    def test_write_then_restore_every_format(self):
        for name in os.listdir(self.lib):
            path = os.path.join(self.lib, name)
            before, pcm = open(path, "rb").read(), self.pcm(path)
            audio.write_lyrics(path, LRC, self.backup)
            self.assertEqual(audio.read_info(path)["lyrics"], LRC, name)
            self.assertEqual(audio.read_info(path)["artist"], "歌手", name)
            self.assertEqual(self.pcm(path), pcm, name)
            self.assertFalse(os.path.exists(path + audio.TMP_SUFFIX))
            row = audio.read_index(self.backup.folder)[path]
            self.assertEqual(row["result"], "ok")
            self.assertIsNone(audio.restore_file(self.backup.folder, row))
            self.assertEqual(open(path, "rb").read(), before, name)

    def test_long_lyrics_need_more_room(self):
        path = os.path.join(self.lib, "a.flac")
        pcm = self.pcm(path)
        big = "\n".join(f"[{i // 60:02d}:{i % 60:02d}.00]{'長' * 200}" for i in range(600))
        audio.write_lyrics(path, big, self.backup)
        self.assertEqual(audio.read_info(path)["lyrics"], big)
        self.assertEqual(self.pcm(path), pcm)

    def test_mp3_without_any_tag(self):
        path = os.path.join(self.lib, "plain.mp3")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=duration=1", "-write_id3v2", "0", "-id3v2_version", "0", path], check=True)
        pcm = self.pcm(path)
        audio.write_lyrics(path, LRC, self.backup)
        self.assertEqual(audio.read_info(path)["lyrics"], LRC)
        self.assertEqual(self.pcm(path), pcm)

    def test_locked_file_keeps_original(self):
        path = os.path.join(self.lib, "a.flac")
        before = open(path, "rb").read()
        with open(path, "rb") as fh:  # what a player or an upload holding the file looks like on Windows
            with self.assertRaises(audio.WriteError):
                audio.write_lyrics(path, LRC, self.backup)
            self.assertEqual(fh.read(), before)
        self.assertEqual(open(path, "rb").read(), before)
        self.assertTrue(audio.read_index(self.backup.folder)[path]["result"].startswith("FAIL"))
        self.assertFalse(os.path.exists(path + audio.TMP_SUFFIX))

    def test_sidecar_encodings(self):
        path = os.path.join(self.lib, "a.flac")
        lrc_path = os.path.join(self.lib, "a.lrc")
        q = make_query("歌手", "歌名", "", 100, path, {})
        for encoding in ("utf-8-sig", "utf-16"):
            with open(lrc_path, "w", encoding=encoding, newline="") as fh:
                fh.write(LRC)
            self.assertEqual(Sidecar().find(q).lrc, LRC, encoding)
        with open(lrc_path, "wb") as fh:
            fh.write(LRC.encode("big5"))
        self.assertIsNone(Sidecar().find(q), "a guessed encoding could embed garbled text")

    def test_not_a_flac(self):
        path = os.path.join(self.lib, "fake.flac")
        open(path, "wb").write(b"not audio at all")
        with self.assertRaises(Exception):
            audio.write_lyrics(path, LRC, self.backup)
        self.assertEqual(open(path, "rb").read(), b"not audio at all")

    def test_restore_refuses_changed_audio(self):
        path = os.path.join(self.lib, "a.flac")
        audio.write_lyrics(path, LRC, self.backup)
        with open(path, "ab") as fh:
            fh.write(b"x")
        self.assertEqual(audio.restore_file(self.backup.folder, audio.read_index(self.backup.folder)[path]), "音訊與備份時不同")

    def config(self, **kw):
        return {"library": self.lib, "backup_root": os.path.join(self.d, "bak"), "excluded": ["tea"], "aliases": {"歌手": ["歌手", "Singer"]}, "to_traditional": True, "remove_credits": True, "backup_enabled": True, "skip_albums": False, "sources": {}, **kw}

    def test_whole_flow(self):
        os.makedirs(os.path.join(self.lib, "tea"))
        os.makedirs(os.path.join(self.lib, "teardrop"))
        shutil.copy(os.path.join(self.lib, "a.flac"), os.path.join(self.lib, "tea", "skip.flac"))
        shutil.copy(os.path.join(self.lib, "a.flac"), os.path.join(self.lib, "teardrop", "keep.flac"))
        open(os.path.join(self.lib, "broken.flac"), "wb").close()
        open(os.path.join(self.lib, "x.flac" + audio.TMP_SUFFIX), "wb").close()
        store, cfg, ctx = Store(os.path.join(self.d, "s.sqlite")), self.config(), jobs.Ctx()
        jobs.run_scan(store, cfg, ctx)
        self.assertEqual(store.counts(), {"plain": 2, "none": 2, "unreadable": 1})  # ffmpeg's lyrics tag only lands in FLAC
        self.assertIsNone(store.track(os.path.join(self.lib, "tea", "skip.flac")))
        self.assertFalse(os.path.exists(os.path.join(self.lib, "x.flac" + audio.TMP_SUFFIX)))

        target = os.path.join(self.lib, "a.flac")
        raw = "[00:00.50]歌名 - 歌手\n[00:10.00]后来\n[00:11.00]作曲 : 某人\n" + LRC
        t = store.db.execute("select *, 0 status from tracks where path=?", (target,)).fetchone()
        src = Fake(Hit("假來源", raw, matched_artist="歌手", matched_title="歌名", matched_length=2.0))
        self.assertEqual(jobs.lookup_one([src], t, cfg, store, ctx), "found")
        self.assertIn("Singer", src.q.names)
        item = store.pending_items()[0]
        self.assertNotIn("作曲", item["lrc"])
        self.assertIn("後來", item["lrc"])
        self.assertEqual(sorted(json.loads(item["removed"])), sorted(["歌名 - 歌手", "作曲 : 某人"]))
        self.assertIn("會取代純文字歌詞", json.loads(item["flags"]))
        self.assertEqual(jobs.action_text(item), "取代純文字歌詞")

        # The file was tagged by someone else while the list waited: it must be left alone.
        other = os.path.join(self.lib, "b.mp3")
        t2 = store.db.execute("select *, 0 status from tracks where path=?", (other,)).fetchone()
        jobs.lookup_one([Fake(Hit("假來源", LRC, matched_title="歌名", matched_length=2.0))], t2, cfg, store, ctx)
        audio.write_lyrics(other, LRC, audio.Backup(os.path.join(self.d, "bak", "other")))

        res = jobs.run_write(store, cfg, ctx)
        self.assertEqual(res["ok"], 1)
        self.assertEqual(res["stale"], [(other, "已經有同步歌詞")])
        self.assertEqual(store.track(target)["state"], "synced")
        self.assertEqual(jobs.run_write(store, cfg, ctx)["ok"], 0, "nothing is written twice")
        names = [b["name"] for b in jobs.list_backups(cfg["backup_root"])]
        self.assertIn(os.path.basename(res["folder"]), names)
        self.assertEqual(jobs.run_restore(store, cfg, ctx, res["folder"])["done"], 1)
        self.assertEqual(store.track(target)["state"], "plain")

    def test_scan_keeps_data_when_drive_is_gone(self):
        store, cfg, ctx = Store(os.path.join(self.d, "s.sqlite")), self.config(), jobs.Ctx()
        jobs.run_scan(store, cfg, ctx)
        n = sum(store.counts().values())
        for f in os.listdir(self.lib):
            os.remove(os.path.join(self.lib, f))
        with self.assertRaises(ValueError):
            jobs.run_scan(store, cfg, ctx)
        self.assertEqual(sum(store.counts().values()), n)

    def test_backup_inside_library_holds_nothing_that_looks_like_music(self):
        store, cfg, ctx = Store(os.path.join(self.d, "s.sqlite")), self.config(backup_root=os.path.join(self.lib, "bak")), jobs.Ctx()
        jobs.run_scan(store, cfg, ctx)
        before = sum(store.counts().values())
        target = os.path.join(self.lib, "a.flac")
        store.put_item(target, "new", "x", LRC, [], [], 15, "", 2, text_hash(store.track(target)["lyrics"]))
        self.assertEqual(jobs.run_write(store, cfg, ctx)["ok"], 1)
        self.assertEqual(len(jobs.list_backups(cfg["backup_root"])), 1)
        jobs.run_scan(store, cfg, ctx)
        self.assertEqual(sum(store.counts().values()), before)
        names = [f for _, _, files in os.walk(cfg["backup_root"]) for f in files]
        self.assertEqual([f for f in names if os.path.splitext(f)[1].lower() in audio.AUDIO], [], "music software would list these as duplicate songs")

    def test_writing_with_backups_off_keeps_every_other_safeguard(self):
        store, cfg, ctx = Store(os.path.join(self.d, "s.sqlite")), self.config(backup_enabled=False, backup_root=os.path.join(self.d, "unused")), jobs.Ctx()
        jobs.run_scan(store, cfg, ctx)
        target = os.path.join(self.lib, "a.flac")
        pcm = self.pcm(target)
        store.put_item(target, "new", "x", LRC, [], [], 15, "", 2, text_hash(store.track(target)["lyrics"]))
        self.assertEqual(jobs.run_write(store, cfg, ctx)["ok"], 1)
        self.assertEqual((audio.read_info(target)["lyrics"], self.pcm(target)), (LRC, pcm))
        self.assertFalse(os.path.exists(cfg["backup_root"]))
        before = open(target, "rb").read()
        with mock.patch.object(audio, "_replace", side_effect=OSError("被占用")):
            with self.assertRaises(audio.WriteError):
                audio.write_lyrics(target, LRC + "\n[00:40.00]多一行", None)
        self.assertEqual(open(target, "rb").read(), before)

    def test_quality_is_read_for_every_kind_of_file(self):
        flac, mp3, wav, broken = (os.path.join(self.lib, n) for n in ("a.flac", "b.mp3", "x.wav", "y.ogg"))
        self.assertEqual(audio.read_info(flac)["quality"], "16/44.1kHz")
        self.assertRegex(audio.read_info(mp3)["quality"], r"^\d+kbps/44\.1kHz$")
        with wave.open(wav, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(3)
            w.setframerate(96000)
            w.writeframes(b"\0\0\0" * 960)
        open(broken, "wb").close()
        store = Store(os.path.join(self.d, "s.sqlite"))
        jobs.run_scan(store, self.config(), jobs.Ctx())
        self.assertEqual(store.track(flac)["quality"], "16/44.1kHz")
        self.assertEqual((store.track(wav)["quality"], store.track(wav)["state"]), ("24/96.0kHz", "unsupported"))
        self.assertEqual(store.track(broken)["quality"], "")

    def test_defaults_assume_nothing_about_the_computer(self):
        with mock.patch.dict(os.environ, {"LYRICSTOOL_DATA": self.d}):
            cfg = default_config()
        self.assertEqual((cfg["library"], cfg["excluded"], cfg["lrclib_db"], cfg["aliases"]), ("", [], "", {}))
        self.assertEqual(cfg["backup_root"], os.path.join(self.d, "backups"))
        self.assertTrue(cfg["backup_enabled"])

    def track(self, store, name):
        return store.db.execute("select *, 0 status from tracks where path=?", (os.path.join(self.lib, name),)).fetchone()

    def test_first_lyrics_found_win_without_waiting_for_a_slow_source(self):
        store, cfg, ctx = Store(os.path.join(self.d, "s.sqlite")), self.config(), jobs.Ctx()
        jobs.run_scan(store, cfg, ctx)
        asked = []

        class Slow:
            label = "慢"

            def find(self, q):
                asked.append(q.path)
                time.sleep(1)
                return None

        slow, fast = Slow(), Fake(Hit("快", LRC, matched_title="歌名", matched_length=2.0))
        lanes = {s: Lane(s) for s in (slow, fast)}
        started = time.monotonic()
        for name in ("a.flac", "b.mp3"):
            self.assertEqual(jobs.lookup_one([slow, fast], self.track(store, name), cfg, store, ctx, lanes=lanes), "found")
        self.assertLess(time.monotonic() - started, 0.9, "the slow source held nothing up")
        time.sleep(1.5)
        self.assertEqual([os.path.basename(p) for p in asked], ["a.flac"], "the second song was settled before the slow source got to it")
        for lane in lanes.values():
            lane.close()

    def test_one_source_saying_instrumental_is_not_final(self):
        store, cfg, ctx = Store(os.path.join(self.d, "s.sqlite")), self.config(), jobs.Ctx()
        jobs.run_scan(store, cfg, ctx)
        says_none = Fake(Hit("甲", instrumental=True))
        self.assertEqual(jobs.lookup_one([says_none, Fake(Hit("乙", LRC, matched_title="歌名", matched_length=2.0))], self.track(store, "a.flac"), cfg, store, ctx), "found")
        self.assertTrue(store.pending_items()[0]["source"].startswith("乙"))
        self.assertEqual(jobs.lookup_one([says_none, Fake(None)], self.track(store, "b.mp3"), cfg, store, ctx), "instrumental")

    def test_a_busy_source_is_asked_again_even_after_another_says_instrumental(self):
        class Refuses:
            label = "限流"

            def find(self, q):
                raise jobs.Busy("限流")

        store, cfg, ctx = Store(os.path.join(self.d, "s.sqlite")), self.config(), jobs.Ctx()
        jobs.run_scan(store, cfg, ctx)
        says_none = Fake(Hit("甲", instrumental=True))
        for name in ("a.flac", "b.mp3"):
            self.assertEqual(jobs.lookup_one([says_none, Refuses()], self.track(store, name), cfg, store, ctx), "busy")
        self.assertEqual(jobs.lookup_one([Fake(None)], self.track(store, "a.flac"), cfg, store, ctx), "instrumental", "the earlier answer still counts")
        self.assertEqual(jobs.lookup_one([Fake(Hit("乙", LRC, matched_title="歌名", matched_length=2.0))], self.track(store, "b.mp3"), cfg, store, ctx), "found")

    def test_throttled_source_means_ask_again_not_missing(self):
        class Refuses:
            label = "限流"

            def find(self, q):
                raise jobs.Busy("限流")

        store, cfg, ctx = Store(os.path.join(self.d, "s.sqlite")), self.config(), jobs.Ctx()
        jobs.run_scan(store, cfg, ctx)
        self.assertEqual(jobs.lookup_one([Refuses(), Fake(None)], self.track(store, "a.flac"), cfg, store, ctx), "busy")
        self.assertEqual(jobs.lookup_one([Fake(None)], self.track(store, "a.flac"), cfg, store, ctx), "notfound")

        class Resting:
            label = "休息"

            def find(self, q):
                lim = Limiter("休息", 0, [], ctx)
                lim.suspended_until = time.monotonic() + 60
                return lim.call(lambda: None)

        resting, failed = Resting(), {}
        self.assertEqual(jobs.lookup_one([resting, Fake(None)], self.track(store, "a.flac"), cfg, store, ctx, failed), "busy")
        self.assertEqual(failed, {resting: "暫停中"}, "a paused source is noted as paused, not as refusing")

    def test_retry_asks_only_the_source_that_failed(self):
        class Flaky:
            label = "時好時壞"
            calls = 0

            def find(self, q):
                self.calls += 1
                if self.calls <= 3:  # every track fails once: a.flac, b.mp3, c.mp3
                    raise jobs.Busy("限流")
                return Hit("時好時壞", LRC, matched_title="歌名", matched_length=2.0)

        class Counting(Fake):
            calls = 0

            def find(self, q):
                self.calls += 1
                return None

        class NoWait(jobs.Ctx):
            def wait(self, seconds, template):
                self.waited = seconds

        flaky, other = Flaky(), Counting(None)
        store, cfg, ctx = Store(os.path.join(self.d, "s.sqlite")), self.config(), NoWait()
        jobs.run_scan(store, cfg, ctx)
        with mock.patch.object(jobs, "build_sources", lambda c, x: [flaky, other]):
            counts = jobs.run_lookup(store, cfg, ctx)
        self.assertEqual(counts, {"found": 3})
        self.assertEqual(other.calls, 3, "it already answered in the first round")
        self.assertEqual(ctx.waited, 60)
        self.assertEqual(store.reasons(), {"found": 3})

    def test_an_album_nothing_has_lyrics_for_is_given_up(self):
        for name in ("d.flac", "e.flac"):
            shutil.copy(os.path.join(self.lib, "a.flac"), os.path.join(self.lib, name))
        asked = Fake(None)
        asked.calls = 0
        asked.find = lambda q: setattr(asked, "calls", asked.calls + 1)
        store, ctx = Store(os.path.join(self.d, "s.sqlite")), jobs.Ctx()
        cfg = self.config(skip_albums=True)
        jobs.run_scan(store, cfg, ctx)
        with mock.patch.object(jobs, "build_sources", lambda c, x: [asked]):
            self.assertEqual(jobs.run_lookup(store, cfg, ctx), {"notfound": 3, "skipped": 2})
        self.assertEqual(asked.calls, 3, "the last two songs were not asked")
        self.assertEqual(store.reasons(), {"notfound": 5})
        with mock.patch.object(jobs, "build_sources", lambda c, x: [Fake(None)]):
            off = self.config()
            store.db.execute("delete from lookups")
            self.assertEqual(jobs.run_lookup(store, off, ctx), {"notfound": 5}, "switched off, every song is asked")
        class FindsOnlyTheFirst:
            label = "只找到一首"
            replies = [Hit("乙", LRC, matched_title="歌名", matched_length=2.0)]

            def find(self, q):
                return self.replies.pop() if self.replies else None

        with mock.patch.object(jobs, "build_sources", lambda c, x: [FindsOnlyTheFirst()]):
            store.db.execute("delete from lookups")
            self.assertEqual(jobs.run_lookup(store, cfg, ctx), {"found": 1, "notfound": 4}, "one song found among the first three: the whole album is asked")

    def test_retries_give_up_and_stay_on_the_to_do_list(self):
        class Refuses:
            label = "一直限流"

            def find(self, q):
                raise jobs.Busy("限流")

        class NoWait(jobs.Ctx):
            def wait(self, seconds, template):
                pass

        store, cfg, ctx = Store(os.path.join(self.d, "s.sqlite")), self.config(), NoWait()
        jobs.run_scan(store, cfg, ctx)
        logs = []
        ctx.emit = lambda kind, text: logs.append(text) if kind == "log" else None
        says_none = Fake(Hit("甲", instrumental=True))
        with mock.patch.object(jobs, "build_sources", lambda c, x: [Refuses(), says_none]):
            self.assertEqual(jobs.run_lookup(store, cfg, ctx), {"busy": 3})
        self.assertTrue(any(line.startswith("待重查（一直限流 忙碌）：") for line in logs), "the log says which source and why")
        targets = store.lookup_targets(("never", "busy"))
        self.assertEqual([t["source"] for t in targets], ["一直限流；純音樂：甲"] * 3, "which source is owed, and what the others said")

        class Answers:
            label = "一直限流"

            def find(self, q):
                return None

        class Counting(Fake):
            calls = 0

            def find(self, q):
                self.calls += 1

        other = Counting(None)
        with mock.patch.object(jobs, "build_sources", lambda c, x: [Answers(), other]):
            self.assertEqual(jobs.run_lookup(store, cfg, ctx), {"instrumental": 3}, "the next run settles them with the earlier answer")
        self.assertEqual(other.calls, 0, "a source that already answered is not asked again")

    def test_stopping_counts_the_songs_still_to_ask_again(self):
        class Refuses:
            label = "一直限流"

            def find(self, q):
                raise jobs.Busy("限流")

        class StopsWhileWaiting(jobs.Ctx):
            def wait(self, seconds, template):
                self.cancel()

        store, cfg, ctx = Store(os.path.join(self.d, "s.sqlite")), self.config(), StopsWhileWaiting()
        jobs.run_scan(store, cfg, ctx)
        logs = []
        ctx.emit = lambda kind, text: logs.append(text) if kind == "log" else None
        with mock.patch.object(jobs, "build_sources", lambda c, x: [Refuses()]):
            self.assertEqual(jobs.run_lookup(store, cfg, ctx), {"busy": 3})
        self.assertEqual(logs[-1], "已停止。查詢結果已儲存，下次會接續查詢：待重查 3 首")

    def test_a_round_of_asking_again_waits_out_a_short_rest_but_not_a_long_one(self):
        class Rests:
            label = "會休息"

            def __init__(self, rest):
                self.lim, self.rest = Limiter("會休息", 0, [], jobs.Ctx()), rest
                self.replies = ["refuse", "refuse"] + [Hit("會休息", LRC, matched_title="歌名", matched_length=2.0)] * 3

            def find(self, q):
                if self.lim.suspended_until > time.monotonic():
                    raise Paused(self.label)
                reply = self.replies.pop(0)
                if reply == "refuse":
                    self.lim.suspended_until = time.monotonic() + self.rest
                    raise Busy(self.label)
                return reply

        for rest in (60, 600):
            src = Rests(rest)

            class TimePasses(jobs.Ctx):
                def wait(self, seconds, template):
                    src.lim.suspended_until = 0

            store, cfg, ctx = Store(os.path.join(self.d, f"{rest}.sqlite")), self.config(), TimePasses()
            jobs.run_scan(store, cfg, ctx)
            logs = []
            ctx.emit = lambda kind, text: logs.append(text) if kind == "log" else None
            with mock.patch.object(jobs, "build_sources", lambda c, x: [src]):
                self.assertEqual(jobs.run_lookup(store, cfg, ctx), {"found": 3})
            if rest == 60:
                rounds = [i for i, line in enumerate(logs) if "輪）" in line]
                self.assertEqual(sum(line.startswith("找到") for line in logs[rounds[0]:rounds[1]]), 2, "after the first song's refusal, the other two wait and are found")
                self.assertFalse(any("仍在休息" in line for line in logs))
            else:
                self.assertIn("會休息 仍在休息，剩下 2 首留到下一輪", logs, "a long rest ends the round instead of being waited out")

    def test_exports_can_be_read_back(self):
        store, cfg, ctx = Store(os.path.join(self.d, "s.sqlite")), self.config(), jobs.Ctx()
        jobs.run_scan(store, cfg, ctx)
        jobs.lookup_one([Fake(Hit("乙", "[00:00.50]歌名 - 歌手\n" + LRC, matched_title="歌名", matched_length=2.0))], self.track(store, "a.flac"), cfg, store, ctx)
        review, missing = os.path.join(self.d, "r.csv"), os.path.join(self.d, "m.csv")
        jobs.export_review(store.pending_items(), review)
        jobs.export_missing(store.missing_rows(), missing)
        with open(review, encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual((len(rows), rows[0]["歌手"], rows[0]["刪除的內容"]), (1, "歌手", "歌名 - 歌手"))
        with open(missing, encoding="utf-8-sig", newline="") as fh:
            self.assertEqual(sorted(r["查詢結果"] for r in csv.DictReader(fh)), ["尚未查詢", "尚未查詢", "待審閱"])

    def test_censored_words_are_flagged(self):
        store, cfg, ctx = Store(os.path.join(self.d, "s.sqlite")), self.config(), jobs.Ctx()
        jobs.run_scan(store, cfg, ctx)
        jobs.lookup_one([Fake(Hit("乙", LRC + "\n[00:40.00]擦**最好的技術", matched_title="歌名", matched_length=2.0))], self.track(store, "a.flac"), cfg, store, ctx)
        self.assertIn("部分文字被遮蔽（*）", json.loads(store.pending_items()[0]["flags"]))

    def test_a_source_crashing_does_not_lose_the_track(self):
        class Crashes:
            label = "壞掉"

            def find(self, q):
                raise KeyError("unexpected reply")

        store, cfg, ctx = Store(os.path.join(self.d, "s.sqlite")), self.config(), jobs.Ctx()
        jobs.run_scan(store, cfg, ctx)
        self.assertEqual(jobs.lookup_one([Crashes(), Fake(Hit("乙", LRC, matched_title="歌名", matched_length=2.0))], self.track(store, "a.flac"), cfg, store, ctx), "found")

    def test_dismissed_answer_stays_gone_but_a_different_one_returns(self):
        store, cfg, ctx = Store(os.path.join(self.d, "s.sqlite")), self.config(), jobs.Ctx()
        jobs.run_scan(store, cfg, ctx)
        same = Fake(Hit("乙", LRC, matched_title="歌名", matched_length=2.0))
        jobs.lookup_one([same], self.track(store, "a.flac"), cfg, store, ctx)
        store.set_item_state(store.pending_items()[0]["id"], "dismissed")
        self.assertEqual(jobs.lookup_one([same], self.track(store, "a.flac"), cfg, store, ctx), "dismissed")
        self.assertEqual(store.pending_items(), [])
        other = Fake(Hit("丙", LRC.replace("第", "另"), matched_title="歌名", matched_length=2.0))
        self.assertEqual(jobs.lookup_one([other], self.track(store, "a.flac"), cfg, store, ctx), "found")
        self.assertEqual(len(store.pending_items()), 1)

    def test_rescan_keeps_unticked_rows(self):
        store, cfg, ctx = Store(os.path.join(self.d, "s.sqlite")), self.config(), jobs.Ctx()
        audio.write_lyrics(os.path.join(self.lib, "a.flac"), "[00:01.00]作曲 : 某人\n" + LRC, self.backup)
        jobs.run_scan(store, cfg, ctx)
        it = store.pending_items()[0]
        store.set_selected([it["id"]], False)
        jobs.run_scan(store, cfg, ctx)
        again = store.pending_items()[0]
        self.assertEqual((again["id"], again["selected"]), (it["id"], 0))
        self.assertEqual(store.selected_items(), [])

    def test_review_items_survive_a_drive_dropping_out(self):
        store, cfg, ctx = Store(os.path.join(self.d, "s.sqlite")), self.config(), jobs.Ctx()
        jobs.run_scan(store, cfg, ctx)
        path = os.path.join(self.lib, "a.flac")
        jobs.lookup_one([Fake(Hit("乙", LRC, matched_title="歌名", matched_length=2.0))], self.track(store, "a.flac"), cfg, store, ctx)
        away = os.path.join(self.d, "away.flac")
        os.replace(path, away)
        jobs.run_scan(store, cfg, ctx)
        self.assertEqual(store.pending_items(), [])
        self.assertEqual(jobs.run_write(store, cfg, ctx)["stale"], [], "a file that is gone is not even attempted")
        os.replace(away, path)
        jobs.run_scan(store, cfg, ctx)
        self.assertEqual(len(store.pending_items()), 1)
        self.assertEqual(jobs.run_write(store, cfg, ctx)["ok"], 1)

    def test_lyrics_edited_after_lookup_are_not_overwritten(self):
        store, cfg, ctx = Store(os.path.join(self.d, "s.sqlite")), self.config(), jobs.Ctx()
        jobs.run_scan(store, cfg, ctx)
        path = os.path.join(self.lib, "a.flac")
        jobs.lookup_one([Fake(Hit("乙", LRC, matched_title="歌名", matched_length=2.0))], self.track(store, "a.flac"), cfg, store, ctx)
        audio.write_lyrics(path, "我自己打的歌詞", self.backup)
        self.assertEqual(jobs.run_write(store, cfg, ctx)["stale"], [(path, "歌詞已被修改")])
        self.assertEqual(audio.read_info(path)["lyrics"], "我自己打的歌詞")

    def test_failed_write_stays_on_the_list(self):
        store, cfg, ctx = Store(os.path.join(self.d, "s.sqlite")), self.config(), jobs.Ctx()
        jobs.run_scan(store, cfg, ctx)
        path = os.path.join(self.lib, "a.flac")
        jobs.lookup_one([Fake(Hit("乙", LRC, matched_title="歌名", matched_length=2.0))], self.track(store, "a.flac"), cfg, store, ctx)
        with open(path, "rb"):
            res = jobs.run_write(store, cfg, ctx)
        self.assertEqual(len(res["failed"]), 1)
        self.assertEqual(len(store.pending_items()), 1)
        self.assertEqual(jobs.run_write(store, cfg, ctx)["ok"], 1)

    def test_stop_during_writing_is_clean(self):
        store, cfg, _ = Store(os.path.join(self.d, "s.sqlite")), self.config(), jobs.Ctx()
        jobs.run_scan(store, cfg, jobs.Ctx())
        for name in ("a.flac", "b.mp3", "c.mp3"):
            jobs.lookup_one([Fake(Hit("乙", LRC, matched_title="歌名", matched_length=2.0))], self.track(store, name), cfg, store, jobs.Ctx())
        ctx = jobs.Ctx()
        ctx.emit = lambda kind, p: ctx.cancel() if kind == "progress" and p[0] == 1 else None  # pressed while the second file is being written
        res = jobs.run_write(store, cfg, ctx)
        self.assertEqual(res["ok"], 2, "the file in hand is finished, nothing after it is started")
        self.assertEqual(len(store.pending_items()), 1)

    def test_cleanup_flow_and_dismiss_sticks(self):
        store, cfg, ctx = Store(os.path.join(self.d, "s.sqlite")), self.config(), jobs.Ctx()
        path = os.path.join(self.lib, "a.flac")
        audio.write_lyrics(path, "[00:01.00]作曲 : 某人\n" + LRC, self.backup)
        jobs.run_scan(store, cfg, ctx)
        it = store.pending_items()[0]
        self.assertEqual(it["kind"], "cleanup")
        store.set_item_state(it["id"], "dismissed")
        jobs.suggest_cleanups(store)
        self.assertEqual(store.pending_items(), [])
        store.db.execute("update items set state='pending' where id=?", (it["id"],))
        self.assertEqual(jobs.run_write(store, cfg, ctx)["ok"], 1)
        self.assertEqual(audio.read_info(path)["lyrics"], LRC)
        jobs.run_scan(store, cfg, ctx)
        self.assertEqual(store.pending_items(), [])

    def test_credit_switch(self):
        store, ctx = Store(os.path.join(self.d, "s.sqlite")), jobs.Ctx()
        on, off = self.config(), self.config(remove_credits=False)
        audio.write_lyrics(os.path.join(self.lib, "a.flac"), "[00:01.00]作曲 : 某人\n" + LRC, self.backup)
        jobs.run_scan(store, on, ctx)
        self.assertEqual(len(store.pending_items()), 1)
        self.assertEqual(store.pending_items()[0]["source"], "現有歌詞")
        jobs.run_scan(store, off, ctx)
        self.assertEqual(store.pending_items(), [], "off withdraws the suggestion")
        jobs.run_scan(store, on, ctx)
        self.assertEqual(len(store.pending_items()), 1, "on brings it back")
        jobs.run_scan(store, off, ctx)

        t = store.db.execute("select *, 0 status from tracks where path=?", (os.path.join(self.lib, "b.mp3"),)).fetchone()
        credits_only = "[00:01.00]作曲 : 甲\n[00:02.00]編曲 : 乙\n[00:03.00]混音 : 丙\n[00:04.00]母帶 : 丁"
        self.assertEqual(jobs.lookup_one([Fake(Hit("假來源", credits_only, matched_title="歌名", matched_length=2.0))], t, off, store, ctx), "notfound", "credits alone are not lyrics, kept or not")
        self.assertEqual(jobs.lookup_one([Fake(Hit("假來源", "[00:01.00]作曲 : 甲\n" + LRC, matched_title="歌名", matched_length=2.0))], t, off, store, ctx), "found")
        item = store.pending_items()[0]
        self.assertIn("作曲 : 甲", item["lrc"])
        self.assertEqual(json.loads(item["removed"]), [])


if __name__ == "__main__":
    unittest.main()
