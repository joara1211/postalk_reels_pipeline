# 릴스 내부 API 명세

POSTALK 백엔드와 릴스 서비스의 운영 연동을 설명한다. 배포와 호출 순서는 [백엔드 연동 안내](backend-integration.md)를 참고한다.

## 1. 개요

릴스는 대본(script)과 생성(render) 두 작업으로 나뉜다. 프론트는 Next.js 백엔드를 호출하고, Next.js가 아래 릴스 API를 호출한다. 프론트는 미디어 서버를 직접 호출하지 않는다.

| 백엔드 작업 | 릴스 API |
| --- | --- |
| 마지막 영상 제출 후 대본 생성 | POST /reels/scripts |
| 대본 결과 조회 | GET /reels/scripts/{script_id} |
| 완성된 대본과 영상으로 렌더링 요청 | POST /reels/renders |
| 렌더링 상태 조회 | GET /reels/renders/{render_id} |
| 콘셉트·장면 규칙 조회 | GET /reels/templates |

대본 API는 자막을 쓰는 템플릿에서만 호출한다. 재료 업로드, 광고 목록·상세, 캡션 수정, 게시, 삭제는 Next.js가 처리하며 릴스 API 범위가 아니다.

배포와 인증 (2026-11 백엔드 연동 반영)

- 릴스 서비스는 POSTALK 백엔드와 같은 Railway 프로젝트의 별도 Docker 서비스로 띄우고, 공개 도메인 없이 내부 주소(`http://<서비스 이름>.railway.internal:8000`)로만 호출한다. 자세한 연동 절차는 [backend-integration.md](backend-integration.md), [deployment.md](deployment.md).
- `GET /healthz`를 제외한 모든 `/reels/*` 요청은 `x-reels-api-key` 헤더에 `REELS_INTERNAL_API_KEY` 값을 담아야 한다. 틀리면 `401`, 서비스에 `SUPABASE_URL`은 있는데 키가 설정 안 됐으면 `503`.
- 로컬 개발(`SUPABASE_URL`, `REELS_INTERNAL_API_KEY` 둘 다 미설정)에서는 인증 없이 호출된다.

공통 규칙

- 운영 백엔드는 내부 요청의 `user_id`에 `1`을 넣고 제작 세션의 가게·메뉴 정보를 전달한다. 작업 공간 접근 권한은 백엔드에서 확인한다.
- 운영 요청은 `storage: "supabase"`와 입력 객체 키·서명 다운로드 URL, 결과용 서명 업로드 URL을 사용한다. 프론트에는 백엔드가 발급한 결과 미리보기 URL을 전달한다.
- `GET /healthz`를 제외한 운영 요청은 `x-reels-api-key`를 사용한다. 키가 다르면 401, `SUPABASE_URL`을 설정한 상태에서 서비스 키가 없으면 503이다.
- 결과물은 1080×1920 MP4(H.264/AAC)이며 메뉴 1·2·3개에 따라 16·20·24초다.
- 시간은 ISO 8601, 길이는 초, 처리 시간은 밀리초.

## 2. 템플릿과 장면 구성

장면 수는 메뉴 수에 따라 달라진다. 공통 장면 2개(훅·마무리)에 메뉴마다 2개(클로즈업·디테일)를 더한다. 두 콘셉트 모두 메뉴 장면당 최소 3초를 배정한다.

| 순서 | 장면(scene_type) | 대상 | 재료 | 자막 내용 |
| --- | --- | --- | --- | --- |
| 1 | hook | 가게 | 간판 영상 | 가게 이름 + 한 줄 소개 |
| 메뉴마다 | closeup | 메뉴 n | 촬영 | 메뉴명 |
| 메뉴마다 | detail | 메뉴 n | 촬영 | 특징, 가격(설명에 있을 때) |
| 마지막 | outro | 가게 | 가게 입구 영상 | 방문 유도 문구 |

| 메뉴 수 | 장면 수 | 촬영 + 갤러리 | 메뉴 장면 길이 | 전체 길이 |
| --- | --- | --- | --- | --- |
| 1 | 4 | 3 + 1 | 5초 | 16초 |
| 2 | 6 | 5 + 1 | 3.5초 | 20초 |
| 3 | 8 | 7 + 1 | 3초 | 24초 |

훅과 마무리는 각각 3초다. 메뉴 장면 길이는 장면당 평균이며, 하나의 메뉴에 배정된 시간 안에서 원본 길이와 최소 장면 길이를 기준으로 클로즈업과 디테일을 나눈다. 자막 제한은 훅·클로즈업·디테일·마무리 순으로 20·18·24·20자다.

콘셉트별 자막·오디오

| concept | 이름 | 자막(has_captions) | 오디오 |
| --- | --- | --- | --- |
| simple | 심플 | 사용 | BGM |
| christmas | 크리스마스 | 사용 | BGM |

릴스의 전환·자막 스타일·BGM은 콘셉트별 템플릿 JSON을 따른다.

BGM은 콘셉트별 곡 목록(`audio.bgm_tracks`, 심플 4곡·크리스마스 3곡)에서 렌더마다 한 곡을 무작위로 고른다(2026-11-17 변경, 이전엔 콘셉트별 1곡). 사용자가 고르는 기능은 두지 않는다. 곡 출처와 라이선스는 `assets/bgm/CREDITS.md`. TTS 내레이션은 쓰지 않는다(2026-11-02 결정) — 영상 소리는 BGM뿐이다. 촬영 원본 소리는 모두 제거한다.

### GET /reels/templates

프론트의 템플릿 장면 구성 정의와 같은 내용. 앱에 포함하더라도 이 응답을 원본으로 삼는다.

```json
{
  "templates": [
    {
      "concept": "simple",
      "name": "심플",
      "has_captions": true,
      "audio": { "bgm": "assets/bgm/MP_맑은 아침 (Short).mp3", "bgm_tracks": ["assets/bgm/MP_맑은 아침 (Short).mp3", "assets/bgm/simple_go_to_the_picnic.mp3", "..."], "bgm_volume": 0.3 },
      "scene_rule": {
        "common": [
          { "scene_type": "hook", "position": "first", "source": "camera", "duration_sec": 3.0, "caption_max_chars": 20, "min_record_sec": 3, "guide": "대표 상품이 모두 보이게 찍어주세요", "guide_image": "hook" },
          { "scene_type": "outro", "position": "last", "source": "gallery", "duration_sec": 3.0, "caption_max_chars": 20, "min_record_sec": 2, "guide": "가게 모습이 담긴 영상을 골라주세요", "guide_image": "outro" }
        ],
        "per_menu": [
          { "scene_type": "closeup", "source": "camera", "caption_max_chars": 18, "min_record_sec": 3, "guide": "가까이서 천천히 찍어주세요", "guide_image": "closeup" },
          { "scene_type": "detail", "source": "camera", "caption_max_chars": 24, "min_record_sec": 3, "guide": "단면이나 질감이 보이게 찍어주세요", "guide_image": "detail" }
        ],
        "menu_block_sec": { "1": 10.0, "2": 14.0, "3": 18.0 }
      }
    }
  ]
}
```

- 장면 순서: hook → 메뉴 1(closeup, detail) → … → 메뉴 n → outro. 이 순서의 0부터 시작하는 번호가 `scene_index`.
- `min_record_sec`보다 짧으면 프론트가 재촬영을 요청한다(SC-04).
- `guide_image`는 메뉴 종류와 무관한 공통 구도 샘플 이름(이미지는 앱에 포함).
- `caption_max_chars`는 SC-06 글자 수 제한에 그대로 쓴다. 현재 2종 모두 `has_captions`가 true다. 기능명세서의 자막 없는 템플릿 분기를 위해 필드는 남겨 두며, false면 대본 API를 호출하지 않고 SC-06을 건너뛴다.

## 3. 대본 생성·결과 조회

### POST /reels/scripts

운영 백엔드는 마지막 영상 제출로 생성 작업을 시작한 뒤 대본을 요청한다.

요청

```json
{
  "user_id": 1,
  "concept": "simple",
  "store": {
    "name": "해오름 떡집",
    "description": "30년 전통 방식으로 만드는 동네 떡집"
  },
  "menus": [
    { "menu_index": 0, "name": "창억떡", "description": "쫀득한 식감, 한 팩 5,000원" }
  ]
}
```

- `store.name`과 `store.description`에는 제작 세션의 가게 이름과 소개를 전달한다.
- `menus`는 1~3개. `menu_index`는 입력 순서(0부터)이며 재료·장면을 연결하는 메뉴 식별값.

응답 (202 Accepted)

```json
{ "script_id": "rs_51c0e2", "status": "pending" }
```

### GET /reels/scripts/{script_id}

백엔드는 약 0.5초 간격으로 대본 상태를 조회하고 `done`이 되면 렌더링을 요청한다.

```json
{
  "script_id": "rs_51c0e2",
  "status": "done",
  "is_fallback": false,
  "scenes": [
    { "scene_index": 0, "scene_type": "hook", "menu_index": null, "target": "가게", "text": "30년 전통 해오름 떡집", "max_chars": 20 },
    { "scene_index": 1, "scene_type": "closeup", "menu_index": 0, "target": "창억떡", "text": "쫀득한 창억떡", "max_chars": 18 },
    { "scene_index": 2, "scene_type": "detail", "menu_index": 0, "target": "창억떡", "text": "한 팩 5,000원의 행복", "max_chars": 24 },
    { "scene_index": 3, "scene_type": "outro", "menu_index": null, "target": "가게", "text": "오늘 바로 들러주세요", "max_chars": 20 }
  ],
  "caption": "30년 전통 방식 그대로, 해오름 떡집 창억떡을 만나보세요.",
  "hashtags": ["#창억떡", "#떡집", "#동네맛집"],
  "error": null
}
```

- status: `pending` | `done` | `failed`.
- `target`은 SC-06 "장면 대상 표시"에 그대로 쓴다. `text`는 화면에 뜨는 자막 문장(TTS 없음).
- AI를 사용할 수 없거나 유효한 응답을 얻지 못하면 기본 자막·캡션·해시태그를 생성해 `done`, `is_fallback: true`로 반환한다. 대본 작업 자체가 실패하면 `failed`와 `error`를 반환한다.
- 백엔드는 `scenes`, `caption`, `hashtags`를 렌더링 요청에 전달하고 캡션과 해시태그를 생성 결과에 저장한다.

## 4. 생성 요청

### POST /reels/renders

영상 업로드와 대본 생성이 끝난 뒤 백엔드가 호출한다. `ad_id`에는 백엔드 생성 작업 ID를 사용한다.

요청

```json
{
  "user_id": 1,
  "ad_id": "ad_20261120_0042",
  "concept": "simple",
  "store": { "name": "해오름 떡집", "description": "30년 전통 방식으로 만드는 동네 떡집" },
  "menus": [
    { "menu_index": 0, "name": "창억떡", "description": "쫀득한 식감, 한 팩 5,000원" }
  ],
  "media": [
    { "scene_index": 0, "menu_index": null, "key": "stores/example/video_clip/0.mp4", "storage": "supabase", "download_url": "장면 0 서명 다운로드 URL" },
    { "scene_index": 1, "menu_index": 0, "key": "stores/example/video_clip/1.mp4", "storage": "supabase", "download_url": "장면 1 서명 다운로드 URL" },
    { "scene_index": 2, "menu_index": 0, "key": "stores/example/video_clip/2.mp4", "storage": "supabase", "download_url": "장면 2 서명 다운로드 URL" },
    { "scene_index": 3, "menu_index": null, "key": "stores/example/video_clip/3.mp4", "storage": "supabase", "download_url": "장면 3 서명 다운로드 URL" }
  ],
  "uploads": {
    "video_url": "https://<project>.supabase.co/storage/v1/object/upload/sign/uploads/1/ad_20261120_0042/generated/ad_20261120_0042.mp4?token=...",
    "thumbnail_url": "https://<project>.supabase.co/storage/v1/object/upload/sign/uploads/1/ad_20261120_0042/generated/ad_20261120_0042.jpg?token=..."
  },
  "scenes": [
    { "scene_index": 0, "text": "30년 전통 해오름 떡집" },
    { "scene_index": 1, "text": "쫀득한 창억떡" },
    { "scene_index": 2, "text": "입에 착 붙는 쫀득함" },
    { "scene_index": 3, "text": "오늘 바로 들러주세요" }
  ],
  "caption": "30년 전통 방식 그대로, 해오름 떡집 창억떡을 만나보세요.",
  "hashtags": ["#창억떡", "#떡집", "#동네맛집"],
  "uploads": {
    "video_url": "MP4 서명 업로드 URL",
    "thumbnail_url": "JPG 서명 업로드 URL"
  }
}
```

- `media`는 장면마다 영상 1개. 기능명세서의 "장면 번호, 메뉴 식별값"이 `scene_index`와 `menu_index`.
- `scenes`는 대본 응답에서 가져온 자막 목록이다. `uploads`에는 같은 생성 경로의 `<ad_id>.mp4`와 `<ad_id>.jpg`용 URL을 넣는다.
- 자막이 max_chars를 넘으면 `400 CAPTION_TOO_LONG`, 장면 수가 메뉴 수 규칙과 다르면 `400 SCENE_MISMATCH`.
- 현재 렌더 응답의 `fallbacks`는 빈 배열이다. 다운로드나 합성에 실패하면 작업 상태와 오류 코드를 확인한다.

응답 (202 Accepted)

```json
{ "render_id": "rr_7f3a91c04b2e4d8f9a1b6c3e5d7f0a12", "ad_id": "ad_20261120_0042", "status": "queued" }
```

- `render_id`는 `rr_` + 32자리 16진수.
- 같은 ad_id가 이미 queued나 rendering이면 `409 ALREADY_RENDERING`과 기존 render_id를 돌려준다.
- 같은 서명 업로드 작업이 이미 완료됐으면 기존 `render_id`와 `status: "done"`을 HTTP 202로 반환한다. 이전 작업이 실패한 경우 새 `render_id`를 받는다.

## 5. 생성 상태 조회

### GET /reels/renders/{render_id}

백엔드는 `done` 또는 `failed`까지 약 2초 간격으로 조회한다. 프론트는 공개 백엔드의 생성 진행률과 결과 API를 사용한다.

```json
{
  "render_id": "rr_7f3a91",
  "ad_id": "ad_20261120_0042",
  "status": "done",
  "progress": {
    "stage": "uploading",
    "stage_index": 3,
    "stage_total": 3,
    "label": "거의 다 됐어요",
    "percent": 100
  },
  "result": {
    "video_key": "store-id/submission-id/generated/ad_20261120_0042.mp4",
    "thumbnail_key": "store-id/submission-id/generated/ad_20261120_0042.jpg",
    "video_bytes": 1852104,
    "thumbnail_bytes": 94218,
    "duration_sec": 16.0,
    "caption": "30년 전통 방식 그대로, 해오름 떡집 창억떡을 만나보세요.",
    "hashtags": ["#창억떡", "#떡집", "#동네맛집"],
    "render_ms": 24180,
    "fallbacks": []
  },
  "error": null,
  "updated_at": "2026-11-20T10:16:12+09:00"
}
```

- result는 done일 때만 채워진다. `video_key`·`thumbnail_key`는 `uploads`로 받은 업로드 URL의 객체 경로(`uploads` 버킷 기준)이고, `video_bytes`·`thumbnail_bytes`는 실제로 올린 파일 크기다. Next.js는 이 경로를 브라우저에서 열리는 URL로 바꿔 프론트에 전달한다.
- 로컬 개발(`uploads` 없음)에서는 `video_key`가 `results/{user_id}/{ad_id}/reel.mp4`, `thumbnail_key`가 `results/{user_id}/{ad_id}/thumb.jpg`이고 `*_bytes`는 없다.
- 썸네일은 훅 장면에서 뽑은 1080×1920 JPG. 홈·보관함 목록에서 영상 대신 쓴다. Supabase 연동에서는 썸네일 생성·업로드가 실패하면 작업 전체가 `RENDER_FAILED`가 된다.

상태 값 (status)

| 값 | 의미 | 프론트 동작 |
| --- | --- | --- |
| queued | 요청 접수, 순서 대기 | 진행 표시 |
| rendering | 생성 중 | 단계 문구 + 진행 표시 |
| done | 완료 | SC-08로 replace 이동 |
| failed | 실패 | 안내 + 다시 시도 |

진행 단계 (progress.stage)

| stage_index | stage | label (SC-07 문구) |
| --- | --- | --- |
| 1 | preparing | 영상을 다듬고 있어요 |
| 2 | composing | 장면과 음악을 합치고 있어요 |
| 3 | uploading | 거의 다 됐어요 |

- TTS를 쓰지 않으므로(2026-11-02 결정) narrating 단계는 없다. stage_total은 항상 3. 프론트는 단계 목록을 고정하지 않고 stage_index / stage_total을 그대로 표시한다.
- 내부 진행률은 렌더 단계 기준이다. `uploading`의 `percent: 100`은 업로드 단계에 진입했다는 뜻이며, 성공 여부는 `status: "done"`으로 확인한다. 프론트에 표시하는 생성 진행률은 공개 백엔드가 관리한다.

오류 코드 (error.code)

| 코드 | 상황 | 처리 |
| --- | --- | --- |
| CAPTION_TOO_LONG | 자막 글자 수 초과 | 400, 수정 후 재요청 |
| SCENE_MISMATCH | 장면 수·번호가 규칙과 다름 | 400 |
| INVALID_CONCEPT | 없는 콘셉트이거나 형식이 잘못됨 | 400 |
| INVALID_MENU_COUNT | 메뉴 수가 1~3개가 아님 | 400 |
| INVALID_AD_ID | ad_id 형식 오류 | 400 |
| INVALID_RENDER_INPUT | 서명 URL·Storage 키·결과 경로 불일치, 또는 Supabase 연동 중인데 `uploads` 없음 | 400 |
| ALREADY_RENDERING | 같은 ad_id로 생성 중 | 409, 기존 render_id 반환 |
| NOT_FOUND | 없는 render_id·script_id(서비스 재시작 포함) | 404, 다시 등록 |
| NO_USABLE_MEDIA | 쓸 수 있는 영상이 하나도 없음 | failed, 재료 재전송 안내 |
| RENDER_FAILED | FFmpeg 실패(자동 재시도 1회 후), 원본 다운로드 실패, 결과 업로드 실패 | failed, 다시 시도 |
| TIMEOUT | 60초 초과 | failed, 다시 시도 |

요청 단계의 HTTP 오류는 FastAPI의 `detail`에 담긴다. 수락한 작업이 실패하면 상태 조회 응답의 `error`에 코드와 메시지가 들어간다.

error에는 code와 함께 안내 문구(message)를 넣는다.

운영 백엔드는 상태 조회 API로 작업을 확인한다. 릴스 서비스의 선택적 콜백은 `NEXT_BASE_URL`을 설정한 경우 `POST {NEXT_BASE_URL}/api/internal/reels/callback`을 호출한다. 본문은 상태 조회 응답에 `sent_at`을 추가한 형태이며, `REELS_CALLBACK_API_KEY`가 있으면 `X-Internal-Api-Key` 헤더로 전달한다.
