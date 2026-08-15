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

```sh
curl -X POST http://127.0.0.1:8080/v1/speak \
  -H 'Authorization: Bearer replace-with-a-long-random-token' \
  -H 'Content-Type: application/json' \
  -d '{
    "text": "お知らせです。テスト発話を開始します。",
    "device_id": "living-room",
    "voice": "Kore",
    "style": "自然で聞き取りやすく話す",
    "title": "テスト発話"
  }'
```

成功すると、Castへの再生要求が`playing`または`buffering`として返ります。

### `GET /v1/devices`

設定済みデバイス一覧を返します。認証を有効にしている場合はBearerトークンが必要です。

### `GET /healthz`

認証なしの死活確認です。

## n8n

n8nからはJSONだけを送ります。Mac評価時は`http://192.168.50.203:8080/v1/speak`、本番Dockerでは同一Dockerネットワークのサービス名または`http://192.168.50.7:8080/v1/speak`を使います。

Nest HubがMP3を取得するURLは、n8n向けURLとは別に`MEDIA_PUBLIC_BASE_URL`で指定します。

## Docker

```sh
cp .env.example .env
# .envのMEDIA_PUBLIC_BASE_URLをhttp://192.168.50.7:8080に変更
docker compose -f docker-compose.example.yml up -d --build
```

n8nからDocker内部で呼ぶ場合は、Composeサービス名が使える構成に合わせてください。Nest HubからのMP3取得には、ホストIPの`192.168.50.7:8080`が使われます。

## 注意

- 同時リクエストのキュー制御や排他制御は実装していません。
- `POST /v1/play`は提供しません。MP3は本体サービス内部で生成・配信します。
- `MEDIA_PUBLIC_BASE_URL`はNest Hubから到達可能である必要があります。
- `API_TOKEN`を空にすると認証なしになるため、LAN内の評価時以外は設定してください。
