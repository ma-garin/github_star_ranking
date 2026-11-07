"""AIニュース日報（定期タスク）が Slack #ai-news のスレッドに返信したアプリ用 JSON を取り込む。

定期タスクは本投稿のスレッドに `APP-JSON news-YYYY-MM-DD k/N` + ```json コードブロック``` を N 件返信する。
1..N を結合して JSON を復元し、docs/data/news/YYYY-MM-DD.json と latest.json に書き、app.json を作り直す。
必要な環境変数: SLACK_BOT_TOKEN（スコープ channels:history, channels:join）。トークンはログに出さない。
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import collect  # noqa: E402

CHANNEL = os.environ.get("NEWS_CHANNEL_ID", "C0C4L66LLMS")
NEWS_DIR = collect.DOCS_DATA / "news"
LOOKBACK_DAYS = 3
REQUIRED = ("date", "weekday", "period", "memo", "failures", "items")
ITEM_REQUIRED = ("no", "category", "company", "title", "summary", "sources")
HEAD = re.compile(r"APP-JSON\s+news-(\d{4}-\d{2}-\d{2})\s+(\d+)\s*/\s*(\d+)")
BLOCK = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.S)


def slack(method: str, token: str, **params) -> dict:
    url = f"https://slack.com/api/{method}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, method="POST" if method == "conversations.join" else "GET",
                                 headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.loads(r.read().decode("utf-8"))
    if not d.get("ok"):
        raise RuntimeError(f"{method}: {d.get('error')}")
    return d


def parts_from(messages: list[dict]) -> dict[str, dict[int, tuple[int, str]]]:
    """返信群から {日付: {k: (N, 部分文字列)}}。同じ k は後勝ち。"""
    out: dict[str, dict[int, tuple[int, str]]] = {}
    for m in messages:
        text = m.get("text") or ""
        h, b = HEAD.search(text), BLOCK.search(text)
        if not h or not b:
            continue
        date, k, n = h.group(1), int(h.group(2)), int(h.group(3))
        out.setdefault(date, {})[k] = (n, html.unescape(b.group(1)).strip())
    return out


def assemble(parts: dict[int, tuple[int, str]]) -> str | None:
    """1..N が揃っていれば結合した文字列。欠けていれば None。"""
    if not parts:
        return None
    n = max(v[0] for v in parts.values())
    if any(k not in parts for k in range(1, n + 1)):
        return None
    return "".join(parts[k][1] for k in range(1, n + 1))


def validate(d: dict, date: str) -> None:
    missing = [k for k in REQUIRED if k not in d]
    if missing or d["date"] != date or not isinstance(d["items"], list):
        raise ValueError(f"{date}: 形式不正 missing={missing}")
    for n in d["items"]:
        for k in ITEM_REQUIRED:
            if k not in n:
                raise ValueError(f"{date}: item に {k} が無い")


def write(found: dict[str, dict]) -> list[str]:
    changed = []
    for date, d in sorted(found.items()):
        out = NEWS_DIR / f"{date}.json"
        text = json.dumps(d, ensure_ascii=False, indent=1) + "\n"
        if not out.exists() or out.read_text(encoding="utf-8") != text:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text, encoding="utf-8")
            changed.append(date)
    if changed:
        newest = sorted(p.stem for p in NEWS_DIR.glob("????-??-??.json"))[-1]
        (NEWS_DIR / "latest.json").write_text((NEWS_DIR / f"{newest}.json").read_text(encoding="utf-8"), encoding="utf-8")
    return changed


def collect_threads(token: str, api=slack) -> list[dict]:
    try:
        api("conversations.join", token, channel=CHANNEL)
    except RuntimeError as e:
        print(f"[warn] {e}")
    hist = api("conversations.history", token, channel=CHANNEL, oldest=f"{time.time() - LOOKBACK_DAYS * 86400:.0f}", limit=100)
    replies = []
    for m in hist.get("messages", []):
        if m.get("reply_count"):
            replies += api("conversations.replies", token, channel=CHANNEL, ts=m["ts"], limit=200).get("messages", [])[1:]
    return replies


def sync(replies: list[dict]) -> tuple[list[str], list[str]]:
    """(書き換えた日付, 形式不正の一覧)。分割が揃っていない日は投稿途中とみなし、警告だけ出して飛ばす。"""
    found, errors = {}, []
    for date, parts in parts_from(replies).items():
        s = assemble(parts)
        if s is None:
            print(f"[warn] {date}: 分割が揃っていない（{sorted(parts)}）")
            continue
        try:
            d = json.loads(s)
            validate(d, date)
            found[date] = d
        except ValueError as e:
            errors.append(f"{date}: {e}")
    return write(found), errors


def main() -> int:
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not token:
        print("SLACK_BOT_TOKEN が未設定")
        return 1
    replies = collect_threads(token)
    changed, errors = sync(replies)
    for e in errors:
        print(f"[error] {e}")
    print("changed:", changed or "なし")
    if changed:
        collect.build_app_data()
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
