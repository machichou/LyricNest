"""Settings and the app's own database, under %LOCALAPPDATA%\\LyricsTool. Music files are never touched here."""
import hashlib
import json
import os
import sqlite3
import threading
import time

SCHEMA = """
create table if not exists tracks(path text primary key, ext text, size int, mtime real, artist text, album_artist text, title text, album text,
    length real, state text, lyrics text, error text, quality text);
create index if not exists tracks_state on tracks(state);
create table if not exists lookups(path text primary key, status text, source text, at real);
create table if not exists items(id integer primary key, path text, kind text, source text, lrc text, removed text, flags text, lines int,
    matched text, matched_length real, base_hash text, selected int default 1, state text default 'pending', at real, unique(path, kind));
create index if not exists items_state on items(state);
"""
WHY_SQL = "case when t.state in('unsupported','unreadable') then t.state else coalesce(l.status,'never') end why"


def data_dir():
    d = os.environ.get("LYRICSTOOL_DATA") or os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "LyricsTool")
    os.makedirs(d, exist_ok=True)
    return d


def default_config():
    lrcget = os.path.join(os.environ.get("APPDATA", ""), "net.lrclib.lrcget", "db.sqlite3")
    return {"library": "",
            "backup_root": os.path.join(data_dir(), "backups"),
            "excluded": [],
            "lrclib_db": "",
            "lrcget_db": lrcget if os.path.exists(lrcget) else "",
            "to_traditional": True,
            "remove_credits": True,
            "backup_enabled": True,
            "sources": {"sidecar": True, "lrcget": True, "lrclib": True, "netease": True, "kugou": True},
            "aliases": {}}


def load_config():
    cfg = default_config()
    p = os.path.join(data_dir(), "settings.json")
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as fh:
                cfg.update(json.load(fh))
        except (OSError, ValueError):
            os.replace(p, p + ".bad")
    return cfg


def save_config(cfg):
    p = os.path.join(data_dir(), "settings.json")
    with open(p + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=1)
    os.replace(p + ".tmp", p)


def text_hash(s):
    return hashlib.sha1((s or "").encode("utf-8")).hexdigest()


class Store:
    """One connection per thread; every call commits, so stopping or crashing loses nothing already found."""

    def __init__(self, path=None):
        self.path = path or os.path.join(data_dir(), "state.sqlite")
        self._local = threading.local()
        self.db.executescript(SCHEMA)

    @property
    def db(self):
        c = getattr(self._local, "c", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=30)
            c.row_factory = sqlite3.Row
            c.execute("pragma journal_mode=wal")
            c.execute("pragma synchronous=normal")
            self._local.c = c
        return c

    def track(self, path):
        return self.db.execute("select * from tracks where path=?", (path,)).fetchone()

    def known_files(self):
        return {r["path"]: (r["size"], r["mtime"]) for r in self.db.execute("select path,size,mtime from tracks")}

    def put_track(self, path, ext, size, mtime, info, state, error=""):
        self.db.execute("insert or replace into tracks values(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (path, ext, size, mtime, info.get("artist", ""), info.get("album_artist", ""), info.get("title", ""), info.get("album", ""),
                         info.get("length", 0), state, info.get("lyrics", ""), error, info.get("quality", "")))

    def drop_tracks(self, paths):
        self.db.executemany("delete from tracks where path=?", [(p,) for p in paths])

    def commit(self):
        self.db.commit()

    def counts(self):
        return {r["state"]: r["n"] for r in self.db.execute("select state,count(*) n from tracks group by state")}

    def reasons(self):
        """Tracks without synced lyrics, by what lookups said about them."""
        rows = self.db.execute(f"select {WHY_SQL}, count(*) n from tracks t left join lookups l on l.path=t.path where t.state<>'synced' group by why")
        return {r["why"]: r["n"] for r in rows}

    def tracks_in(self, states):
        """Everything the overview's song lists show, without the lyrics: those add up to megabytes, and only one song at a time needs them."""
        marks = ",".join("?" * len(states))
        return self.db.execute(f"""select t.path,t.ext,t.size,t.artist,t.album_artist,t.title,t.album,t.length,t.state,t.error,t.quality,l.source,l.at,{WHY_SQL}
                                   from tracks t left join lookups l on l.path=t.path where t.state in({marks}) order by t.artist,t.album,t.path""", states).fetchall()

    def missing_rows(self):
        return self.db.execute("""select t.artist,t.title,t.album,t.state,t.ext,coalesce(l.status,'never') status,t.path from tracks t left join lookups l on l.path=t.path
                                  where t.state<>'synced' order by t.artist,t.album,t.path""").fetchall()

    def top_missing(self, limit=12):
        return self.db.execute("""select t.artist,count(*) n from tracks t join lookups l on l.path=t.path
                                  where t.state in('none','plain') and l.status='notfound' and t.artist<>'' group by t.artist order by n desc limit ?""", (limit,)).fetchall()

    def lookup_targets(self, statuses, text=""):
        """Tracks that can take lyrics, whose last lookup status is one of `statuses` ('never' = not asked yet)."""
        marks = ",".join("?" * len(statuses))
        sql = f"""select t.*,coalesce(l.status,'never') status from tracks t left join lookups l on l.path=t.path
                  where t.state in('none','plain') and coalesce(l.status,'never') in ({marks})"""
        args = list(statuses)
        if text:
            sql += " and (t.artist like ? or t.album like ? or t.title like ? or t.path like ?)"
            args += [f"%{text}%"] * 4
        return self.db.execute(sql + " order by t.path", args).fetchall()

    def save_lookup(self, path, status, source=""):
        self.db.execute("insert or replace into lookups values(?,?,?,?)", (path, status, source, time.time()))
        self.db.commit()

    def put_item(self, path, kind, source, lrc, removed, flags, lines, matched, matched_length, base_hash):
        """Returns 'added', or the state of an identical suggestion already there: a rescan must not undo someone's untick or dismissal."""
        old = self.db.execute("select state,base_hash,lrc from items where path=? and kind=?", (path, kind)).fetchone()
        if old and old["state"] in ("dismissed", "pending") and old["base_hash"] == base_hash and old["lrc"] == lrc:
            return old["state"]
        self.db.execute("""insert or replace into items(path,kind,source,lrc,removed,flags,lines,matched,matched_length,base_hash,selected,state,at)
                           values(?,?,?,?,?,?,?,?,?,?,1,'pending',?)""",
                        (path, kind, source, lrc, json.dumps(removed, ensure_ascii=False), json.dumps(flags, ensure_ascii=False), lines, matched, matched_length, base_hash, time.time()))
        self.db.commit()
        return "added"

    def pending_items(self):
        return self.db.execute("""select i.*,t.artist,t.title,t.album,t.length,t.state track_state from items i join tracks t on t.path=i.path
                                  where i.state='pending' order by i.kind,t.artist,t.album,t.path""").fetchall()

    def selected_items(self):
        return self.db.execute("select i.* from items i join tracks t on t.path=i.path where i.state='pending' and i.selected=1 order by i.path").fetchall()

    def set_selected(self, ids, value):
        self.db.executemany("update items set selected=? where id=?", [(int(value), i) for i in ids])
        self.db.commit()

    def set_item_state(self, item_id, state):
        self.db.execute("update items set state=? where id=?", (state, item_id))
        self.db.commit()

    def drop_pending(self, path, kind):
        self.db.execute("delete from items where path=? and kind=? and state='pending'", (path, kind))
