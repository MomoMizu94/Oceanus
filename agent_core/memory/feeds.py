"""Remember entries returned by successful feed reads across Oceanus sessions."""

import sqlite3
from contextlib import closing
from hashlib import sha256
from pathlib import Path
from urllib.parse import urldefrag


def record_feed_read(url, entry_keys, lines, check_cancel):
    """Atomically record this read and return the baseline flag and unseen lines."""
    directory = Path.home() / ".local/share/oceanus/feed-history"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    feed_key = sha256(urldefrag(url).url.encode()).hexdigest()
    unseen = []
    with closing(sqlite3.connect(directory / "seen.sqlite3")) as db, db:
        # Concurrent readers must agree which one first returned an entry
        db.execute("BEGIN IMMEDIATE")
        db.execute("CREATE TABLE IF NOT EXISTS feeds (feed TEXT PRIMARY KEY)")
        db.execute("CREATE TABLE IF NOT EXISTS entries "
                   "(feed TEXT, item TEXT, PRIMARY KEY (feed, item)) WITHOUT ROWID")
        first_check = db.execute("INSERT OR IGNORE INTO feeds VALUES (?)", (feed_key,)).rowcount == 1
        for key, line in zip(entry_keys, lines, strict=True):
            inserted = db.execute("INSERT OR IGNORE INTO entries VALUES (?, ?)", (feed_key, key))
            if inserted.rowcount:
                unseen.append(line)
        check_cancel()
    return first_check, unseen
