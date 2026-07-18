# tact-notify

名古屋大学のLMS **TACT**(Sakaiベース)を定期的に確認し、講義の新着をSlackへ通知するツール。

- **新着通知**（10分ごと）: 新しく公開された課題・小テスト（公開日時・締切日時つき）とお知らせを通知
- **未提出まとめ**（毎朝7:00 JST）: 未提出かつ締切前の課題・小テストを、締切が早い順で通知

GitHub Actions 上で自動実行され、PCの電源に依存しない。同じ項目が二度通知されることはない（通知済みIDを記録し、新着の差分だけ送る）。通知済みIDやログインセッションは Actions のキャッシュに保持し、リポジトリには一切コミットしない。

## スクレイピングについて

TACT公式ヘルプ[「スクレイピングツールを利用したい」](https://tact-help.ac.thers.ac.jp/hc/ja/articles/30325584083993)で、ツールによる自動アクセスは**明示的に許可**されている:

> スクレイピングツールの利用は制限していません。ただし、アクセス負荷がかかるとTACTの障害につながる恐れがありますので、時間を空けて処理を実行するなど、適切にご利用ください。

本ツールはこの方針に従い、Sakaiの公式REST API（`/direct/*.json`）を使い、リクエスト間隔を空け、ログインセッションを再利用して負荷を抑えている。認証は各自の大学アカウントで行い、認証情報はコードには含めず GitHub Secrets（暗号化）にのみ保存する。

## セットアップ

1. このリポジトリをフォーク
2. フォーク先の **Settings → Secrets and variables → Actions** に次の5つを登録:
   | 名前 | 内容 |
   |---|---|
   | `MS_EMAIL` | 大学アカウントのメールアドレス |
   | `MS_PASSWORD` | そのパスワード |
   | `MS_TOTP_SECRET` | 認証アプリのTOTP秘密鍵（下記） |
   | `SLACK_WEBHOOK_NOTIFY` | 新着通知チャンネルの Incoming Webhook URL |
   | `SLACK_WEBHOOK_DIGEST` | 未提出まとめチャンネルの Incoming Webhook URL |
3. **Actions** タブでワークフローを有効化する

これで `check`（10分ごと）と `daily`（毎朝7:00 JST）が自動実行される。初回は既存の課題・お知らせを記録するだけで通知は出ない（以後の新着から通知）。

### TOTP秘密鍵の取得

多要素認証を自動で通過するために必要。

1. https://mysignins.microsoft.com/security-info を開く
2. 「サインイン方法の追加」→「認証アプリ」→「別の認証アプリを使用します」
3. QRコード画面で「画像をスキャンできません」を選ぶと表示される **Secret key** を控える
4. 続く画面で確認コードの入力を求められるので、`uv run python -m tact_notify totp` で生成した6桁を入力し、登録を完了する

### Slack Incoming Webhook

https://api.slack.com/apps → Create New App（From scratch）→ Incoming Webhooks を On → 通知したいチャンネルごとに「Add New Webhook to Workspace」で URL を発行する。

## ローカル実行 / 開発

```sh
uv sync
uv run playwright install chromium

uv run python -m tact_notify test    # 両チャンネルにサンプル通知を送り書式を確認
uv run python -m tact_notify check   # 新着チェック
uv run python -m tact_notify daily   # 未提出まとめ
```

`.env`（`.env.template` 参照）に上記5変数を書けばローカルでも動く。`--dry-run` を付けると Slack送信せず内容を表示する。

## 仕組み

```
GitHub Actions (cron)
  └─ python -m tact_notify {check|daily}
       ├─ auth     Playwright で Microsoft SSO ログイン（TOTP / Shibboleth同意を自動処理）
       ├─ sakai    Sakai /direct REST API から課題・小テスト・お知らせ・講義名を取得
       ├─ state    通知済みID（state/seen.json）を Actions キャッシュで永続化（非コミット）
       ├─ check    新着の差分 → 新着通知
       └─ daily    未提出・締切前の一覧 → 未提出まとめ
```

通知対象は通常の講義サイト（`site.type == "course"`）のみ。取得できる時刻や提出状況などのフィールド仕様は `uv run python -m tact_notify probe` で実データを確認できる。

## ライセンス

[MIT License](LICENSE)
