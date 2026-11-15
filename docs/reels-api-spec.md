# 릴스 API 명세 (초안)

프론트 기능명세서(SC-00~SC-09, 백엔드 요청 사항)와 최종 기획안 기준. 원본 문서: POSTALK 릴스 파이프라인 문서의 "릴스 API 명세 (초안)" 탭.

## 1. 개요

릴스는 대본(script)과 생성(render) 두 작업으로 나뉜다. 프론트는 Next.js 백엔드를 호출하고, Next.js가 아래 릴스 API를 호출한다. 프론트는 미디어 서버를 직접 호출하지 않는다.

| 프론트 요청 (기능명세서 4장) | 화면 | 릴스 API |
| --- | --- | --- |
| 대본 생성 요청 | SC-03 | POST /reels/scripts |
| 대본 결과 조회 | SC-06 | GET /reels/scripts/{script_id} |
| 생성 요청 | SC-07 | POST /reels/renders |
| 생성 상태 조회 | SC-07 | GET /reels/renders/{render_id} (백엔드가 조회) |
| (장면 구성 정의) | SC-02, SC-04 | GET /reels/templates |

대본 API는 자막을 쓰는 템플릿에서만 호출한다. 재료 업로드, 광고 목록·상세, 캡션 수정, 게시, 삭제는 Next.js가 처리하며 릴스 API 범위가 아니다.

배포와 인증 (2026-11 백엔드 연동 반영)

- 릴스 서비스는 POSTALK 백엔드와 같은 Railway 프로젝트의 별도 Docker 서비스로 띄우고, 공개 도메인 없이 내부 주소(`http://<서비스 이름>.railway.internal:8000`)로만 호출한다. 자세한 연동 절차는 [backend-integration.md](backend-integration.md), [deployment.md](deployment.md).
- `GET /healthz`를 제외한 모든 `/reels/*` 요청은 `x-reels-api-key` 헤더에 `REELS_INTERNAL_API_KEY` 값을 담아야 한다. 틀리면 `401`, 서비스에 `SUPABASE_URL`은 있는데 키가 설정 안 됐으면 `503`.
- 로컬 개발(`SUPABASE_URL`, `REELS_INTERNAL_API_KEY` 둘 다 미설정)에서는 인증 없이 호출된다.

공통 규칙

- 사용자는 고정 계정 user_id = 1. 가게 이름·주소는 입력받지 않으므로 Next.js가 회원 정보에서 채워 보낸다.
- 파일은 Supabase Storage로 주고받는다(2026-11-02 결정: S3 연동은 하지 않는다). 릴스 서비스는 Supabase 비밀 키를 갖지 않고, 백엔드가 발급한 **서명 URL**로만 원본을 내려받고 결과를 올린다. `media[].key`와 `result.video_key`·`thumbnail_key`는 `uploads` 버킷 안의 객체 경로이며, 브라우저에서 열리는 URL로 바꾸는 것은 Next.js가 맡는다. `storage` 필드는 `supabase`여야 한다.
- 렌더링 목표 1건당 30초 이내. 결과물은 1080×1920 MP4(H.264/AAC), 13~15초.
- 시간은 ISO 8601, 길이는 초, 처리 시간은 밀리초.

## 2. 템플릿과 장면 구성

장면 수는 메뉴 수에 따라 달라진다(SC-03, SC-04). 공통 장면 2개(훅·마무리)에 메뉴마다 2개(클로즈업·디테일)를 더한다. 장면 규칙은 2종 템플릿 공통이고, 메뉴 수가 늘면 메뉴 장면이 짧아져 전체 길이를 13~15초로 유지한다.

| 순서 | 장면(scene_type) | 대상 | 재료 | 자막 내용 |
| --- | --- | --- | --- | --- |
| 1 | hook | 가게 | 촬영 (대표 상품 전체) | 가게 이름 + 한 줄 소개 |
| 메뉴마다 | closeup | 메뉴 n | 촬영 | 메뉴명 |
| 메뉴마다 | detail | 메뉴 n | 촬영 | 특징, 가격(설명에 있을 때) |
| 마지막 | outro | 가게 | 갤러리 (가게 장면) | 방문 유도 문구 |

| 메뉴 수 | 장면 수 | 촬영 + 갤러리 | 메뉴 장면 길이 | 전체 길이 |
| --- | --- | --- | --- | --- |
| 1 | 4 | 3 + 1 | 4.5초 | 13초 |
| 2 | 6 | 5 + 1 | 2.5초 | 14초 |
| 3 | 8 | 7 + 1 | 약 1.8초 | 15초 |

훅과 마무리는 항상 2초. "메뉴 장면 길이"는 메뉴 하나가 받는 평균치다 — 2026-11-02부로 같은 메뉴의 클로즈업/디테일을
무조건 반반으로 나누지 않고, 실제로 더 길게 찍은 장면에 더 많은 시간을 준다(평균치의 0.5~2배 범위 안에서). 메뉴 하나의
합계와 전체 길이(13~15초)는 항상 이 표대로 정확히 지켜진다 — 바뀌는 건 그 메뉴 안에서 클로즈업과 디테일이 어떻게
나뉘는지뿐이다. 가격 입력란이 없으므로(SC-03) 가격은 메뉴 설명에서 대본 생성 시 추출하고, 없으면 특징만 넣는다.

콘셉트별 자막·오디오

| concept | 이름 | 자막(has_captions) | 오디오 |
| --- | --- | --- | --- |
| simple | 심플 | 사용 | BGM |
| christmas | 크리스마스 | 사용 | BGM |

릴스와 카드뉴스 모두 이 2종 콘셉트를 쓴다. 컷 속도, 전환, 자막 스타일, 색, BGM 곡 같은 디자인은 아직 정해지지 않았다. 모두 템플릿 JSON 값으로만 정하므로, 확정되면 API와 코드 변경 없이 반영한다.

BGM은 콘셉트별 기본곡을 쓰고, 사용자가 고르는 기능은 두지 않는다. TTS 내레이션은 쓰지 않는다(2026-11-02 결정) — 영상 소리는 BGM뿐이다. 촬영 원본 소리는 모두 제거한다.

### GET /reels/templates

프론트의 템플릿 장면 구성 정의와 같은 내용. 앱에 포함하더라도 이 응답을 원본으로 삼는다.

```json
{
  "templates": [
    {
      "concept": "simple",
      "name": "심플",
      "has_captions": true,
      "audio": { "bgm": "simple_01", "bgm_volume": 0.3 },
      "scene_rule": {
        "common": [
          { "scene_type": "hook", "position": "first", "source": "camera", "duration_sec": 2.0, "caption_max_chars": 14, "min_record_sec": 3, "guide": "대표 상품이 모두 보이게 찍어주세요", "guide_image": "hook" },
          { "scene_type": "outro", "position": "last", "source": "gallery", "duration_sec": 2.0, "caption_max_chars": 16, "min_record_sec": 2, "guide": "가게 모습이 담긴 영상을 골라주세요", "guide_image": "outro" }
        ],
        "per_menu": [
          { "scene_type": "closeup", "source": "camera", "caption_max_chars": 12, "min_record_sec": 3, "guide": "가까이서 천천히 찍어주세요", "guide_image": "closeup" },
          { "scene_type": "detail", "source": "camera", "caption_max_chars": 16, "min_record_sec": 3, "guide": "단면이나 질감이 보이게 찍어주세요", "guide_image": "detail" }
        ],
        "menu_block_sec": { "1": 9.0, "2": 10.0, "3": 11.0 }
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

SC-03에서 다음을 누르는 시점에 호출하고 응답을 기다리지 않는다. `has_captions`가 true인 콘셉트에서만 호출한다.

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

- `store.name`은 Next.js가 회원 정보에서 채운다. `store.description`은 SC-03 가게 설명.
- `menus`는 1~3개. `menu_index`는 입력 순서(0부터)이며 재료·장면을 연결하는 메뉴 식별값.

응답 (202 Accepted)

```json
{ "script_id": "rs_51c0e2", "status": "pending" }
```

### GET /reels/scripts/{script_id}

SC-06에 진입하면 status가 done이 될 때까지 조회한다.

```json
{
  "script_id": "rs_51c0e2",
  "status": "done",
  "is_fallback": false,
  "scenes": [
    { "scene_index": 0, "scene_type": "hook", "menu_index": null, "target": "가게", "text": "30년 전통 해오름 떡집", "max_chars": 14 },
    { "scene_index": 1, "scene_type": "closeup", "menu_index": 0, "target": "창억떡", "text": "쫀득한 창억떡", "max_chars": 12 },
    { "scene_index": 2, "scene_type": "detail", "menu_index": 0, "target": "창억떡", "text": "한 팩 5,000원의 행복", "max_chars": 16 },
    { "scene_index": 3, "scene_type": "outro", "menu_index": null, "target": "가게", "text": "오늘 바로 들러주세요", "max_chars": 16 }
  ],
  "caption": "30년 전통 방식 그대로, 해오름 떡집 창억떡을 만나보세요.",
  "hashtags": ["#창억떡", "#떡집", "#동네맛집"],
  "error": null
}
```

- status: `pending` | `done` | `failed`.
- `target`은 SC-06 "장면 대상 표시"에 그대로 쓴다. `text`는 화면에 뜨는 자막 문장(TTS 없음).
- LLM이 1회 재시도 후에도 실패하면 기본 문구에 가게명·메뉴명만 넣어 done으로 돌려주고 `is_fallback: true`. failed는 입력값이 잘못된 경우에만 나오며, SC-06 재요청 버튼은 같은 요청을 다시 보낸다.
- caption·hashtags는 생성 요청에 그대로 넘기고, SC-08에서 사용자가 수정한다. 인스타그램 제한(본문 2,200자, 해시태그 30개) 안에서 만든다.

## 4. 생성 요청

### POST /reels/renders

SC-07에서 재료 업로드가 끝난 뒤 Next.js가 호출한다. `ad_id`는 Next.js가 만든 광고 ID이고, 릴스 API는 이 값을 그대로 기록한다.

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
    { "scene_index": 0, "menu_index": null, "key": "1/ad_20261120_0042/0.mp4", "storage": "supabase", "download_url": "https://<project>.supabase.co/storage/v1/object/sign/uploads/1/ad_20261120_0042/0.mp4?token=..." },
    { "scene_index": 1, "menu_index": 0, "key": "1/ad_20261120_0042/1.mp4", "storage": "supabase", "download_url": "https://<project>.supabase.co/storage/v1/object/sign/uploads/1/ad_20261120_0042/1.mp4?token=..." },
    { "scene_index": 2, "menu_index": 0, "key": "1/ad_20261120_0042/2.mp4", "storage": "supabase", "download_url": "https://<project>.supabase.co/storage/v1/object/sign/uploads/1/ad_20261120_0042/2.mp4?token=..." },
    { "scene_index": 3, "menu_index": null, "key": "1/ad_20261120_0042/3.mp4", "storage": "supabase", "download_url": "https://<project>.supabase.co/storage/v1/object/sign/uploads/1/ad_20261120_0042/3.mp4?token=..." }
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
  "hashtags": ["#창억떡", "#떡집", "#동네맛집"]
}
```

- `media`는 장면마다 영상 1개. 기능명세서의 "장면 번호, 메뉴 식별값"이 `scene_index`와 `menu_index`.
- `scenes`는 SC-06에서 확정한 자막 목록. `has_captions`가 false인 콘셉트(현재 없음)는 scenes와 caption·hashtags를 빼고 보내며, 이때 릴스 API가 캡션·해시태그를 새로 만든다.
- 자막이 max_chars를 넘으면 `400 CAPTION_TOO_LONG`, 장면 수가 메뉴 수 규칙과 다르면 `400 SCENE_MISMATCH`. 같은 `scene_index`가 두 번 들어와도 `SCENE_MISMATCH`.
- `ad_id`는 영문·숫자·`_`·`-`만, 최대 100자. 어기면 `400 INVALID_AD_ID`.
- 서명 URL 규칙 (어기면 `400 INVALID_RENDER_INPUT`):
  - 모든 URL은 서비스의 `SUPABASE_URL`과 같은 https 프로젝트 주소이고 `token` 쿼리가 있어야 한다.
  - `media[].download_url`은 `/storage/v1/object/sign/uploads/<key>` 형태이고, 그 `<key>`가 `media[].key`와 같아야 한다.
  - `uploads.video_url`은 `/storage/v1/object/upload/sign/uploads/.../generated/<ad_id>.mp4`, `uploads.thumbnail_url`은 같은 경로의 `.jpg`여야 한다.
  - 원본 영상과 결과 파일은 각각 최대 50MB.
- 서비스에 `SUPABASE_URL`이 설정돼 있으면 `uploads`는 필수다. `uploads` 없는 요청은 로컬 개발 전용이며, 이때는 `key`를 `STORAGE_ROOT` 밑 로컬 파일 경로로 읽는다.
- (미구현) 비어 있거나 읽을 수 없는 장면은 같은 메뉴의 다른 장면, 그다음 다른 장면 영상으로 대체하고 `fallbacks`에 기록한다. 지금은 대체하지 않고 실패 처리하며 `fallbacks`는 항상 빈 배열이다.

응답 (202 Accepted)

```json
{ "render_id": "rr_7f3a91c04b2e4d8f9a1b6c3e5d7f0a12", "ad_id": "ad_20261120_0042", "status": "queued" }
```

- `render_id`는 `rr_` + 32자리 16진수.
- 같은 ad_id가 이미 queued나 rendering이면 `409 ALREADY_RENDERING`과 기존 render_id를 돌려준다.
- `uploads`가 있는 요청에서 같은 ad_id가 이미 done이면 새로 렌더하지 않고 기존 render_id와 `"status": "done"`을 돌려준다. 백엔드가 같은 작업을 안전하게 다시 등록할 수 있게 하기 위해서다.
- SC-07의 "다시 시도"는 같은 요청을 다시 보낸다. 이전 작업이 failed면 새 render_id가 발급된다.
- 작업 상태는 서비스 프로세스 메모리에 있어서 재시작하면 사라진다. 이때 조회는 `404 NOT_FOUND`이며, 백엔드는 같은 요청을 다시 등록한다.

## 5. 생성 상태 조회

### GET /reels/renders/{render_id}

SC-07은 done 또는 failed가 될 때까지 2초 간격으로 조회한다. progress로 단계별 문구와 진행 표시를 보여주고, done이면 SC-08로 replace 이동한다.

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
    "video_key": "1/ad_20261120_0042/generated/ad_20261120_0042.mp4",
    "thumbnail_key": "1/ad_20261120_0042/generated/ad_20261120_0042.jpg",
    "video_bytes": 3145728,
    "thumbnail_bytes": 143360,
    "duration_sec": 13.0,
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
- label은 릴스 API가 내려주므로 문구 수정 시 프론트 배포가 필요 없다.

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

- 인증 오류는 error.code 없이 HTTP 상태로만 온다: `401`(키 불일치), `503`(서비스에 키 미설정).
- 원본 다운로드·업로드 실패는 서명 URL이 로그에 남지 않도록 상세 원인 없이 `RENDER_FAILED`로만 알린다.

error에는 code와 함께 안내 문구(message)를 넣는다.

### 상태 전달 (조회 우선, 콜백은 선택)

POSTALK 백엔드 연동은 **상태 조회 방식**이다. 백엔드가 `GET /reels/renders/{render_id}`를 주기적으로 호출해 생성 작업에 반영하고, 프론트는 백엔드의 진행률 API만 2~3초 간격으로 조회한다.

콜백 기능도 코드에 남아 있지만, 릴스 서비스에 `NEXT_BASE_URL`이 설정된 경우에만 보낸다(현재 연동에서는 설정하지 않음).

`POST {NEXT_BASE_URL}/api/internal/reels/callback` (헤더 `X-Internal-Api-Key: <REELS_CALLBACK_API_KEY>`)

- 본문은 상태 조회 응답과 같고, `sent_at`을 더한다.
- 받는 쪽은 render_id와 sent_at으로 중복·순서 뒤바뀜을 무시한다.

## 6. 확인 필요 사항

준서 (프론트)

- [ ] 메뉴당 2장면(closeup·detail) 규칙과 촬영 횟수(메뉴 3개면 촬영 7회 + 갤러리 1회) 괜찮은지
- [ ] 릴스 콘셉트 2종 모두 자막을 쓰므로, 릴스는 항상 SC-06을 거치는 것으로 봐도 되는지
- [ ] 가격 입력란 없이 메뉴 설명에서 가격을 뽑는 방식으로 충분한지
- [ ] 장면 구성 정의를 앱에 넣을지, GET /reels/templates로 받을지
- [ ] SC-06 "배경음악은 사용하지 않는다"를 "배경음악은 콘셉트 기본곡을 쓰며 선택 기능은 없다"로 고칠지

성준 (API·인프라)

- [x] 작업 등록 방식: 백엔드가 Railway 내부 주소로 직접 호출(SQS 없음)
- [x] 서버 간 인증: `x-reels-api-key` + `REELS_INTERNAL_API_KEY`. 상태는 콜백 대신 백엔드가 조회
- [x] 스토리지: S3가 아니라 Supabase Storage를 쓴다(2026-11-02 확인)
- [x] Supabase Storage: `uploads` 버킷, 서명 URL로만 주고받음, 결과는 `.../generated/<ad_id>.mp4|.jpg`
- [ ] user_id = 1 회원 정보의 가게 이름을 요청에 채워 보내는지
- [ ] 광고 삭제 시 Supabase Storage 결과 파일도 지울지
- [ ] 해커톤 FastAPI 코드의 기존 엔드포인트를 유지할지, 이 명세로 바꿀지

지우 (이미지 AI)

- [ ] 캡션·해시태그 생성을 카드뉴스와 공유할지
- [ ] 콘셉트 ID(simple, christmas)와 이름 통일, 콘셉트별 색·폰트 규칙(디자인 확정 시)

기획안 수정 필요

- 3.3절 "공통 장면 5개"는 메뉴 수에 따라 장면이 늘어나는 규칙으로 바뀐다.
- 3.1·3.3절의 콘셉트 3종(시장형·숏폼형·브이로그형)은 심플·크리스마스 2종으로 바뀐다.
- 두 콘셉트의 디자인(컷 속도, 전환, 자막, 색, BGM)은 팀에서 정한 뒤 템플릿 JSON에 반영한다.
- TTS 내레이션을 전제로 한 절(있다면)은 삭제한다 — 2026-11-02부로 TTS를 쓰지 않기로 했다. 영상 소리는 BGM뿐이다.
