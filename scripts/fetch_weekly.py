#!/usr/bin/env python3
"""
Weekly AI News Fetcher
X APIv2で対象アカウントの先週ツイートを取得し、Claude APIで週刊記事を生成する
実行タイミング: 毎週月曜9時JST（GitHub Actions: cron '0 0 * * 1'）
"""

import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import anthropic
import tweepy

# ── 定数 ──────────────────────────────────────────────────────────────
JST = timezone(timedelta(hours=9))
NOW = datetime.now(JST)

# 月曜実行を前提に先週月曜〜日曜を算出
_days_since_monday = NOW.weekday()  # 月曜=0
LAST_MONDAY = (NOW - timedelta(days=7 + _days_since_monday)).date()
LAST_SUNDAY = (NOW - timedelta(days=1 + _days_since_monday)).date()

WEEK_DATE = LAST_MONDAY.strftime("%Y-%m-%d")
WEEK_LABEL = LAST_MONDAY.strftime("%Y年%m月%d日")

SCRIPT_DIR = Path(__file__).parent
ROOT_DIR = SCRIPT_DIR.parent
DOCS_DIR = ROOT_DIR / "docs"
RULES_DIR = SCRIPT_DIR / "rules"

X_ACCOUNTS = [
    # Claude関連
    "claudeai",
    # Google関連
    "NotebookLM",
    "GoogleLabs",
    "GoogleAI",
    # OpenAI
    "OpenAI",
    "sama",
    # その他
    "perplexity_ai",
    "genspark_japan",
    "n8n_io",
]


# ── ユーティリティ ─────────────────────────────────────────────────────
def _load_rules() -> str:
    writing_style = (RULES_DIR / "writing-style.md").read_text(encoding="utf-8")
    seo = (RULES_DIR / "seo.md").read_text(encoding="utf-8")
    return f"{writing_style}\n\n{seo}"


# ── X（Twitter）取得 ─────────────────────────────────────────────────────
def fetch_x_tweets() -> dict[str, list[dict]]:
    """X API v2でアカウントごとの先週ツイートを取得する。
    戻り値: {username: [{"text": ..., "url": ...}, ...]}
    """
    bearer_token = os.environ.get("X_BEARER_TOKEN", "")
    if not bearer_token:
        print("ERROR: X_BEARER_TOKEN is not set.", file=sys.stderr)
        sys.exit(1)

    client = tweepy.Client(bearer_token=bearer_token, wait_on_rate_limit=True)

    start_time = datetime(
        LAST_MONDAY.year, LAST_MONDAY.month, LAST_MONDAY.day,
        tzinfo=timezone.utc,
    )
    end_time = datetime(
        LAST_SUNDAY.year, LAST_SUNDAY.month, LAST_SUNDAY.day,
        23, 59, 59, tzinfo=timezone.utc,
    )

    results: dict[str, list[dict]] = {}

    for username in X_ACCOUNTS:
        print(f"Fetching @{username} ...")
        try:
            user_resp = client.get_user(username=username, user_fields=["id"])
            if not user_resp.data:
                print(f"  User not found: @{username}", file=sys.stderr)
                results[username] = []
                continue
            user_id = user_resp.data.id

            tweets_resp = client.get_users_tweets(
                id=user_id,
                start_time=start_time,
                end_time=end_time,
                max_results=20,
                tweet_fields=["created_at", "text"],
                exclude=["retweets", "replies"],
            )
            tweets = tweets_resp.data or []
            print(f"  -> {len(tweets)} tweets")

            results[username] = [
                {
                    "text": tw.text,
                    "url": f"https://x.com/{username}/status/{tw.id}",
                }
                for tw in tweets
            ]
        except tweepy.TweepyException as e:
            print(f"  ERROR @{username}: {e}", file=sys.stderr)
            results[username] = []

    return results


# ── 記事生成 ────────────────────────────────────────────────────────────
def generate_weekly_article(
    tweets_by_account: dict[str, list[dict]],
    rules_text: str,
) -> str:
    """Claude APIで週刊まとめMarkdown記事を生成する"""
    client = anthropic.Anthropic()

    # アカウントごとのツイートをテキスト化
    account_blocks = []
    for username in X_ACCOUNTS:
        tweets = tweets_by_account.get(username, [])
        if not tweets:
            account_blocks.append(f"### @{username}\n（今週の投稿なし）")
            continue
        lines = [f"### @{username}"]
        for tw in tweets:
            lines.append(f"- {tw['text'][:280]}\n  URL: {tw['url']}")
        account_blocks.append("\n".join(lines))

    raw_data = "\n\n".join(account_blocks)
    title = f"週刊AIニュース {WEEK_LABEL}週"
    date_from = LAST_MONDAY.strftime("%Y-%m-%d")
    date_to = LAST_SUNDAY.strftime("%Y-%m-%d")

    prompt = f"""以下のXツイートデータをもとに、日本語の週刊AIニュースまとめ記事をMarkdown形式で生成してください。

## ライティング・SEOルール
{rules_text}

## 週刊記事固有のルール
- タイトルは必ず「{title}」を使用する（SEOタイトル14字ルールの例外）
- 対象期間: {date_from} 〜 {date_to}
- アカウントごとにH2見出しでセクションを分ける（見出し20字以内厳守）
- 各ツイートには必ずツイートURLをMarkdownリンクで付ける
- リストやテーブルを適宜使って読みやすくする
- 全文ですます調厳守
- 文字数: 2000〜5000字
- 冒頭にその週の要約（3〜5文）を入れる
- 投稿がないアカウントは「今週の投稿はありませんでした」と記載する
- 英語ツイートは日本語に翻訳・要約する

## 収集データ（{date_from} 〜 {date_to}）
{raw_data}

## 出力形式
以下のMarkdown構造のみを出力してください（説明文・コードブロック記号は不要）：

---
description: （ディスクリプション：150字以内・結論ベース）
---

# {title}

> 対象期間: {date_from} 〜 {date_to}｜自動生成: {NOW.strftime('%Y-%m-%d %H:%M JST')}

（週の要約：3〜5文）

---

（アカウントごとのH2セクション、ツイート要約リスト、ツイートURLリンク）

---

[← 一覧に戻る](index.md)
"""

    print("Calling Claude API to generate weekly article...")
    with client.messages.stream(
        model="claude-opus-4-6",
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        response = stream.get_final_message()

    for block in response.content:
        if block.type == "text":
            return block.text
    return ""


# ── index.md 更新 ───────────────────────────────────────────────────────
def update_index() -> None:
    """docs/index.md のバックナンバー一覧を再生成する"""
    weeks = sorted(
        [p.stem for p in DOCS_DIR.glob("????-??-??.md")],
        reverse=True,
    )
    lines = [
        "# 週刊AIニュース",
        "",
        "毎週月曜9時(JST)に自動収集・生成されるAIニュース週刊まとめです。",
        "",
        "## バックナンバー",
        "",
    ]
    for w in weeks:
        try:
            dt = datetime.strptime(w, "%Y-%m-%d")
            label = f"{dt.strftime('%Y年%m月%d日')}週"
        except ValueError:
            label = w
        lines.append(f"- [{label}]({w}.md)")
    lines.append("")
    (DOCS_DIR / "index.md").write_text("\n".join(lines), encoding="utf-8")
    print("Updated docs/index.md")


# ── メイン ──────────────────────────────────────────────────────────────
def main() -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    rules_text = _load_rules()
    tweets_by_account = fetch_x_tweets()

    article = generate_weekly_article(
        tweets_by_account=tweets_by_account,
        rules_text=rules_text,
    )

    if not article:
        print("ERROR: 記事生成に失敗しました", file=sys.stderr)
        sys.exit(1)

    out_path = DOCS_DIR / f"{WEEK_DATE}.md"
    out_path.write_text(article, encoding="utf-8")
    print(f"Saved: {out_path}")

    update_index()


if __name__ == "__main__":
    main()
