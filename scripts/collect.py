#!/usr/bin/env python3
"""GitHub スター獲得ランキング（週間Top30 / 前日比Top30）を集計する。

標準ライブラリのみで動く。環境変数:
  GITHUB_TOKEN        必須（Actions では自動付与）
  ANTHROPIC_API_KEY   任意。設定時のみ日本語要約を生成
  SLACK_WEBHOOK_URL   任意。設定時のみ Slack に投稿
  TOP_N               任意。既定 30
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SNAP_DIR = ROOT / "data" / "snapshots"
POOL_FILE = ROOT / "data" / "pool.json"
SUMMARY_FILE = ROOT / "data" / "summaries.json"
REPORT_DIR = ROOT / "reports"
DOCS_DATA = ROOT / "docs" / "data"

JST = dt.timezone(dt.timedelta(hours=9))
TOP_N = int(os.environ.get("TOP_N", "30"))
POOL_TTL_DAYS = 14  # 候補に出なくなってからも追跡を続ける日数（7日差分を取るため）
TRENDING_LANGS = ["", "python", "typescript", "javascript", "go", "rust", "shell"]
SEARCH_QUERIES = [
    "created:>{d30} stars:>300",
    "created:>{d180} stars:>3000",
    "pushed:>{d7} stars:>20000",
    "topic:claude-code pushed:>{d7} stars:>500",
    "topic:agent-skills pushed:>{d7} stars:>300",
    "topic:mcp pushed:>{d7} stars:>1000",
    "topic:ai-agents pushed:>{d7} stars:>2000",
    "topic:llm pushed:>{d7} stars:>5000",
]
UA = "github-star-ranking (+https://github.com/ma-garin/github_star_ranking)"


# ---------------------------------------------------------------- HTTP
def _get(url: str, *, api: bool = True, timeout: int = 30) -> bytes:
    headers = {"User-Agent": UA}
    if api:
        headers["Accept"] = "application/vnd.github+json"
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _post_json(url: str, payload: dict, headers: dict | None = None, timeout: int = 120) -> bytes:
    h = {"Content-Type": "application/json", "User-Agent": UA}
    h.update(headers or {})
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# ---------------------------------------------------------------- 候補収集
TRENDING_ROW = re.compile(r'<article class="Box-row">(.*?)</article>', re.S)
TRENDING_NAME = re.compile(r'<h2[^>]*>\s*<a[^>]*href="/([^"/]+/[^"/]+)"', re.S)
TRENDING_GAIN = re.compile(r'([\d,]+)\s+stars?\s+(?:today|this week)')


def parse_trending(html: str) -> dict[str, int]:
    """Trending ページの HTML から {full_name: 期間内獲得スター} を返す。"""
    out: dict[str, int] = {}
    for row in TRENDING_ROW.findall(html):
        m = TRENDING_NAME.search(row)
        if not m:
            continue
        g = TRENDING_GAIN.search(row)
        out[m.group(1)] = int(g.group(1).replace(",", "")) if g else 0
    return out


def fetch_trending() -> tuple[dict[str, int], dict[str, int]]:
    daily: dict[str, int] = {}
    weekly: dict[str, int] = {}
    for since, bucket in (("daily", daily), ("weekly", weekly)):
        for lang in TRENDING_LANGS:
            url = f"https://github.com/trending/{lang}?since={since}".replace("//?", "/?")
            try:
                got = parse_trending(_get(url, api=False).decode("utf-8", "replace"))
            except Exception as e:  # noqa: BLE001 — Trending は補助データ源
                print(f"[warn] trending {since}/{lang or 'all'}: {e}", file=sys.stderr)
                continue
            for k, v in got.items():
                bucket[k] = max(bucket.get(k, 0), v)
    return daily, weekly


def fetch_search(today: dt.date) -> set[str]:
    fmt = {f"d{n}": (today - dt.timedelta(days=n)).isoformat() for n in (7, 30, 180)}
    names: set[str] = set()
    for q in SEARCH_QUERIES:
        qs = urllib.parse.urlencode({"q": q.format(**fmt), "sort": "stars", "order": "desc", "per_page": 100})
        try:
            data = json.loads(_get(f"https://api.github.com/search/repositories?{qs}"))
        except Exception as e:  # noqa: BLE001
            print(f"[warn] search '{q}': {e}", file=sys.stderr)
            continue
        names.update(item["full_name"] for item in data.get("items", []))
    return names


def fetch_repo(full_name: str) -> dict | None:
    try:
        d = json.loads(_get(f"https://api.github.com/repos/{full_name}"))
    except urllib.error.HTTPError as e:
        if e.code not in (404, 451):
            print(f"[warn] repo {full_name}: {e}", file=sys.stderr)
        return None
    except Exception as e:  # noqa: BLE001
        print(f"[warn] repo {full_name}: {e}", file=sys.stderr)
        return None
    return {
        "full_name": d["full_name"],
        "stars": d["stargazers_count"],
        "description": d.get("description") or "",
        "language": d.get("language") or "",
        "created_at": d.get("created_at", ""),
        "url": d["html_url"],
    }


# ---------------------------------------------------------------- 集計（純粋関数）
def compute_delta(name: str, stars: int, created: str, today: dt.date, days: int,
                  past: dict[str, int] | None, trending: dict[str, int]) -> tuple[int, str] | None:
    """獲得スターと算出根拠を返す。根拠: snapshot / trending / new。算出不能なら None。"""
    if past is not None and name in past:
        return stars - past[name], "snapshot"
    if name in trending:
        return trending[name], "trending"
    if created:
        c = dt.datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone(JST).date()
        if (today - c).days < days:
            return stars, "new"
    return None


def rank(repos: list[dict], today: dt.date, days: int, past: dict[str, int] | None,
         trending: dict[str, int], top_n: int) -> list[dict]:
    rows = []
    for r in repos:
        got = compute_delta(r["full_name"], r["stars"], r["created_at"], today, days, past, trending)
        if got is None or got[0] <= 0:
            continue
        rows.append({**r, "delta": got[0], "basis": got[1]})
    rows.sort(key=lambda x: (-x["delta"], x["full_name"]))
    for i, row in enumerate(rows[:top_n], 1):
        row["rank"] = i
    return rows[:top_n]


# ---------------------------------------------------------------- 永続化
def load_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True) + "\n")


def load_snapshot(day: dt.date) -> dict[str, int] | None:
    snap = load_json(SNAP_DIR / f"{day.isoformat()}.json", None)
    return snap


# ---------------------------------------------------------------- 要約（任意）
def summarize(rows: list[dict]) -> None:
    cache: dict[str, str] = load_json(SUMMARY_FILE, {})
    key = os.environ.get("ANTHROPIC_API_KEY")
    todo = [r for r in rows if r["full_name"] not in cache and r["description"]]
    if key and todo:
        lines = "\n".join(f"{r['full_name']}\t{r['description']}" for r in todo)
        prompt = ("次のGitHubリポジトリ説明を、それぞれ日本語40字以内で要約してください。"
                  "出力は {\"owner/repo\": \"要約\"} のJSONのみ。\n\n" + lines)
        try:
            res = json.loads(_post_json(
                "https://api.anthropic.com/v1/messages",
                {"model": os.environ.get("SUMMARY_MODEL", "claude-haiku-5-5"), "max_tokens": 4000,
                 "messages": [{"role": "user", "content": prompt}]},
                {"x-api-key": key, "anthropic-version": "2023-06-01"}))
            text = "".join(b.get("text", "") for b in res.get("content", []))
            cache.update(json.loads(text[text.find("{"): text.rfind("}") + 1]))
            save_json(SUMMARY_FILE, cache)
        except Exception as e:  # noqa: BLE001 — 要約失敗でもランキングは出す
            print(f"[warn] summarize: {e}", file=sys.stderr)
    for r in rows:
        r["summary_ja"] = cache.get(r["full_name"], "")


# ---------------------------------------------------------------- 出力
BASIS_MARK = {"snapshot": "", "trending": "†", "new": "★"}


def render_markdown(day: dt.date, weekly: list[dict], daily: list[dict]) -> str:
    def table(rows: list[dict], unit: str) -> str:
        out = [f"| # | リポジトリ | +{unit} | 累計 | 言語 | 概要 |", "|---:|---|---:|---:|---|---|"]
        for r in rows:
            desc = (r.get("summary_ja") or r["description"]).replace("|", "\\|").replace("\n", " ")[:120]
            out.append(f"| {r['rank']} | [{r['full_name']}]({r['url']}) | {r['delta']:,}{BASIS_MARK[r['basis']]} "
                       f"| {r['stars']:,} | {r['language']} | {desc} |")
        return "\n".join(out)

    return (f"# GitHub スター獲得ランキング {day.isoformat()}\n\n"
            f"## 週間 Top{len(weekly)}\n\n{table(weekly, '週')}\n\n"
            f"## 前日比 Top{len(daily)}\n\n{table(daily, '日')}\n\n"
            "† Trending の表示値（スナップショット履歴不足時の補完）　★ 期間内に新規作成\n")


def post_slack(day: dt.date, weekly: list[dict]) -> None:
    hook = os.environ.get("SLACK_WEBHOOK_URL")
    if not hook:
        return
    lines = [f"*GitHub 週間スター獲得 Top{len(weekly)}*（{day.isoformat()}）"]
    for r in weekly:
        lines.append(f"{r['rank']}. <{r['url']}|{r['full_name']}>  +{r['delta']:,}/週 ・累計 {r['stars']:,}")
    pages = os.environ.get("PAGES_URL")
    if pages:
        lines.append(f"<{pages}|ダッシュボード>")
    try:
        _post_json(hook, {"text": "\n".join(lines)})
    except Exception as e:  # noqa: BLE001
        print(f"[warn] slack: {e}", file=sys.stderr)


# ---------------------------------------------------------------- main
def main() -> int:
    today = dt.datetime.now(JST).date()
    if os.environ.get("RUN_DATE"):
        today = dt.date.fromisoformat(os.environ["RUN_DATE"])

    tr_daily, tr_weekly = fetch_trending()
    searched = fetch_search(today)

    pool: dict[str, str] = load_json(POOL_FILE, {})  # full_name -> 最後に候補に出た日
    for name in set(tr_daily) | set(tr_weekly) | searched:
        pool[name] = today.isoformat()
    cutoff = (today - dt.timedelta(days=POOL_TTL_DAYS)).isoformat()
    pool = {k: v for k, v in pool.items() if v >= cutoff}

    with ThreadPoolExecutor(max_workers=8) as ex:
        repos = [r for r in ex.map(fetch_repo, sorted(pool)) if r]
    if not repos:
        print("[error] リポジトリ情報を1件も取得できませんでした", file=sys.stderr)
        return 1
    # リネームされたリポジトリは新しい名前で追跡する
    pool = {r["full_name"]: pool.get(r["full_name"], today.isoformat()) for r in repos}

    snapshot = {r["full_name"]: r["stars"] for r in repos}
    save_json(SNAP_DIR / f"{today.isoformat()}.json", snapshot)
    save_json(POOL_FILE, pool)

    weekly = rank(repos, today, 7, load_snapshot(today - dt.timedelta(days=7)), tr_weekly, TOP_N)
    daily = rank(repos, today, 1, load_snapshot(today - dt.timedelta(days=1)), tr_daily, TOP_N)
    summarize(weekly + [r for r in daily if r["full_name"] not in {w["full_name"] for w in weekly}])

    result = {"date": today.isoformat(), "generated_at": dt.datetime.now(JST).isoformat(timespec="seconds"),
              "pool_size": len(repos), "weekly": weekly, "daily": daily}
    REPORT_DIR.mkdir(exist_ok=True)
    (REPORT_DIR / f"{today.isoformat()}.md").write_text(render_markdown(today, weekly, daily))
    save_json(DOCS_DATA / f"{today.isoformat()}.json", result)
    save_json(DOCS_DATA / "latest.json", result)
    dates = sorted({p.stem for p in DOCS_DATA.glob("????-??-??.json")}, reverse=True)
    save_json(DOCS_DATA / "index.json", dates)

    post_slack(today, weekly)
    print(f"pool={len(repos)} weekly={len(weekly)} daily={len(daily)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
