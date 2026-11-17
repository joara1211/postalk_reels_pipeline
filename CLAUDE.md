# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 이 레포가 무엇이 되는가

이 레포의 기존 코드(`src/quote_video/`, 실제 영상 + 손으로 쓴 자막 대본 → ASS 자막 → FFmpeg 합성)를
**POSTALK 릴스 파이프라인**으로 개조한다. POSTALK는 2026 CO-SHOW 전시용 서비스로, 사용자가 음식·가게를
장면별로 촬영하면 AI가 인스타그램용 광고(카드뉴스·릴스)를 만들어 POSTALK 공식 인스타그램에 게시한다.

- 이 레포 담당: **릴스 파이프라인(아라)**. 입력을 받아 MP4·썸네일·캡션을 내놓기까지가 범위다.
- 범위 밖: 화면(준서, React PWA), Next.js API·Supabase 발급·인스타 게시(성준), 카드뉴스 이미지 AI(지우).
- 실행 위치: **Railway**의 Docker 기반 **FastAPI + FFmpeg** 내부 서비스(공개 도메인 없음, `*.railway.internal` 주소).
  POSTALK 백엔드가 `x-reels-api-key` 헤더로 작업을 등록하고 `GET /reels/renders/{id}`를 **조회**해서 결과를 가져간다
  (콜백은 선택 기능, 현재 연동에선 안 씀). 프론트는 이 서버를 직접 호출하지 않는다.
  배포·연동 절차는 `docs/backend-integration.md`, `docs/deployment.md`(백엔드 팀원 작성).

**API 계약의 원본은 `docs/reels-api-spec.md`다.** 엔드포인트, 필드 이름, 상태 값, 오류 코드는 그 문서를 따른다.
명세와 다르게 구현해야 할 이유가 생기면 코드를 먼저 바꾸지 말고 사용자에게 먼저 묻는다(프론트·백엔드가 이 명세에 맞춰 개발 중이다).

이전 CLAUDE.md는 `docs/legacy-CLAUDE.md`에 보관했다. 아래 "기존 코드에서 반드시 지킬 것"에 핵심을 옮겨 두었다.

## 목표 파이프라인

```
[대본] POST /reels/scripts  (정보 입력 직후, 촬영 중 백그라운드)
   가게·메뉴 정보 → LLM(JSON 구조화 출력) → 장면별 자막 + 캡션·해시태그
[생성] POST /reels/renders  (자막 확정 + 재료 업로드 후)
   서명 URL로 Supabase Storage 영상을 임시 폴더에 다운로드 → 정규화(1080x1920, 30fps, 9:16 중앙 크롭, 원본 오디오 제거)
   → 장면 길이 맞춤 → ASS 자막 → 장면 합성 + 전환(xfade) + BGM
   → MP4(H.264/AAC) + 썸네일 → 서명 업로드 URL로 Supabase Storage에 PUT → 백엔드가 상태 조회로 확인
```

### 확정된 결정 (사용자와 합의됨, 임의로 바꾸지 말 것)

- **생성형 영상 AI는 쓰지 않는다.** 템플릿 + FFmpeg만.
- **장면 구성은 메뉴 수에 따라 달라진다.** `hook`(가게, 촬영) → 메뉴마다 `closeup`·`detail`(촬영) → `outro`(가게, 갤러리 영상).
  메뉴 1/2/3개 → 장면 4/6/8개, 전체 **16/20/24초**. hook·outro는 3초, 메뉴 블록은 10/14/18초(메뉴 장면 5/3.5/3초).
  **(2026-11-16 사용자 결정, 이전 13/14/15초·hook/outro 2초)** 자막이 너무 짧다는 피드백으로 글자 수 한도를 hook 20·closeup 18·
  detail 24·outro 20자(2줄 자막)로 늘리고, 그걸 읽을 시간을 위해 영상도 늘렸다. 프론트·백엔드에 알려야 함(아래 "팀과 맞출 것").
  **메뉴 블록 안에서 closeup/detail 배분은 2026-11-02부로 더 이상 무조건 균등 분할이 아니다** — 실제 촬영 길이에 비례해서
  배분하고(길게 찍은 장면에 더 많은 시간), 장면 하나가 배분 기준치의 0.5~2배를 벗어나지 않게만 막는다
  (`templates.rebalance_menu_scene_durations()`). 메뉴 자신의 총 예산과 전체 16/20/24초는 그대로 지켜진다 — 바뀌는 건
  "균등 분할이냐 비례 분할이냐"뿐이다.
- **콘셉트 2종**: `simple`(심플), `christmas`(크리스마스). 릴스·카드뉴스 공통.
  **두 콘셉트의 디자인(컷 속도, 전환, 자막 스타일, 색, 폰트, BGM 곡)은 아직 미정이다.** 템플릿 JSON 값으로만 표현하고
  코드에 콘셉트별 분기를 하드코딩하지 않는다. 디자인이 정해지기 전까지는 임시 값(기본 페이드, 흰 글씨+반투명 박스, 임의 BGM)으로 둔다.
  두 콘셉트 모두 **자막 사용(has_captions=true)**. `has_captions` 필드는 프론트 분기용으로 남겨 두되 false인 콘셉트는 현재 없다.
  **(2026-11-02 추가)** 장식 오버레이(테두리·스티커류)도 콘셉트별 선택 사항으로 지원한다 — `render.overlay.image`에 투명
  배경 PNG 경로를 넣으면 자막 번인 후·페이드 전에 합성된다(`video_composer.build_ffmpeg_command()`). `christmas.json`에
  `assets/overlay/christmas_border_placeholder.png`(생성한 임시 테두리)가 걸려있고, `simple.json`은 없음 — 지우가 실제
  디자인 주면 그 경로만 바꾸면 된다.
- **TTS는 쓰지 않는다** (2026-11-02 결정, 이전 "TTS 사용" 결정을 뒤집음). 영상에는 음성 내레이션이 없고 자막과 BGM만 있다.
- **BGM은 쓴다.** 사용자 선택 기능 없음. 덕킹 대상(TTS)이 없으므로 덕킹도 없다.
  **(2026-11-17)** 콘셉트별 곡 목록 `audio.bgm_tracks`에서 렌더마다 무작위 1곡(`pipeline.pick_bgm_path()`) — 심플 4곡(기존 맑은 아침 + CC0 3곡),
  크리스마스 3곡(CC0). 새 곡은 원곡 앞 40초를 잘라 약 -17 LUFS로 맞춘 MP3. **곡은 CC0·퍼블릭 도메인만** 넣고 출처를 `assets/bgm/CREDITS.md`에 적는다
  (공개 레포 + 공식 인스타 게시). Wikimedia 파일 서버(upload.wikimedia.org)는 연락처 없는 자동 다운로드를 429로 막으므로 Internet Archive에서 받았다.
- **촬영 원본 소리는 모두 제거**한다(기존 코드의 "현장음 유지" 규칙은 폐기).
- **자막은 화면 표시용 텍스트**(내레이션 대본이 아니다). 장면당 자막 1개, 장면별 최대 글자 수 제한.
- **가격 입력란은 없다.** 메뉴 설명에서 LLM이 가격을 뽑아 detail 장면에 넣고, 없으면 생략.
- **사진 움직임 효과(켄 번즈)는 기본 기능에서 제외.** 입력은 영상이다.
- **자막은 우선 전부 ASS로 만든다**(BorderStyle=3으로 박스형 가능). Pillow PNG 자막은 둥근 박스·스티커가 꼭 필요해질 때만 추가.
- 템플릿은 코드와 분리된 **JSON 파일**(`templates/<concept>.json`). 콘셉트 추가·수정 시 코드를 바꾸지 않는다.
- 렌더 목표 **30초 이내**, 하드 타임아웃 **60초**(초과 시 `TIMEOUT`으로 실패 처리).
- 사용자는 고정 계정 `user_id = 1`. 가게 이름은 Next.js가 회원 정보에서 채워 보낸다.

### 실패 대체 (한 단계가 실패해도 가능하면 완성)

- **(완료)** LLM 실패: 1회 재시도 → 템플릿 기본 문구 + 가게명·메뉴명으로 채우고 `is_fallback: true`
- 장면 영상 없음/읽기 실패: 같은 메뉴의 다른 장면 → 다른 장면 영상의 다른 구간으로 대체, `fallbacks`에 기록 —
  **아직 미구현.** 지금은 장면 미디어가 없으면 바로 `NO_USABLE_MEDIA`로 실패 처리하고, `result.fallbacks`는 항상 빈 배열이다.
- **(완료)** FFmpeg 실패: 1회 자동 재시도 → `RENDER_FAILED` (`api._run_render_with_one_retry()`)
- **(완료)** 쓸 수 있는 영상이 하나도 없음: `NO_USABLE_MEDIA`

## 기존 코드에서 반드시 지킬 것 (시행착오로 얻은 것)

- **자막은 `.srt`가 아니라 `.ass`, 헤더에 `PlayResX`/`PlayResY`를 영상 크기로 지정.** SRT를 libass로 태우면
  기본 해상도(384x288) 기준으로 MarginV 등이 ~6.67배 틀어진다. 위치를 다시 검증할 땐 실제 영상 말고 단색 테스트 클립으로 픽셀 위치를 확인한다.
- **폰트 `.woff`/`.woff2`는 `fontTools`로 `.ttf` 변환** 후 사용(libass가 woff를 못 읽음).
  ```python
  from fontTools.ttLib import TTFont
  f = TTFont("assets/font/Name.woff"); f.flavor = None; f.save("assets/font/Name.ttf")
  ```
- **BGM 음량은 기준 트랙의 실측 음량 대비로 보정하는 게 원칙**이었다(`probe_mean_volume_db()`(volumedetect) + `compute_bgm_gain_db()`,
  `amix ... normalize=0` + `alimiter`) — 현장음이 기준이었다가, TTS를 기준으로 바꿀 계획이었다. **TTS를 쓰지 않기로 하면서(2026-11-02) 비교할 기준 트랙 자체가 없어졌다.**
  지금 `compose_video()`는 이 상대 보정을 호출하지 않고 BGM 자체 raw 레벨 대비 절대 비율(`bgm_volume`)만 적용한다.
  `probe_mean_volume_db()`/`compute_bgm_gain_db()`는 나중에 기준 트랙이 다시 생기면 쓰려고 코드에 남겨뒀을 뿐 지금은 호출되지 않는다.
- `-preset veryfast` 유지(8클립/16초 기준 4~5초 측정).
- 파이프라인 전체에 **하나의 공유 데드라인**을 넘겨 모든 ffprobe/ffmpeg 호출에 남은 시간을 적용(`PipelineTimeoutError`). 값만 config에서 60초로.
- `subprocess.run(..., text=True)`에는 `encoding="utf-8", errors="replace"`(Windows cp949 콘솔 대응).
- 설정값은 `config.py`(→ 앞으로는 템플릿 JSON)에 모으고 인라인 하드코딩 금지.

## 기존 코드에서 바꿀 것

| 지금 | 바꿀 방향 |
|---|---|
| `CUT_DURATION`=2초, `CAPTION_DURATION`=1초, 컷당 자막 2줄 고정 | 템플릿 JSON 기반 장면별 길이, 장면당 자막 1개, 메뉴 수에 따른 장면 수 |
| 해상도 다르면 레터박스 | 세로 영상은 9:16 중앙 크롭으로 꽉 채우기. **가로 영상은 (2026-11-18 사용자 결정) 전체 화면을 가운데에 두고 위아래를 같은 영상의 흐린 사본으로 채움** — 중앙 크롭하면 폭의 1/3만 남아 너무 확대돼 보여서. 템플릿 `render.landscape_fit`(`mode: crop|blur`, 선택 `foreground_aspect`), `video_composer._frame_filter()` |
| 짧은 클립은 `tpad` 정지 프레임 | 0.5배속 슬로우 → 반복 → (최후) 정지 프레임 |
| 긴 클립은 앞부분 사용 | 앞뒤 흔들림 피해 중간 구간 사용 |
| 마크다운 대본 파서 입력 | API JSON 장면 목록 입력(파서는 테스트용으로만 유지) |
| 현장음 0.7 + BGM 20% | 원본 오디오 제거, BGM만 사용(TTS 없음) |
| 클립 concat 후 합성(2회 인코딩) | xfade 전환을 위해 하나의 filter_complex로 합성 |
| CLI 전용 | FastAPI 서비스(작업·상태·진행 단계·콜백). CLI는 로컬 테스트용 유지 |
| 타임아웃 20초 | 하드 60초, 목표 30초 |

## 새로 만들 것

- **(완료)** LLM 대본 생성 로직(`script_generator.generate_script()`) + `POST /reels/scripts`/`GET /reels/scripts/{id}` 엔드포인트(`api.py`, 백그라운드 처리).
- **(완료)** 콘셉트별 전환(xfade) — 자막 스타일 자체는 디자인 대기 중(JSON 값으로 교체 가능하게 되어 있음)
- **(완료)** 썸네일(hook 장면에서 1080x1920 JPG) — 원본 훅 영상이 아니라 합성 끝난 최종 MP4 초반 프레임에서 뽑는다(`video_composer.generate_thumbnail()`), 이미 크롭된 상태라 추가 스케일링이 필요 없어서.
- **(완료)** 진행 단계 보고: `preparing → composing → uploading` (TTS 없으므로 narrating 단계 자체가 없다), 한국어 label 포함.
- **(완료, 백엔드 팀원 작업)** Supabase Storage 연동(`storage.py`) — 릴스 서비스는 Supabase 비밀 키 없이, 백엔드가 준
  서명 URL(`media[].download_url`, `uploads.video_url`/`thumbnail_url`)로만 내려받고 올린다. `signed_object_key()`가
  URL이 `SUPABASE_URL` 프로젝트의 `uploads` 버킷 서명 URL인지, `key`와 일치하는지, 결과가 `.../generated/<ad_id>.mp4|.jpg`인지 검사한다.
  파일당 50MB 제한. 다운로드·업로드 오류는 서명 URL이 로그에 남지 않게 예외 종류만 로그하고 `RENDER_FAILED`로 처리.
  `uploads`가 없는 요청은 **로컬 개발 전용**으로 예전처럼 `STORAGE_ROOT / key`를 읽고 쓴다(`SUPABASE_URL`이 설정돼 있으면 거절).
- **(완료)** 서버 간 인증 — `/reels/*`는 `x-reels-api-key` 헤더 = `REELS_INTERNAL_API_KEY`(`api.require_internal_auth()`).
  `SUPABASE_URL`도 키도 없으면 인증 생략(로컬), `SUPABASE_URL`만 있으면 503.
- **(완료)** 콜백 전송 코드(`job_store.send_callback()`) — `NEXT_BASE_URL` 설정 시에만 보낸다. 현재 백엔드 연동은 조회 방식이라 안 씀.
- **(완료)** 오류 코드: `CAPTION_TOO_LONG`, `SCENE_MISMATCH`, `ALREADY_RENDERING`, `NO_USABLE_MEDIA`, `RENDER_FAILED`, `TIMEOUT`,
  `INVALID_CONCEPT`, `INVALID_MENU_COUNT`, `INVALID_AD_ID`, `INVALID_RENDER_INPUT`, `NOT_FOUND`.
- **(완료)** `Dockerfile`(python:3.11-slim + ffmpeg + fonts-noto-cjk, 비루트 사용자), `.dockerignore`, `.env.example`.

## 작업 순서

1. **(완료)** 로컬 최소 파이프라인: 메뉴 1개짜리 테스트 영상 4개(hook, closeup, detail, outro) + 직접 쓴 자막 JSON
   → `templates/simple.json` 기반 13초 MP4. 순서: 템플릿 JSON 스키마 → 9:16 크롭 정규화 → 장면 길이 맞춤 → ASS 자막 → BGM.
   길이가 제각각(2초/5초/15초)인 영상과 가로 영상으로 테스트했다.
2. **(완료)** 메뉴 2·3개 장면 규칙, 전환 효과(xfade), 콘셉트 2종(`christmas.json` 추가)
3. **(완료)** LLM 대본 생성 (`script_generator.py`)
4. **(완료)** FastAPI 래핑(명세의 4개 엔드포인트 + 콜백), 상태·진행 단계, 실패 대체 (`api.py`, `job_store.py`) —
   업로드/결과 저장은 Supabase Storage가 아니라 로컬 `STORAGE_ROOT`를 그 버킷처럼 흉내만 냄. 작업 상태도 프로세스 재시작하면 날아가는 인메모리 dict.
5. **(완료, 백엔드 팀원이 `Superkid0714/postalk_reels_pipeline`에서 작업 → 병합)** Supabase Storage 서명 URL 연동, 내부 인증,
   Dockerfile, Railway 배포 문서.
6. **(다음)** Railway 실배포 후 속도 측정(목표 30초), 장면 영상 대체(`fallbacks`) 구현, 필요하면 인메모리 `JobStore`를 영속 저장소로 교체

각 단계마다 unittest를 추가한다. 기존 테스트 중 폐기된 규칙(2초 컷, 컷당 자막 2줄, 현장음)에 묶인 것은 지우지 말고 새 규칙에 맞게 고치거나, 지워야 하면 사용자에게 먼저 알린다.

## 팀과 맞출 것

### 백엔드와 확정된 것 (2026-11 병합으로 확정)

- 작업 등록: 백엔드 → 릴스 서비스 Railway 내부 주소 직접 호출(SQS 없음). 인증 헤더 `x-reels-api-key`.
- 상태 전달: 백엔드가 `GET /reels/renders/{id}`로 조회. 콜백은 안 씀(코드는 남아 있고 `NEXT_BASE_URL` 설정 시에만 동작).
- 스토리지: S3 아니고 Supabase Storage `uploads` 버킷, 서명 URL로만 주고받음. 결과 경로는 백엔드가 정함(`.../generated/<ad_id>.mp4|.jpg`).
- 작업 상태 저장소: 인메모리 `JobStore` 그대로 두고, 재시작으로 잃은 작업은 백엔드가 같은 요청을 다시 등록한다.
  `uploads` 있는 요청은 같은 `ad_id`가 이미 done이면 재렌더 없이 기존 결과를 돌려준다(`create_render_if_not_active(reuse_completed=True)`).
  단일 프로세스 가정이므로 워커를 여러 개 띄우면 안 된다.
- (2026-11-16 병합) 백엔드는 메뉴 1~3개(영상 4·6·8개)를 보낸다. 릴스 서비스가 자막·캡션·해시태그를 직접 생성한다.
- (2026-11-16 병합) 운영 LLM은 **OpenAI**(백엔드와 같은 제공자). Railway에서 `OPENAI_API_KEY`를 백엔드 변수 참조로 연결, 기본 모델 `gpt-4.1-mini`.
- 협업 방식: 팀원 포크(`superkid` 리모트 = `Superkid0714/postalk_reels_pipeline`)가 Railway 배포 원본이다.
  내 레포(`reels` 리모트)에서 작업 → 팀원 레포로 PR. **팀원 레포에는 `CLAUDE.md`·`docs/legacy-CLAUDE.md`를 보내지 않는다**
  (팀원이 삭제함. 내 레포에만 유지 — PR 브랜치에서 두 파일을 빼고 보낸다).

### 아직 미정

- **(2026-11-16) 영상 길이 16/20/24초·자막 한도 18~24자 변경을 프론트·백엔드에 공유**(프론트 글자 수 표시는 `GET /reels/templates`의 `caption_max_chars`를 읽으면 자동 반영).

- 메뉴당 2장면 규칙에 대한 프론트 최종 동의
- LLM 제공자 구현 — `script_generator`에 세 가지, `default_llm_client()`가 고른다: `LLM_PROVIDER`(`openai`|`anthropic`|`gemini`)가
  있으면 그걸, 없으면 키가 있는 첫 번째를 OpenAI → Anthropic → Gemini 순으로.
  `OpenAiLlmClient`(stdlib HTTP, Responses API, strict JSON 스키마, 모델 `OPENAI_MODEL` → `LLM_MODEL` → `gpt-4.1-mini`),
  `AnthropicLlmClient`(공식 `anthropic` SDK, 기본 `claude-opus-5-5`, effort `medium`, 구조화 출력 + `fallbacks: "default"`),
  `GeminiLlmClient`(공식 `google-genai` SDK, 기본 `gemini-2.5-flash`). Anthropic·Gemini는 로컬 테스트용 대안.
  `LlmClient` 프로토콜만 맞추면 다른 제공자도 추가 가능(`generate_script(..., llm_client=...)`).

### 자막(대본) 생성 규칙 (2026-11-15 개편)

- 입력은 화면 "무엇을 알릴까요?" 그대로: 가게 이름, 가게 설명("어떤 가게인가요?"), 메뉴 1~3개(이름 + 자유 설명, 설명은 비어 있을 수 있음).
- 자막은 공백 포함 `caption_max_chars` 이하. 넘친 장면이 있으면 **어느 장면이 몇 자 넘었는지 알려주고 1회 재요청**,
  그래도 넘치면 **그 장면만** 사용자 입력 기반 기본 문구로 바꾼다(`is_fallback`은 false 유지). 전체 기본 문구(`is_fallback: true`)는
  LLM 결과가 아예 없을 때만(키 없음, 네트워크 오류, JSON 2회 실패).
- 자막에서 이모지·특수기호는 제거한다(자막 폰트에 글리프가 없어 빈 네모로 나옴). 해시태그는 `#` + 띄어쓰기 없음, 중복 제거, 30개 이하.
- 자막은 해요체 존댓말(반말 금지), 캡션도 해요체로 통일. 장면마다 한도의 약 75~100%(`_recommended_min_chars()`)를 채우도록 프롬프트에서 요청.
- 기본 문구: hook=가게명, closeup=메뉴명, detail=메뉴명+가격 → 가격 → 설명 첫 구절, outro=`{가게명}에서 만나요`. 캡션은 가게·메뉴 설명으로 구성.
- 심플·크리스마스 콘셉트의 디자인 전체

## 명령어
```bash
pip install -r requirements.txt           # fastapi, pydantic, uvicorn, httpx, anthropic, google-genai
python -m unittest discover -s tests -v    # 테스트 (stdlib unittest + FastAPI TestClient)

# FastAPI 서버 (docs/reels-api-spec.md의 4개 엔드포인트 + /healthz)
python -m uvicorn src.quote_video.api:app --reload

# CLI (POST /reels/renders 본문을 JSON 파일로 저장해 로컬 테스트, 서버 없이 한 번 렌더)
python -m src.quote_video.pipeline --request tests/fixtures/render_request_1menu.json --media-root assets/videos --output output/reel.mp4
```

개발 환경은 Windows(winget으로 설치한 FFmpeg), 배포는 Railway의 Linux Docker다. 경로는 `pathlib`로 다루고 OS 의존 코드를 넣지 않는다.
비밀값은 환경변수로만 받고 커밋하지 않는다(`.env`는 gitignore, 키 이름 목록은 `.env.example`). 코드는 `.env`를 자동으로 읽지 않으므로
로컬에선 `uvicorn ... --env-file .env`, Docker에선 `--env-file`/Railway 변수로 넣는다. 서명 URL·내부 키는 로그·문서·PR에 남기지 않는다.

서버 환경변수:
- 운영 필수: `SUPABASE_URL`(서명 URL 검증용), `REELS_INTERNAL_API_KEY`(백엔드와 공유), `PORT`(기본 8000). Dockerfile은 앞의 두 개가 없으면 기동하지 않는다.
- 자막 AI: `OPENAI_API_KEY`(운영), `OPENAI_MODEL`. 없으면 기본 문구로 대체(`is_fallback: true`).
- 선택: `ANTHROPIC_API_KEY`·`GEMINI_API_KEY`·`LLM_PROVIDER`·`LLM_MODEL`(로컬 테스트용 대체 제공자),
  `NEXT_BASE_URL`·`REELS_CALLBACK_API_KEY`(콜백, 미설정 시 스킵), `STORAGE_ROOT`(로컬 개발용, 기본 `./storage`).
