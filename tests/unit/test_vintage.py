"""The point-in-time data log (vintage.py): every value as first seen, every revision, nothing lost on a failed download."""
import sqlite3

import vintage
from pipeline import SCHEMA


def make_db(path, rows, ok=("A", "B"), failed=()):
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.executemany("INSERT INTO observations VALUES (?, ?, ?, 't')", rows)
    conn.executemany("INSERT INTO series_meta (series_id, status) VALUES (?, 'ok')", [(s,) for s in ok])
    conn.executemany("INSERT INTO series_meta (series_id, status) VALUES (?, 'failed')", [(s,) for s in failed])
    conn.commit()
    conn.close()
    return path


BASE = [("A", "2026-01", 1.0), ("A", "2026-02", 2.0), ("A", "2026-03", 3.0), ("B", "2026-01", 1.08534)]


def value(log, sid, period, day=None):
    k = vintage.known_on(log, day)
    hit = k[(k.series_id == sid) & (k.period == period)]
    return None if hit.empty else float(hit.value.iloc[0])


def test_first_copy_keeps_every_value_exactly(tmp_path):
    log = tmp_path / "data" / "vintages.csv"
    note = vintage.update(make_db(tmp_path / "1.db", BASE), log, day="2026-11-09")
    got = vintage.read_log(log)
    assert len(got) == 4 and set(got.as_of) == {"2026-11-09"}
    assert value(got, "B", "2026-01") == 1.08534
    assert "First copy: 4 values from 2 series" in note


def test_same_data_adds_nothing(tmp_path):
    log = tmp_path / "vintages.csv"
    vintage.update(make_db(tmp_path / "1.db", BASE), log, day="2026-11-09")
    before = log.read_text()
    note = vintage.update(make_db(tmp_path / "2.db", BASE), log, day="2026-11-10")
    assert log.read_text() == before
    assert "No value new or changed" in note


def test_revision_and_new_month_keep_the_first_release(tmp_path):
    log = tmp_path / "vintages.csv"
    vintage.update(make_db(tmp_path / "1.db", BASE), log, day="2026-11-09")
    later = [r for r in BASE if r[:2] != ("A", "2026-02")] + [("A", "2026-02", 2.5), ("A", "2026-04", 4.0)]
    note = vintage.update(make_db(tmp_path / "2.db", later), log, day="2026-11-10")
    got = vintage.read_log(log)
    assert len(got) == 6
    assert value(got, "A", "2026-02", "2026-11-09") == 2.0      # as first published
    assert value(got, "A", "2026-02") == 2.5                    # as revised
    assert value(got, "A", "2026-04", "2026-11-09") is None     # not out yet on the first day
    assert "Revised (1)" in note and "A 2026-02: 2 to 2.5 (+25.00%)" in note
    assert "New values: A 2026-04" in note


def test_failed_download_is_not_deleted_history(tmp_path):
    log = tmp_path / "vintages.csv"
    vintage.update(make_db(tmp_path / "1.db", BASE), log, day="2026-11-09")
    only_a = [r for r in BASE if r[0] == "A"]
    vintage.update(make_db(tmp_path / "2.db", only_a, ok=("A",), failed=("B",)), log, day="2026-11-10")
    assert value(vintage.read_log(log), "B", "2026-01") == 1.08534


def test_value_dropped_by_a_source_is_logged(tmp_path):
    log = tmp_path / "vintages.csv"
    vintage.update(make_db(tmp_path / "1.db", BASE), log, day="2026-11-09")
    without = [r for r in BASE if r[:2] != ("A", "2026-01")]
    note = vintage.update(make_db(tmp_path / "2.db", without), log, day="2026-11-10")
    got = vintage.read_log(log)
    assert value(got, "A", "2026-01") is None
    assert value(got, "A", "2026-01", "2026-11-09") == 1.0
    assert "Dropped by the source (1): A 2026-01" in note


def test_two_runs_on_one_day_keep_the_later_value(tmp_path):
    log = tmp_path / "vintages.csv"
    vintage.update(make_db(tmp_path / "1.db", BASE), log, day="2026-11-09")
    fixed = [r for r in BASE if r[:2] != ("A", "2026-03")] + [("A", "2026-03", 3.3)]
    vintage.update(make_db(tmp_path / "2.db", fixed), log, day="2026-11-09")
    assert value(vintage.read_log(log), "A", "2026-03") == 3.3


def test_a_failed_download_keeps_its_last_known_values(tmp_path):
    """10 Nov 2026: German gas prices failed to download and the page ran two days without the gas factor."""
    import pipeline
    log = tmp_path / "vintages.csv"
    vintage.update(make_db(tmp_path / "1.db", BASE), log, day="2026-11-09")
    revised = [("A", "2026-01", 1.0), ("A", "2026-02", 2.5), ("A", "2026-03", 3.0)]
    vintage.update(make_db(tmp_path / "2.db", revised, ok=("A",)), log, day="2026-11-10")
    db = tmp_path / "3.db"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    conn.executemany("INSERT INTO series_meta (series_id, status) VALUES (?, 'failed')", [("A",), ("C",)])
    kept = pipeline.keep_last_known(conn, ["A", "C"], log, "t3")
    rows = conn.execute("SELECT period, value FROM observations WHERE series_id = 'A' ORDER BY period").fetchall()
    meta = dict(conn.execute("SELECT series_id, status FROM series_meta").fetchall())
    conn.close()
    assert kept == ["A"] and rows == [("2026-01", 1.0), ("2026-02", 2.5), ("2026-03", 3.0)]   # the latest revision
    assert meta == {"A": "kept", "C": "failed"}                 # C was never seen, so there is nothing to keep
    assert vintage.read_db(db).empty                            # kept values are not logged again as new
