# tact-notify

名古屋大学のLMS **TACT**(Sakaiベース)を定期的に確認し、講義の新着をSlackへ通知するツール。

- **新着通知**（10分ごと）: 新しく公開された課題・小テスト（公開日時・締切日時つき）とお知らせを通知
- **未提出まとめ**（毎朝7:00 JST）: 未提出かつ締切前の課題・小テストを、締切が早い順で通知
- **Notion連携**（任意）: 新着の課題・小テストをNotionのデータベースにカードとして自動登録し、提出を検知すると自動でステータスを「完了」に変更（[設定方法](#notion連携任意)）

処理は GitHub Actions 上で実行され（Publicリポジトリなので無料）、PCの電源に依存しない。同じ項目が二度通知されることはない（通知済みIDを記録し、新着の差分だけ送る）。通知済みIDやログインセッションは Actions のキャッシュに保持し、リポジトリには一切コミットしない。

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

## ローカル実行 / 開発

```sh
uv sync
uv run playwright install chromium

uv run python -m tact_notify test    # 両チャンネルにサンプル通知を送り書式を確認
uv run python -m tact_notify check   # 新着チェック
uv run python -m tact_notify daily   # 未提出まとめ
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

通知対象は通常の講義サイト（`site.type == "course"`）のみ。取得できる時刻や提出状況などのフィールド仕様は `uv run python -m tact_notify probe` で実データを確認できる。

## ライセンス

[MIT License](LICENSE)
