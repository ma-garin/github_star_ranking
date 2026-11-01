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


# ---------------------------------------------------------------- 日本語化
GLOSSARY = [("クロード コード", "Claude Code"), ("クロードコード", "Claude Code"), ("クロード", "Claude"), ("コーデックス", "Codex"),
            ("カーソル", "Cursor"), ("オープンコード", "OpenCode"), ("ジェミニ", "Gemini"), ("イレブンラボ", "ElevenLabs"),
            ("ハーネス", "ハーネス"), ("ギットハブ", "GitHub"), ("オープンAI", "OpenAI"), ("アンスロピック", "Anthropic")]
JA = __import__("re").compile(r"[ぁ-んァ-ヶ一-龠]")


def fix_terms(t: str) -> str:
    for a, b in GLOSSARY:
        t = t.replace(a, b)
    return t


def is_japanese(t: str) -> bool:
    return bool(t) and len(JA.findall(t)) >= max(2, len(t) // 10)


def translate_ja(text: str) -> str:
    """Google翻訳の公開エンドポイントで英語→日本語。失敗時は空文字。"""
    qs = urllib.parse.urlencode({"client": "gtx", "sl": "auto", "tl": "ja", "dt": "t", "q": text[:500]})
    try:
        data = json.loads(_get(f"https://translate.googleapis.com/translate_a/single?{qs}", api=False))
        return fix_terms("".join(seg[0] for seg in data[0] if seg and seg[0]).strip())
    except Exception as e:  # noqa: BLE001
        print(f"[warn] translate: {e}", file=sys.stderr)
        return ""


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
    todo = [r for r in rows if r["full_name"] not in cache and r["description"]]
    if todo:
        with ThreadPoolExecutor(max_workers=4) as ex:
            for r, ja in zip(todo, ex.map(lambda x: translate_ja(x["description"]), todo)):
                if ja:
                    cache[r["full_name"]] = ja
        save_json(SUMMARY_FILE, cache)
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


def ranking_lines(rows: list[dict], *, links: bool) -> list[str]:
    """1件2行（番号・名前・+週 / 概要）。links=True で Slack mrkdwn リンク。"""
    out = []
    for r in rows:
        name = f"<{r['url']}|{r['full_name']}>" if links else r["full_name"]
        out.append(f"{r['rank']}. {name}　+{r['delta']:,}/週")
        desc = (r.get("summary_ja") or r["description"]).replace("\n", " ").strip()
        if desc:
            out.append(f"　 {desc[:80]}")
    return out


def post_slack(day: dt.date, weekly: list[dict]) -> None:
    hook = os.environ.get("SLACK_WEBHOOK_URL")
    if not hook:
        return
    lines = [f"*GitHub 週間スター獲得 Top{len(weekly)}*（{day.isoformat()}）"]
    lines += ranking_lines(weekly, links=True)
    pages = os.environ.get("PAGES_URL")
    if pages:
        lines.append(f"<{pages}|ダッシュボード>")
    try:
        _post_json(hook, {"text": "\n".join(lines)})
    except Exception as e:  # noqa: BLE001
        print(f"[warn] slack: {e}", file=sys.stderr)



# ---------------------------------------------------------------- RSS（Slack RSSアプリ購読用）
FEED_URL = "https://raw.githubusercontent.com/ma-garin/github_star_ranking/main/docs/feed.xml"


def _x(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def write_feed(days: int = 14) -> None:
    items = []
    for p in sorted(DOCS_DATA.glob("????-??-??.json"), reverse=True)[:days]:
        d = load_json(p, None)
        if not d:
            continue
        lines = ranking_lines(d["weekly"], links=False)
        report = f"https://github.com/ma-garin/github_star_ranking/blob/main/reports/{d['date']}.md"
        items.append(
            f"<item><title>{_x('GitHub週間スター獲得Top30 ' + d['date'])}</title>"
            f"<link>{report}</link><guid isPermaLink=\"false\">star-ranking-{d['date']}</guid>"
            f"<pubDate>{dt.datetime.fromisoformat(d['generated_at']).strftime('%a, %d %b %Y %H:%M:%S %z')}</pubDate>"
            f"<description>{_x(chr(10).join(lines))}</description></item>")
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel>'
           "<title>GitHub Star Ranking</title>"
           "<link>https://github.com/ma-garin/github_star_ranking</link>"
           "<description>GitHubスター獲得ランキング（週間Top30）</description>"
           + "".join(items) + "</channel></rss>\n")
    (ROOT / "docs" / "feed.xml").write_text(xml)


# ---------------------------------------------------------------- アプリ用データ
def build_app_data(days: int = 14) -> None:
    """PWA 用に、日別順位・スター推移・AIニュースを1ファイルにまとめる（docs/data/app.json）。"""
    dates = sorted(p.stem for p in DOCS_DATA.glob("????-??-??.json"))[-days:]
    keep = ("rank", "full_name", "delta", "stars", "basis", "language", "url", "description", "summary_ja")
    ranks, dranks = {}, {}
    for d in dates:
        j = load_json(DOCS_DATA / f"{d}.json", {})
        ranks[d] = [{k: r.get(k) for k in keep} for r in j.get("weekly", [])]
        dranks[d] = [{k: r.get(k) for k in keep} for r in j.get("daily", [])]
    cache: dict[str, str] = load_json(SUMMARY_FILE, {})
    todo = {r["full_name"]: r["description"] for v in list(ranks.values()) + list(dranks.values()) for r in v
            if r.get("description") and not is_japanese(cache.get(r["full_name"], "")) and not is_japanese(r["description"])}
    if todo:
        with ThreadPoolExecutor(max_workers=4) as ex:
            for n, ja in zip(todo, ex.map(translate_ja, todo.values())):
                if ja:
                    cache[n] = ja
        save_json(SUMMARY_FILE, cache)
    cache = {n: fix_terms(t) for n, t in cache.items()}
    for v in list(ranks.values()) + list(dranks.values()):
        for r in v:
            d = r.get("description") or ""
            r["summary_ja"] = cache.get(r["full_name"]) or (d if is_japanese(d) else "") or ("説明文がありません" if not d else "")
            r.pop("description", None) if r["summary_ja"] else None
    names = {r["full_name"] for v in list(ranks.values()) + list(dranks.values()) for r in v}
    snaps = {d: load_json(SNAP_DIR / f"{d}.json", {}) for d in dates}
    series = {n: [snaps[d].get(n) for d in dates] for n in sorted(names)}
    news = []
    for p in sorted((DOCS_DATA / "news").glob("????-??-??.json"))[-7:]:
        n = load_json(p, None)
        if n:
            news.append(n)
    latest = load_json(DOCS_DATA / "latest.json", {})
    save_json(DOCS_DATA / "app.json", {"dates": dates, "ranks": ranks, "daily_ranks": dranks, "series": series,
                                       "news": news, "pool_size": latest.get("pool_size"),
                                       "generated_at": latest.get("generated_at")})

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

    write_feed()
    build_app_data()
    post_slack(today, weekly)
    print(f"pool={len(repos)} weekly={len(weekly)} daily={len(daily)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
