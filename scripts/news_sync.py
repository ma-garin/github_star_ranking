"""AIニュース日報（定期タスク）が Google Drive の公開フォルダに置いた JSON を取り込む。

定期タスクの実行環境からはこのリポジトリへ push できないため、Drive を受け渡し場所にする。
フォルダ内の news-YYYY-MM-DD.json を docs/data/news/YYYY-MM-DD.json と latest.json に書き、app.json を作り直す。
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import collect  # noqa: E402

FOLDER_ID = os.environ.get("NEWS_FOLDER_ID", "1CtMA1774Yn-YAxyqUpm4VHaT6rWlols_")
NEWS_DIR = collect.DOCS_DATA / "news"
REQUIRED = ("date", "weekday", "period", "memo", "failures", "items")
ENTRY = re.compile(r'id="entry-([\w-]+)".*?class="flip-entry-title">([^<]+)<', re.S)


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "github-star-ranking-news-sync"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def list_folder(page: str) -> dict[str, str]:
    """embeddedfolderview の HTML から {ファイル名: ファイルID}。同名は後勝ち。"""
    return {html.unescape(name).strip(): fid for fid, name in ENTRY.findall(page)}


def validate(d: dict, date: str) -> None:
    missing = [k for k in REQUIRED if k not in d]
    if missing or d["date"] != date or not isinstance(d["items"], list):
        raise ValueError(f"{date}: 形式不正 missing={missing}")
    for n in d["items"]:
        for k in ("no", "category", "company", "title", "summary", "sources"):
            if k not in n:
                raise ValueError(f"{date}: item に {k} が無い")


def sync(files: dict[str, str], get=fetch) -> list[str]:
    changed = []
    for name, fid in sorted(files.items()):
        m = re.fullmatch(r"news-(\d{4}-\d{2}-\d{2})\.json", name)
        if not m:
            continue
        date = m.group(1)
        d = json.loads(get(f"https://drive.google.com/uc?export=download&id={fid}").decode("utf-8"))
        validate(d, date)
        out = NEWS_DIR / f"{date}.json"
        text = json.dumps(d, ensure_ascii=False, indent=1) + "\n"
        if not out.exists() or out.read_text(encoding="utf-8") != text:
            out.write_text(text, encoding="utf-8")
            changed.append(date)
    if changed:
        newest = sorted(p.stem for p in NEWS_DIR.glob("????-??-??.json"))[-1]
        (NEWS_DIR / "latest.json").write_text((NEWS_DIR / f"{newest}.json").read_text(encoding="utf-8"), encoding="utf-8")
    return changed


def main() -> int:
    page = fetch(f"https://drive.google.com/embeddedfolderview?id={FOLDER_ID}").decode("utf-8", "replace")
    files = list_folder(page)
    print(f"Drive: {len(files)} files")
    changed = sync(files)
    print("changed:", changed or "なし")
    if changed:
        collect.build_app_data()
    return 0


if __name__ == "__main__":
    sys.exit(main())
