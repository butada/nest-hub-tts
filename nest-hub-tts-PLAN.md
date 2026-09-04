# Nest Hub TTS Cast Tool Plan

## 目的

n8nからJSONで発話テキストを受け取り、Gemini TTSで音声を生成し、MP3として指定したGoogle Nest Hubで再生する。

n8nには音声バイナリを返さず、音声生成・一時配信・Google Cast制御は本体サービス内で完結させる。

## 確認済みの環境

- 評価用Mac：`192.168.50.203`
- 本番ホスト：`192.168.50.7`
- n8n：`192.168.50.7`上のDockerコンテナ
- 本体サービス：本番では`192.168.50.7`上の別Dockerコンテナ、評価時はMac上のuv環境
- 対象Nest Hub：リビング
- Nest HubのIP：`192.168.50.57`
- Cast UUID：`1c5ab99e-ad4c-c9dc-23f5-cbff043675be`
- Castポート：`8009`
- MacからIP直指定でCast接続できることを確認済み
- Mac上の一時HTTPサーバーからMP3をNest Hubが取得し、`PLAYING`から`IDLE`へ遷移することを確認済み

## スコープ

### MVPに含めるもの

- `POST /v1/speak`
- `POST /v1/speak`の`execution=batch`による非同期実行
- `GET /v1/jobs/{request_id}`によるBatchジョブ状態確認
- TTS結果の永続キャッシュ
- キャッシュ効果を検証するローテーション付きJSON Linesログ
- `replay`による再放送文の付加
- `GET /v1/audio/{audio_id}.mp3`によるキャッシュMP3取得
- `GET /v1/audio/latest.mp3`による最新キャッシュMP3取得
- `GET /v1/audio/latest`による最新キャッシュの署名URL発行
- 速度・低音・明瞭度を調整する音声処理プロファイル
- JSONによるテキスト入力
- `gemini-3.1-flash-tts-preview`によるTTS
- TTS出力のMP3正規化
- 一時HTTP配信
- 固定IP指定によるNest Hub選択
- Google CastによるMP3再生
- Bearer APIキー認証
- `GET /v1/devices`
- `GET /healthz`
- macOS/Linux共通のuv実行
- Linux Dockerでの本番起動

### MVPに含めないもの

- `POST /v1/play`
- n8nからのバイナリMP3入力
- 外部MP3 URLの取得
- 複数リクエストのキュー制御・排他制御
- 複数テキストを1つのBatchジョブへまとめる一括入力API
- 複数Nest Hubへの同期再生
- Google Assistantへの直接発話
- mDNS探索への依存

同時実行が発生した場合の動作は保証しない。最後にCastへ送られた再生要求が優先される前提とする。

## 技術方針

- Python 3.11以上
- FastAPI + Uvicorn
- Gemini APIはHTTP経由で呼び出し、APIレスポンスを柔軟に解析する
- 音声がMP3で返らない場合はffmpegでMP3化する
- Cast制御はpychromecastを使う
- Nest Hubは固定IPとCast UUIDで選択する
- mDNSは補助機能とし、本番の必須経路にしない
- 生成MP3はランダムIDの一時ファイルとして保存する
- Nest Hub向けMP3 URLは`MEDIA_PUBLIC_BASE_URL`から生成する
- MP3 URLは期限付き署名で保護する
- Mac評価時の公開URLは`http://192.168.50.203:<port>`
- 本番時の公開URLは`http://192.168.50.7:<port>`
- n8nから本体を呼ぶURLは、Dockerネットワーク内のサービス名またはホスト公開ポートを使う
- TTSキャッシュはモデル、voice、style、本文、音声処理プロファイル、速度をキーにする
- キャッシュ済みMP3は他のデバイスでも取得できるよう、Bearer認証APIと期限付き署名URLを提供する
- `replay=true`時は「これは再放送です。」を本文に付加した完成音声をキャッシュする
- `clear_speech`プロファイルは低域カット、プレゼンス強調、軽いコンプレッサー、ラウドネス正規化を行う
- 利用分析ログには本文を保存せず、全文・本文・再放送接頭辞を除いた本文のハッシュとキャッシュ判定を保存する

## API案

### `POST /v1/speak`

```json
{
  "text": "おはようございます。",
  "device_id": "living-room",
  "execution": "realtime",
  "cache": true,
  "replay": false,
  "audio_profile": "clear_speech",
  "audio_speed": 1.08,
  "voice": "Kore",
  "style": "自然で聞き取りやすく話す",
  "title": "お知らせ"
}
```

`execution`は`realtime`（デフォルト）または`batch`を指定する。`batch`の場合はHTTP 202で受付結果を返し、サービスがバックグラウンドでGemini Batch APIの完了を監視する。完了後は通常経路と同じMP3化・Nest Hub再生を行う。

### `GET /v1/jobs/{request_id}`

Batch実行の状態を返す。`submitted`、`running`、`succeeded`、`failed`を持つ。ジョブ情報はSQLiteに保存し、再起動後に未完了ジョブの監視を再開する。

成功時は、TTS生成とCastへの再生要求が完了した時点で同期レスポンスを返す。

```json
{
  "request_id": "...",
  "device_id": "living-room",
  "execution": "realtime",
  "status": "playing",
  "cache_hit": false,
  "tts_generated": true,
  "audio_id": "...",
  "audio_url": "http://.../audio/..."
}
```

キャッシュMP3は`GET /v1/audio/{audio_id}.mp3`でBearer認証付き取得ができる。`GET /v1/audio/{audio_id}`で期限付き署名URLを再発行する。

`audio_id`が不明な場合は、キャッシュ全体から最後に生成・更新された音声を`GET /v1/audio/latest.mp3`または`GET /v1/audio/latest`で取得できる。キャッシュはデバイス共通とする。

### `GET /v1/devices`

設定済みデバイスと疎通情報を返す。

### `GET /healthz`

プロセスの生存確認を返す。GeminiやNest Hubの疎通確認は別のready情報として扱う。

## 実装手順

1. uvプロジェクト、設定、依存関係を作成する
2. Gemini TTSアダプターを作成する
3. PCM/WAV/MP3をMP3へ正規化する
4. 期限付き署名URL付きの一時メディアストアを作成する
5. 固定IPのNest Hubへpychromecastで再生する
6. FastAPIの`/v1/speak`、`/v1/devices`、`/healthz`を実装する
7. `.env.example`、README、Dockerfile、Docker Compose例を作成する
8. API単体テストと実機スモークテストを実施する

## 受け入れ条件

- Mac上でuvから起動できる
- Linux Dockerコンテナとして起動できる
- n8nからJSONを送るだけで指定Nest Hubが発話する
- MP3 URLをNest Hubが取得できる
- TTS出力がMP3として再生できる
- 未認証リクエストを拒否できる
- 未登録デバイスを明確なエラーにできる
- Gemini失敗、Cast失敗、MP3配信失敗を区別できる
- 一時MP3がTTL経過後に削除される
