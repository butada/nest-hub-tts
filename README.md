# Nest Hub TTS

Gemini TTSで生成した音声を、Google Cast経由で指定したGoogle Nest Hubに再生する小さなRESTサービスです。

## 動作確認済みのCast経路

- リビングのNest Hub：`192.168.50.57`
- Cast UUID：`1c5ab99e-ad4c-c9dc-23f5-cbff043675be`
- 音声は一時MP3としてHTTP配信し、Nest Hubが取得します

実行時のmDNS探索には依存せず、設定済みの固定IPとUUIDを使います。これにより、Docker bridgeネットワークでも動作させやすくしています。

## macOSでの起動

```sh
uv sync --extra dev
cp .env.example .env
```

`.env`を編集し、少なくとも次を設定します。

```dotenv
GEMINI_API_KEY=your-gemini-api-key
MEDIA_PUBLIC_BASE_URL=http://192.168.50.203:8080
API_TOKEN=replace-with-a-long-random-token
```

ffmpegが必要です。

```sh
brew install ffmpeg
uv run nest-hub-tts
```

## API

### `POST /v1/speak`

`execution`を省略すると、これまでどおりTTS生成からNest Hub再生まで完了してからレスポンスを返します。

```sh
curl -X POST http://127.0.0.1:8080/v1/speak \
  -H 'Authorization: Bearer replace-with-a-long-random-token' \
  -H 'Content-Type: application/json' \
  -d '{
    "text": "お知らせです。テスト発話を開始します。",
    "device_id": "living-room",
    "execution": "realtime",
    "cache": true,
    "replay": false,
    "audio_profile": "clear_speech",
    "audio_speed": 1.08,
    "voice": "Kore",
    "style": "自然で聞き取りやすく話す",
    "title": "テスト発話"
  }'
```

成功すると、Castへの再生要求が`playing`または`buffering`として返ります。初回生成時は`cache_hit`が`false`、同じ条件の2回目以降はGeminiを呼ばず`cache_hit`が`true`になります。

`audio_profile`は`clear_speech`（低音を抑え、明瞭度・音量感を調整）または`natural`を指定できます。`audio_speed`は`0.5`から`2.0`の範囲で、`1.08`は標準より少し速い設定です。

`cache`を`false`にすると、そのリクエストだけキャッシュを使わず、生成音声も保存しません。

`replay`を`true`にすると、本文の前に「これは再放送です。」を付けた音声を再生します。通常放送の本文MP3と固定の再放送接頭辞MP3を別々にキャッシュし、再放送時はGeminiを呼ばずに結合して再生します。入力本文がすでに「これは再放送です。」で始まる場合も自動検出します。

レスポンスには`audio_id`と`audio_url`が含まれます。`audio_url`は他のデバイスでの再生に、`audio_id`はBearer認証付きMP3取得APIに使います。キャッシュが見つかった場合は`cache_hit: true`となり、`execution: batch`を指定していてもBatchジョブを作らず即時再生します。

#### Batch APIによる非同期実行

`execution`に`batch`を指定すると、Gemini Batch APIへジョブを登録してHTTP 202を返します。サービスはバックグラウンドで完了を監視し、音声生成・MP3化・Nest Hub再生まで実行します。

```sh
curl -X POST http://127.0.0.1:8080/v1/speak \
  -H 'Authorization: Bearer replace-with-a-long-random-token' \
  -H 'Content-Type: application/json' \
  -d '{
    "text": "あとで読み上げるお知らせです。",
    "device_id": "living-room",
    "execution": "batch",
    "cache": true,
    "replay": true,
    "audio_profile": "clear_speech",
    "audio_speed": 1.08,
    "voice": "Kore",
    "style": "自然で聞き取りやすく話す",
    "title": "Batchのお知らせ"
  }'
```

レスポンス例：

```json
{
  "request_id": "...",
  "device_id": "living-room",
  "execution": "batch",
  "status": "batch_submitted",
  "batch_name": "batches/...",
  "cache_hit": false,
  "tts_generated": null
}
```

Batch APIは非同期で、完了時刻は保証されません。通常は数分以内に完了する場合がありますが、即時発話が必要な処理には`realtime`を使ってください。

ジョブ状態は次で確認できます。

```sh
curl http://127.0.0.1:8080/v1/jobs/REQUEST_ID \
  -H 'Authorization: Bearer replace-with-a-long-random-token'
```

状態は`submitted`、`running`、`succeeded`、`failed`のいずれかです。ジョブ状態は`BATCH_DB_PATH`で指定したSQLiteファイルに保存され、サービス再起動後も監視を再開します。

### キャッシュ済みMP3の取得

`/v1/speak`のレスポンスに含まれる`audio_id`を使って、Bearer認証付きでMP3を取得できます。

```sh
curl -fSL \
  http://127.0.0.1:8080/v1/audio/AUDIO_ID.mp3 \
  -H 'Authorization: Bearer replace-with-a-long-random-token' \
  -o announcement.mp3
```

レスポンスの`audio_url`は、一定時間だけ有効な署名付きURLです。Nest Hubや認証ヘッダーを付けられない他デバイスには、このURLを渡して再生できます。

後から署名付きURLを再発行する場合は、次を使います。

```sh
curl -fS \
  http://127.0.0.1:8080/v1/audio/AUDIO_ID \
  -H 'Authorization: Bearer replace-with-a-long-random-token'
```

`audio_id`が分からない場合は、キャッシュ全体から最後に生成・更新されたMP3を取得できます。キャッシュはデバイス共通です。

MP3を直接ダウンロードする場合：

```sh
curl -fSL \
  http://127.0.0.1:8080/v1/audio/latest.mp3 \
  -H 'Authorization: Bearer replace-with-a-long-random-token' \
  -o latest.mp3
```

他のデバイスへ渡す署名付きURLを取得する場合：

```sh
curl -fS \
  http://127.0.0.1:8080/v1/audio/latest \
  -H 'Authorization: Bearer replace-with-a-long-random-token'
```

キャッシュが空、または期限切れの場合は`404`を返します。

### `GET /v1/devices`

設定済みデバイス一覧を返します。認証を有効にしている場合はBearerトークンが必要です。

### `GET /healthz`

認証なしの死活確認です。

## 利用分析ログ

キャッシュ効果の分析用に、`USAGE_LOG_PATH`へJSON Lines形式のログを保存します。デフォルトは`./data/logs/usage.jsonl`です。

発話ごとに`cache_decision`イベントを1行記録し、次の情報を確認できます。

- キャッシュの有効・無効、ヒット・ミス
- 実際のキャッシュID
- モデル、voice、styleのハッシュ、音声プロファイル、速度
- 全文、発話本文、再放送接頭辞を除いた本文のSHA-256と文字数
- APIの`replay`指定と、入力本文に再放送接頭辞が含まれていたか

本文そのもの、styleの内容、APIトークンは記録しません。`speak_result`イベントにはTTS生成の有無、成功・失敗、処理段階を記録します。再放送時は`replay_components`イベントに本文・接頭辞のキャッシュ利用状況とTTS呼び出し数を記録します。

ログは`USAGE_LOG_MAX_BYTES`でローテーションし、`USAGE_LOG_BACKUP_COUNT`世代を保持します。Dockerではログを残すため、`USAGE_LOG_PATH=/data/logs/usage.jsonl`を設定し、`/data`を永続ボリュームへマウントしてください。

## n8n

n8nからはJSONだけを送ります。Mac評価時は`http://192.168.50.203:8080/v1/speak`、本番Dockerでは同一Dockerネットワークのサービス名または公開ポートの`http://192.168.50.7:8005/v1/speak`を使います。

Nest HubがMP3を取得するURLは、n8n向けURLとは別に`MEDIA_PUBLIC_BASE_URL`で指定します。

## Docker

```sh
cp .env.example .env
# .envのMEDIA_PUBLIC_BASE_URLをNest Hubから到達できるURLに変更
docker compose -f docker-compose.example.yml up -d --build
```

n8nからDocker内部で呼ぶ場合は、Composeサービス名が使える構成に合わせてください。キャッシュ、Batchジョブ状態、利用分析ログを残すため、`./data`をコンテナの`/data`へマウントします。

## 注意

- 同時リクエストのキュー制御や排他制御は実装していません。
- `POST /v1/play`は提供しません。MP3は本体サービス内部で生成・配信します。
- `MEDIA_PUBLIC_BASE_URL`はNest Hubから到達可能である必要があります。
- `API_TOKEN`を空にすると認証なしになるため、LAN内の評価時以外は設定してください。
- Batch APIの監視間隔は`GEMINI_BATCH_POLL_INTERVAL_SECONDS`で変更できます。
- `AUDIO_CACHE_TTL_SECONDS`を過ぎたキャッシュMP3は自動削除されます。デフォルトは30日です。
- キャッシュMP3の直接取得APIはBearer認証付き、`audio_url`は期限付き署名URLです。
