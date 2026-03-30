# Weekly AI News — 設計書

## 概要

毎週月曜9時(JST)にGitHub Actionsが自動実行され、先週月曜〜日曜のAI関連ニュースを収集・まとめてGitHubに保存するシステム。

## ディレクトリ構成

```
Weekly_AI_news/
├── CLAUDE.md                          # このファイル（設計書）
├── scripts/
│   ├── fetch_weekly.py                # ニュース取得・記事生成スクリプト
│   ├── sites.json                     # 監視対象サイト定義
│   └── rules/
│       ├── writing-style.md           # ライティングスタイルルール
│       └── seo.md                     # SEOルール
├── .github/
│   └── workflows/
│       └── weekly.yml                 # GitHub Actions ワークフロー
└── docs/                              # GitHub Pages 公開ディレクトリ
    ├── index.md                       # トップページ（バックナンバー一覧）
    └── YYYY-MM-DD.md                  # 週ごとのまとめ（自動生成、日付は月曜）
```

## 監視対象サイト

| サイト名 | URL |
|---|---|
| Claude Code Changelog | https://docs.anthropic.com/en/release-notes/claude-code |
| Anthropic Blog | https://www.anthropic.com/blog |
| Cursor Changelog | https://www.cursor.com/changelog |
| Devin Release Notes | https://app.devin.ai/release-notes |
| Google DeepMind Blog | https://deepmind.google/discover/blog |
| Mistral Blog | https://mistral.ai/news |
| OpenAI Blog | https://openai.com/news |

サイトの追加・変更は `scripts/sites.json` を編集する。

## 認証・シークレット管理

| 用途 | 場所 | キー名 |
|---|---|---|
| Claude API呼び出し | GitHub Secrets | `ANTHROPIC_API_KEY` |
| GitHub へのpush | GITHUB_TOKEN（自動） | — |

## 記事生成ルール

- `scripts/rules/writing-style.md` と `scripts/rules/seo.md` のルールに従う
- タイトルは「週刊AIニュース YYYY年MM月DD日週」形式（SEOタイトル14字ルールの例外）
- サイトごとにH2セクションで分割
- 各ニュースにソースURLのMarkdownリンクを付ける
- リストやテーブルを適宜使用
- 文字数2000〜4000字、ですます調

## GitHub Pages 設定

リポジトリの Settings → Pages で以下を設定：
- **Source**: GitHub Actions（または Deploy from branch: `main`, `/docs`）
- 公開ディレクトリ: `docs/`

## スケジュール

```
cron: "0 0 * * 1"   # 毎週月曜 00:00 UTC = 09:00 JST
```

対象期間: 実行日の前週月曜〜日曜（7日分）

手動実行: GitHub → Actions → Weekly AI News → Run workflow

## フロー

1. `fetch_weekly.py` が各サイトをHTMLスクレイピング（最大15エントリ/サイト）
2. Claude API（claude-opus-4-6）に全サイトデータを渡して週刊まとめMarkdownを生成
3. `docs/YYYY-MM-DD.md`（月曜日付）に保存
4. `docs/index.md` のバックナンバー一覧を自動更新
5. `git commit && git push` でmainブランチに反映
6. GitHub Pages が `docs/` を自動デプロイ

## ローカル実行

```bash
cd ~/Weekly_AI_news
pip install requests beautifulsoup4 anthropic
export ANTHROPIC_API_KEY=sk-ant-xxxx
python scripts/fetch_weekly.py
```
