# github_star_ranking

GitHub のスター獲得ランキング（**週間 Top30 / 前日比 Top30**）を毎朝 06:47 JST に集計します。

- ダッシュボード: https://ma-garin.github.io/github_star_ranking/
- Markdown: `reports/YYYY-MM-DD.md`
- 生データ: `docs/data/YYYY-MM-DD.json`、スナップショット: `data/snapshots/`

## 仕組み

1. 候補収集: GitHub Trending（日次・週間、全言語＋主要6言語）と Search API（新規急伸・AI系トピック）
2. 候補の全リポジトリについてスター数をスナップショット（候補から外れても14日間は追跡）
3. 週間 = 7日前スナップショットとの差分、前日比 = 1日前との差分
   - 履歴が無い間は Trending の表示値で補完（†）、期間内に作られたリポジトリは累計をそのまま採用（★）

## Secrets（任意）

| 名前 | 用途 |
|---|---|
| `ANTHROPIC_API_KEY` | 概要の日本語要約（未設定なら英語の description を表示） |
| `SLACK_WEBHOOK_URL` | 週間Top30を Slack に投稿 |

## ローカル実行

```bash
GITHUB_TOKEN=xxx python scripts/collect.py
python -m pytest -q tests
```
