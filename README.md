# tact-notify

名古屋大学のLMS **TACT**(Sakaiベース)を定期的に確認し、講義の新着をSlackへ通知するツール。

- **新着通知**（10分ごと）: 新しく公開された課題・小テスト（公開日時・締切日時つき）とお知らせを通知
- **未提出まとめ**（毎朝7:00 JST）: 未提出かつ締切前の課題・小テストを、締切が早い順で番号つきで通知
- **Notion連携**（任意）: 新着の課題・小テストをNotionのデータベースにカードとして自動登録し、提出を検知すると自動でステータスを「完了」に変更（[設定方法](#notion連携任意)）
- **課題マネジメント**（任意）: まとめチャンネルに `hide 2` のようなコマンドを送るだけで、提出不要な課題を未提出まとめから非表示にできる（[設定方法](#課題マネジメント任意)）

処理は GitHub Actions 上で実行される（Publicリポジトリなので無料）。同じ項目が二度通知されることはない（通知済みIDを記録し、新着の差分だけ送る）。通知済みIDやログインセッションは暗号化して Actions キャッシュにのみ保存され、リポジトリには含まれない。

> **起動方法について**: GitHubの `schedule`（cron）は新規リポジトリで発火しない/大幅に遅延することがあるため、本ツールは外部スケジューラ（[cron-job.org](https://cron-job.org) など）から GitHub API 経由で `workflow_dispatch` を叩いて起動する方式にしている（[セットアップ](#定期実行のセットアップ外部cron)参照）。

## スクレイピングについて

TACT公式ヘルプ[「スクレイピングツールを利用したい」](https://tact-help.ac.thers.ac.jp/hc/ja/articles/30325584083993)で、ツールによる自動アクセスは**明示的に許可**されている:

> スクレイピングツールの利用は制限していません。ただし、アクセス負荷がかかるとTACTの障害につながる恐れがありますので、時間を空けて処理を実行するなど、適切にご利用ください。

本ツールはこの方針に従い、Sakaiの公式REST API（`/direct/*.json`）を使い、リクエスト間隔を空け、ログインセッションを再利用して負荷を抑えている。認証は各自の大学アカウントで行い、認証情報はコードには含めず GitHub Secrets（暗号化）にのみ保存する。

## セットアップ

1. このリポジトリをフォーク
2. フォーク先の **Settings → Secrets and variables → Actions** に次の6つを登録:
   | 名前 | 内容 |
   |---|---|
   | `MS_EMAIL` | 大学アカウントのメールアドレス |
   | `MS_PASSWORD` | そのパスワード |
   | `MS_TOTP_SECRET` | 認証アプリのTOTP秘密鍵（下記） |
   | `SLACK_WEBHOOK_NOTIFY` | 新着通知チャンネルの Incoming Webhook URL |
   | `SLACK_WEBHOOK_DIGEST` | 未提出まとめチャンネルの Incoming Webhook URL |
   | `CACHE_ENC_KEY` | キャッシュ暗号化キー（`openssl rand -hex 32` で生成した任意のランダム文字列） |
3. **Actions** タブでワークフローを有効化する
4. 下記「定期実行のセットアップ」で外部スケジューラから定期起動を設定する

初回実行では既存の課題・お知らせを記録するだけで通知は出ない（以後の新着から通知）。手動で試すには Actions タブの「Run workflow」、または `gh workflow run notify.yml -f mode=check`。

## 定期実行のセットアップ（外部cron）

GitHubの `schedule` に頼らず、外部スケジューラから `workflow_dispatch` を叩いて確実に起動する。

**1. GitHubトークン（fine-grained PAT）を発行**
- https://github.com/settings/personal-access-tokens/new
- Repository access: **Only select repositories → 自分の tact-notify**
- Permissions → Repository permissions → **Actions: Read and write**
- 生成した `github_pat_...` を控える（Actions起動専用。Secretsやコードには触れない権限）

**2. cron-job.org（無料）でジョブを2つ作成**
- 共通:
  - URL: `https://api.github.com/repos/<自分>/tact-notify/actions/workflows/notify.yml/dispatches`
  - Method: `POST`
  - Headers: `Accept: application/vnd.github+json` / `Authorization: Bearer <PAT>` / `X-GitHub-Api-Version: 2022-11-28` / `Content-Type: application/json`
- ジョブA（新着チェック）: 10分ごと / Body `{"ref":"main","inputs":{"mode":"check"}}`
- ジョブB（未提出まとめ）: 毎日 07:00 JST / Body `{"ref":"main","inputs":{"mode":"daily"}}`

### TOTP秘密鍵の取得

多要素認証を自動で通過するために必要。

1. https://mysignins.microsoft.com/security-info を開く
2. 「サインイン方法の追加」→「認証アプリ」→「別の認証アプリを使用します」
3. QRコード画面で「画像をスキャンできません」を選ぶと表示される **Secret key** を控える
4. 続く画面で確認コードの入力を求められるので、`uv run python -m tact_notify totp` で生成した6桁を入力し、登録を完了する

### Slack Incoming Webhook

https://api.slack.com/apps → Create New App（From scratch）→ Incoming Webhooks を On → 通知したいチャンネルごとに「Add New Webhook to Workspace」で URL を発行する。

## Notion連携（任意）

新着の課題・小テストをNotionの専用データベースにカードとして登録し、提出を検知すると自動でステータスを「完了」にする。未設定なら従来どおりSlack通知のみ。

### 1. データベースを用意する

以下のプロパティを持つデータベースを作成する（名前は**一字一句この通り**にすること。`TACT ID` のスペースも含む。改名すると連携が壊れる）:

| プロパティ | 型 | 内容 |
|---|---|---|
| 名前 | タイトル | 課題名 |
| 講義 | セレクト | 講義サイト名（自動追加される） |
| 締切 | 日付 | 締切日時 |
| 種類 | セレクト | 課題 / クイズ |
| ステータス | セレクト | 未着手 / 進行中 / 完了 |
| TACT ID | テキスト | 重複防止キー（触らない） |
| URL | URL | TACTの講義サイトへのリンク |

おすすめビュー: ギャラリー「未完了」（締切昇順・「ステータス≠完了」フィルタ）、カレンダー（締切ベース）、テーブル「全件」。

> カードを一覧から消したいときは、ページを**削除せず**ステータスを「完了」にする（フィルタで消える）。手動で「完了」にしてもよい。システムがステータスを「完了」以外に書き換えることはない。

### 2. インテグレーションを作成してDBに接続する

1. https://www.notion.so/my-integrations →「新しいインテグレーション」
2. 名前 `tact-notify`、種類は**内部**、対象ワークスペースを選択して保存
3. 「機能」タブで「コンテンツを読み取る・更新・挿入」が有効なことを確認し、「内部インテグレーションシークレット」（`ntn_...`）をコピー
4. 作成したDBページの右上 `⋯` →「接続」→ `tact-notify` を追加

### 3. データソースIDを取得する

DBページのURLに含まれる32桁の英数字が database_id。以下でデータソースIDを取得する:

```sh
curl -H "Authorization: Bearer <NOTION_TOKEN>" -H "Notion-Version: 2025-09-03" \
  https://api.notion.com/v1/databases/<database_id>
```

レスポンスの `data_sources[0].id` が `NOTION_DS_ID`。

### 4. 環境変数を登録する

GitHub Secrets（Settings → Secrets and variables → Actions）とローカル `.env` に以下を追加:

| 名前 | 内容 |
|---|---|
| `NOTION_TOKEN` | インテグレーションシークレット（`ntn_...`） |
| `NOTION_DS_ID` | データソースID |

### 5. 接続確認

```sh
uv run python -m tact_notify notion-test
```

トークン検証→クエリ→テストページ作成→完了化まで通ることを確認する（テストページは確認後に削除してよい）。

### 動作の詳細

- 初回実行（またはstate消失後）は、未提出・締切前の課題をまとめてNotionに登録する
- 2回目以降は新着の課題・小テストのみ登録（お知らせは登録しない）
- 提出検知は10分ごとのcheckと毎朝のdailyの両方で動き、検知するとカードのステータスを「完了」に変更する
- `TACT ID` で重複判定するため、stateが消えてもページは重複しない
- Notion APIの障害時は警告ログのみで続行し、Slack通知には影響しない（未登録分は次回実行で自動リトライ）

## 課題マネジメント（任意）

未提出まとめには全サイト（講義以外のプロジェクトサイト含む）の課題が載る。提出が不要なものは、まとめが届くチャンネルにコマンドを送るだけで非表示にできる。未設定なら従来どおり表示のみで動く。

### 使い方

未提出まとめの各行には番号が付いている。同じチャンネルに次のメッセージを送ると、10分ごとのチェック実行時に反映され、ボットが結果と最新の一覧を返信する:

| コマンド | 効果 |
|---|---|
| `hide 2 5` | 番号2と5の課題を非表示にする |
| `show 1` | 非表示一覧の番号1を再表示する |
| `list` | 最新の番号つき未提出一覧を表示 |
| `hidden` | 非表示中の課題一覧を表示 |
| `help` | 使い方を表示 |

- 番号は「最後に投稿された一覧」に対応する。一覧が更新された後の古い番号は適用されず、最新の一覧が再投稿される（誤操作防止）
- 締切が過ぎた非表示課題は自動でリストから消える（手入れ不要）
- 非表示にした課題はNotionへの新規登録対象からも外れる（既存カードはそのまま）
- 非表示リストは暗号化キャッシュにのみ保存される。キャッシュが失われた場合は非表示が解除されて再表示されるだけで、壊れることはない

### セットアップ

1. https://api.slack.com/apps → **Create New App**（From scratch）→ 名前（例 `tact-manage`）とワークスペースを選んで作成
2. **OAuth & Permissions → Scopes → Bot Token Scopes** に3つ追加: `channels:history`（公開チャンネル用）・`groups:history`（非公開チャンネル用）・`chat:write`
3. 同ページ上部の **Install to Workspace** で許可し、**Bot User OAuth Token**（`xoxb-...`）を控える
4. まとめチャンネルで `/invite @tact-manage` を送信してボットを招待する
5. チャンネル名クリック → チャンネル情報の最下部にある **チャンネルID**（`C...`）を控える
6. GitHub Secrets とローカル `.env` に以下を追加:
   | 名前 | 内容 |
   |---|---|
   | `SLACK_BOT_TOKEN` | Bot User OAuth Token（`xoxb-...`） |
   | `SLACK_CHANNEL_DIGEST` | まとめチャンネルのチャンネルID |
7. 接続確認: `uv run python -m tact_notify test`（トークン・スコープ・チャンネル招待を検証し、問題があれば原因を表示する）

> 有効化後の最初のチェック実行では、それまで対象外だったサイトの既存項目を「通知なしで記録した」旨のメッセージが一度だけ届く（新着通知が大量に流れることはない）。

## ローカル実行 / 開発

```sh
uv sync
uv run playwright install chromium

uv run python -m tact_notify test    # 両チャンネルにサンプル通知を送り書式を確認
uv run python -m tact_notify check   # 新着チェック
uv run python -m tact_notify daily   # 未提出まとめ
uv run python -m tact_notify manage  # チャンネルの hide/show コマンドを即時処理
```

`.env`（`.env.template` 参照）に上記5変数を書けばローカルでも動く（Notion連携を使う場合は `NOTION_TOKEN` / `NOTION_DS_ID` も）。`--dry-run` を付けると Slack送信せず内容を表示する。

## 仕組み

```
外部cron → workflow_dispatch → GitHub Actions
  └─ python -m tact_notify {check|daily}
       ├─ auth     Playwright で Microsoft SSO ログイン（TOTP / Shibboleth同意を自動処理）
       ├─ sakai    Sakai /direct REST API から課題・小テスト・お知らせ・講義名を取得
       ├─ state    通知済みID（state/seen.json）を Actions キャッシュで永続化（非コミット・暗号化）
       ├─ notion   （任意）課題・小テストをNotion DBへ登録、提出検知で完了化
       ├─ check    新着の差分 → 新着通知 + Notion登録
       └─ daily    未提出・締切前の一覧 → 未提出まとめ + Notion完了化
```

取得対象は参加している全サイト（講義サイトとプロジェクトサイトの両方）。表示したくない課題は[課題マネジメント](#課題マネジメント任意)で個別に非表示にできる。取得できる時刻や提出状況などのフィールド仕様は `uv run python -m tact_notify probe` で実データを確認できる。

## ライセンス

[MIT License](LICENSE)
