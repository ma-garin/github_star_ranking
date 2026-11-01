import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import collect  # noqa: E402

TODAY = dt.date(2026, 10, 31)


def repo(name, stars, created="2020-01-01T00:00:00Z"):
    return {"full_name": name, "stars": stars, "created_at": created, "description": "", "language": "", "url": ""}


def test_snapshot_diff_wins_over_trending():
    assert collect.compute_delta("a/b", 150, "2020-01-01T00:00:00Z", TODAY, 7, {"a/b": 100}, {"a/b": 999}) == (50, "snapshot")


def test_trending_fallback_when_no_history():
    assert collect.compute_delta("a/b", 150, "2020-01-01T00:00:00Z", TODAY, 7, None, {"a/b": 80}) == (80, "trending")


def test_new_repo_counts_all_stars():
    assert collect.compute_delta("a/b", 150, "2026-10-28T00:00:00Z", TODAY, 7, {}, {}) == (150, "new")


def test_old_repo_without_data_is_excluded():
    assert collect.compute_delta("a/b", 150, "2020-01-01T00:00:00Z", TODAY, 7, {}, {}) is None


def test_rank_orders_limits_and_drops_non_positive():
    repos = [repo(f"o/r{i}", 1000 + i * 10) for i in range(40)] + [repo("o/flat", 500)]
    past = {f"o/r{i}": 1000 for i in range(40)} | {"o/flat": 500}
    rows = collect.rank(repos, TODAY, 7, past, {}, 30)
    assert len(rows) == 30
    assert rows[0]["full_name"] == "o/r39" and rows[0]["rank"] == 1 and rows[0]["delta"] == 390
    assert all(r["full_name"] != "o/flat" for r in rows)


def test_parse_trending():
    html = '''<article class="Box-row"><h2 class="h3"><a href="/foo/bar" class="Link">x</a></h2>
    <span class="d-inline-block float-sm-right">1,234 stars this week</span></article>
    <article class="Box-row"><h2><a href="/baz/qux">y</a></h2><span>56 stars today</span></article>'''
    assert collect.parse_trending(html) == {"foo/bar": 1234, "baz/qux": 56}


def test_markdown_has_both_sections():
    rows = [{**repo("o/r", 10), "delta": 5, "basis": "trending", "rank": 1, "summary_ja": "要約"}]
    md = collect.render_markdown(TODAY, rows, rows)
    assert "週間 Top1" in md and "前日比 Top1" in md and "5†" in md and "要約" in md


def test_write_feed(tmp_path, monkeypatch):
    import json, xml.etree.ElementTree as ET
    data = tmp_path / "docs" / "data"; data.mkdir(parents=True)
    rows = [{**repo("o/r&x", 10), "delta": 5, "basis": "snapshot", "rank": 1}]
    (data / "2026-10-31.json").write_text(json.dumps({"date": "2026-10-31", "generated_at": "2026-10-31T06:50:00+09:00", "weekly": rows}))
    monkeypatch.setattr(collect, "ROOT", tmp_path); monkeypatch.setattr(collect, "DOCS_DATA", data)
    collect.write_feed()
    item = ET.parse(tmp_path / "docs" / "feed.xml").find("channel/item")
    assert item.find("title").text.endswith("2026-10-31") and "o/r&x　+5/週" in item.find("description").text


def test_ranking_lines_two_rows_per_repo():
    rows = [{**repo("o/r", 10), "url": "https://g/o/r", "delta": 1234, "rank": 1, "description": "A tool", "summary_ja": "ツール"}]
    assert collect.ranking_lines(rows, links=True) == ["1. <https://g/o/r|o/r>　+1,234/週", "　 ツール"]
    assert collect.ranking_lines([{**rows[0], "summary_ja": ""}], links=False)[1] == "　 A tool"


def test_build_app_data(tmp_path, monkeypatch):
    import json
    data = tmp_path / "docs" / "data"; (data / "news").mkdir(parents=True); snap = tmp_path / "snap"; snap.mkdir()
    for d, st in (("2026-10-31", 100), ("2026-11-01", 130)):
        (data / f"{d}.json").write_text(json.dumps({"weekly": [{**repo("o/r", st), "rank": 1, "delta": 5}], "daily": []}))
        (snap / f"{d}.json").write_text(json.dumps({"o/r": st}))
    (data / "news" / "2026-11-01.json").write_text(json.dumps({"date": "2026-11-01", "items": []}))
    (data / "latest.json").write_text(json.dumps({"pool_size": 3}))
    monkeypatch.setattr(collect, "DOCS_DATA", data); monkeypatch.setattr(collect, "SNAP_DIR", snap)
    collect.build_app_data()
    a = json.loads((data / "app.json").read_text())
    assert a["dates"] == ["2026-10-31", "2026-11-01"] and a["series"]["o/r"] == [100, 130] and len(a["news"]) == 1
