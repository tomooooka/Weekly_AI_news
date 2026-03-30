#!/usr/bin/env python3
"""
Weekly AI News Fetcher
各サイトを巡回して先週のAIニュースを取得し、Claude APIで週刊記事を生成する
実行タイミング: 毎週月曜9時JST（GitHub Actions: cron '0 0 * * 1'）
"""

import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urljoin

import anthropic
import requests
import tweepy
from bs4 import BeautifulSoup

# ── 定数 ──────────────────────────────────────────────────────────────
JST = timezone(timedelta(hours=9))
NOW = datetime.now(JST)

# 月曜実行を前提に先週月曜〜日曜を算出
_days_since_monday = NOW.weekday()  # 月曜=0
LAST_MONDAY = (NOW - timedelta(days=7 + _days_since_monday)).date()
LAST_SUNDAY = (NOW - timedelta(days=1 + _days_since_monday)).date()

WEEK_DATE = LAST_MONDAY.strftime("%Y-%m-%d")   # ファイル名用
WEEK_LABEL = LAST_MONDAY.strftime("%Y年%m月%d日")  # 記事タイトル用

SCRIPT_DIR = Path(__file__).parent
ROOT_DIR = SCRIPT_DIR.parent
DOCS_DIR = ROOT_DIR / "docs"
SITES_JSON = SCRIPT_DIR / "sites.json"
RULES_DIR = SCRIPT_DIR / "rules"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; WeeklyAINewsBot/1.0; "
        "+https://github.com/tomooooka/Weekly_AI_news)"
    )
}

# X（Twitter）取得対象アカウント（@なし）
X_ACCOUNTS = [
    "claudeai",
    "NotebookLM",
    "GoogleLabs",
    "GoogleAI",
    "OpenAI",
    "sama",
    "perplexity_ai",
    "genspark_japan",
    "n8n_io",
]


# ── ユーティリティ ─────────────────────────────────────────────────────
def _load_rules() -> str:
    writing_style = (RULES_DIR / "writing-style.md").read_text(encoding="utf-8")
    seo = (RULES_DIR / "seo.md").read_text(encoding="utf-8")
    return f"{writing_style}\n\n{seo}"


# ── スクレイピング ─────────────────────────────────────────────────────
def fetch_site(site: dict) -> list[dict]:
    """1サイトを取得してエントリリスト（text, link）を返す"""
    name = site["name"]
    url = site["url"]
    selector = site["selector"]

    print(f"Fetching: {name} ({url})")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  ERROR: {e}", file=sys.stderr)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    entries = []
    seen_texts: set[str] = set()
    seen_links: set[str] = set()

    for tag in soup.select(selector):
        text = re.sub(r"\s+", " ", tag.get_text(separator=" ", strip=True))
        if not text or len(text) < 10:
            continue
        if text in seen_texts:
            continue
        seen_texts.add(text)

        # リンク抽出（自サイト内リンク優先）
        link = ""
        a = tag.find("a", href=True)
        if not a and tag.name == "a":
            a = tag
        if a:
            href = a.get("href", "")
            link = href if href.startswith("http") else urljoin(url, href)
        if link and link in seen_links:
            link = ""
        if link:
            seen_links.add(link)

        entries.append({"text": text[:400], "link": link})
        if len(entries) >= 15:
            break

    print(f"  -> {len(entries)} entries")
    return entries


# ── X（Twitter）取得 ─────────────────────────────────────────────────────
def fetch_x_tweets() -> list[dict]:
    """X API v2でアカウントごとの先週ツイートを取得する。
    X_BEARER_TOKEN が未設定の場合はスキップして空リストを返す。
    """
    bearer_token = os.environ.get("X_BEARER_TOKEN", "")
    if not bearer_token:
        print("X_BEARER_TOKEN not set, skipping X fetch.")
        return []

    client = tweepy.Client(bearer_token=bearer_token, wait_on_rate_limit=True)

    # 期間をRFC3339(UTC)に変換
    start_time = datetime(
        LAST_MONDAY.year, LAST_MONDAY.month, LAST_MONDAY.day,
        tzinfo=timezone.utc,
    )
    end_time = datetime(
        LAST_SUNDAY.year, LAST_SUNDAY.month, LAST_SUNDAY.day,
        23, 59, 59, tzinfo=timezone.utc,
    )

    results: list[dict] = []

    for username in X_ACCOUNTS:
        print(f"Fetching X: @{username}")
        try:
            user_resp = client.get_user(username=username, user_fields=["id"])
            if not user_resp.data:
                print(f"  User not found: @{username}", file=sys.stderr)
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

            for tw in tweets:
                results.append({
                    "account": username,
                    "text": tw.text,
                    "url": f"https://x.com/{username}/status/{tw.id}",
                })
        except tweepy.TweepyException as e:
            print(f"  ERROR @{username}: {e}", file=sys.stderr)

    return results


# ── 記事生成 ────────────────────────────────────────────────────────────
def generate_weekly_article(
    all_site_data: list[dict],
    x_tweets: list[dict],
    rules_text: str,
    week_label: str,
    date_from: str,
    date_to: str,
) -> str:
    """Claude APIで週刊まとめMarkdown記事を生成する"""
    client = anthropic.Anthropic()

    # 各サイトのデータをテキスト化
    site_blocks = []
    for site in all_site_data:
        name = site["name"]
        entries = site["entries"]
        if not entries:
            site_blocks.append(f"### {name}\n（情報なし）")
            continue
        lines = [f"### {name}"]
        for e in entries:
            line = f"- {e['text'][:300]}"
            if e["link"]:
                line += f"\n  URL: {e['link']}"
            lines.append(line)
        site_blocks.append("\n".join(lines))

    raw_data = "\n\n".join(site_blocks)

    # Xツイートのテキスト化
    x_block = ""
    if x_tweets:
        x_lines = []
        for tw in x_tweets:
            x_lines.append(
                f"- @{tw['account']}: {tw['text'][:280]}\n  URL: {tw['url']}"
            )
        x_block = "\n\n### X（Twitter）注目ツイート\n" + "\n".join(x_lines)

    title = f"週刊AIニュース {week_label}週"

    x_section_instruction = (
        "- 収集データの末尾にXツイートがある場合は「## X注目ツイート」セクションを追加し、"
        "アカウントごとにまとめて日本語で要約する（ツイートURLリンクを付ける）"
        if x_tweets else ""
    )

    prompt = f"""以下の収集データをもとに、日本語の週刊AIニュースまとめ記事をMarkdown形式で生成してください。

## ライティング・SEOルール
{rules_text}

## 週刊記事固有のルール
- タイトルは必ず「{title}」を使用する（SEOタイトル14字ルールの例外）
- 対象期間: {date_from} 〜 {date_to}
- サイトごとにH2見出しでセクションを分ける（見出し20字以内厳守）
- 各ニュース項目には必ずソースURLをMarkdownリンクで付ける
- URLがない場合はサイトのトップURLをリンク先にする
- リストやテーブルを適宜使って読みやすくする
- 全文ですます調厳守
- 文字数: 2000〜5000字
- 冒頭にその週の要約（3〜5文）を入れる
- 情報が少ないサイトは「今週の更新はありませんでした」と記載する
{x_section_instruction}

## 収集データ（{date_from} 〜 {date_to}）
{raw_data}{x_block}

## 出力形式
以下のMarkdown構造のみを出力してください（説明文・コードブロック記号は不要）：

---
description: （ディスクリプション：150字以内・結論ベース）
---

# {title}

> 対象期間: {date_from} 〜 {date_to}｜自動生成: {NOW.strftime('%Y-%m-%d %H:%M JST')}

（週の要約：3〜5文）

---

（サイトごとのH2セクション、ニュース一覧、ソースURLリンク）

（Xツイートがある場合は「## X注目ツイート」セクション）

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
        # YYYY-MM-DD → YYYY年MM月DD日週 の表示名
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

    sites = json.loads(SITES_JSON.read_text(encoding="utf-8"))["sites"]
    rules_text = _load_rules()

    # 各サイトを巡回
    all_site_data = []
    for site in sites:
        entries = fetch_site(site)
        all_site_data.append({"name": site["name"], "entries": entries})

    # Xツイートを取得
    x_tweets = fetch_x_tweets()

    # 週刊記事を一括生成
    article = generate_weekly_article(
        all_site_data=all_site_data,
        x_tweets=x_tweets,
        rules_text=rules_text,
        week_label=WEEK_LABEL,
        date_from=LAST_MONDAY.strftime("%Y-%m-%d"),
        date_to=LAST_SUNDAY.strftime("%Y-%m-%d"),
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
