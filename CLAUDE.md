# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 이 레포가 무엇이 되는가

이 레포의 기존 코드(`src/quote_video/`, 실제 영상 + 손으로 쓴 자막 대본 → ASS 자막 → FFmpeg 합성)를
**POSTALK 릴스 파이프라인**으로 개조한다. POSTALK는 2026 CO-SHOW 전시용 서비스로, 사용자가 음식·가게를
장면별로 촬영하면 AI가 인스타그램용 광고(카드뉴스·릴스)를 만들어 POSTALK 공식 인스타그램에 게시한다.

- 이 레포 담당: **릴스 파이프라인(아라)**. 입력을 받아 MP4·썸네일·캡션을 내놓기까지가 범위다.
- 범위 밖: 화면(준서, React PWA), Next.js API·Supabase 발급·인스타 게시(성준), 카드뉴스 이미지 AI(지우).
- 실행 위치: AWS EC2의 Docker 기반 **FastAPI + FFmpeg** 서비스. Next.js 백엔드가 작업을 등록하고,
  미디어 서버는 상태·결과를 콜백으로 돌려준다. 프론트는 이 서버를 직접 호출하지 않는다.

**API 계약의 원본은 `docs/reels-api-spec.md`다.** 엔드포인트, 필드 이름, 상태 값, 오류 코드는 그 문서를 따른다.
명세와 다르게 구현해야 할 이유가 생기면 코드를 먼저 바꾸지 말고 사용자에게 먼저 묻는다(프론트·백엔드가 이 명세에 맞춰 개발 중이다).

이전 CLAUDE.md는 `docs/legacy-CLAUDE.md`에 보관했다. 아래 "기존 코드에서 반드시 지킬 것"에 핵심을 옮겨 두었다.

## 목표 파이프라인

```
[대본] POST /reels/scripts  (정보 입력 직후, 촬영 중 백그라운드)
   가게·메뉴 정보 → LLM(JSON 구조화 출력) → 장면별 자막 + 캡션·해시태그
[생성] POST /reels/renders  (자막 확정 + 재료 업로드 후)
   Supabase Storage 영상 다운로드 → 정규화(1080x1920, 30fps, 9:16 중앙 크롭, 원본 오디오 제거)
   → 장면 길이 맞춤 → ASS 자막 → 장면 합성 + 전환(xfade) + BGM
   → MP4(H.264/AAC) + 썸네일 → Supabase Storage 업로드 → 콜백
```

### 확정된 결정 (사용자와 합의됨, 임의로 바꾸지 말 것)

- **생성형 영상 AI는 쓰지 않는다.** 템플릿 + FFmpeg만.
- **장면 구성은 메뉴 수에 따라 달라진다.** `hook`(가게, 촬영) → 메뉴마다 `closeup`·`detail`(촬영) → `outro`(가게, 갤러리 영상).
  메뉴 1/2/3개 → 장면 4/6/8개, 전체 13/14/15초. hook·outro는 2초, 메뉴 블록은 9/10/11초.
  **메뉴 블록 안에서 closeup/detail 배분은 2026-11-02부로 더 이상 무조건 균등 분할이 아니다** — 실제 촬영 길이에 비례해서
  배분하고(길게 찍은 장면에 더 많은 시간), 장면 하나가 배분 기준치의 0.5~2배를 벗어나지 않게만 막는다
  (`templates.rebalance_menu_scene_durations()`). 메뉴 자신의 총 예산과 전체 13/14/15초는 그대로 지켜진다 — 바뀌는 건
  "균등 분할이냐 비례 분할이냐"뿐이다.
- **콘셉트 2종**: `simple`(심플), `christmas`(크리스마스). 릴스·카드뉴스 공통.
  **두 콘셉트의 디자인(컷 속도, 전환, 자막 스타일, 색, 폰트, BGM 곡)은 아직 미정이다.** 템플릿 JSON 값으로만 표현하고
  코드에 콘셉트별 분기를 하드코딩하지 않는다. 디자인이 정해지기 전까지는 임시 값(기본 페이드, 흰 글씨+반투명 박스, 임의 BGM)으로 둔다.
  두 콘셉트 모두 **자막 사용(has_captions=true)**. `has_captions` 필드는 프론트 분기용으로 남겨 두되 false인 콘셉트는 현재 없다.
  **(2026-11-02 추가)** 장식 오버레이(테두리·스티커류)도 콘셉트별 선택 사항으로 지원한다 — `render.overlay.image`에 투명
  배경 PNG 경로를 넣으면 자막 번인 후·페이드 전에 합성된다(`video_composer.build_ffmpeg_command()`). `simple.json`은
  없음 — 지우가 실제 디자인 주면 콘셉트에 그 경로를 달면 된다.
  **(2026-11-03 christmas 전면 재설계)** "크리스마스 느낌이 약하고 산만하다"는 피드백으로 christmas만 다시 손봤다
  (simple은 안 건드림). 색 팔레트를 4색(레드 #B3001B/그린 #0F4D2E/골드 #F2C14E/크림 #FFF6E5)으로 통일하고,
  실사 촬영분엔 `render.color_grade`(따뜻한 톤+채도+비네팅, `video_composer._color_grade_filter()`)를 입혀서
  차갑고 산만해 보이던 걸 보정했다. 흩어진 스티커 테두리는 걷어내고 가랜드+오너먼트(흔들림)+전구(반짝임)+보케+
  3단 눈+하단 눈더미를 하나의 애니메이션 루프(`assets/overlay/christmas_scene.mov`, `render.animated_overlay`
  하나로 통합, 정적 `render.overlay`는 이제 안 씀)로 바꿨다. 자막은 한 글자씩 색 바꾸던 걸 걷어내고 크림 글씨+
  레드 굵은 외곽선+반투명 그림자+팝인 애니메이션으로(`subtitle_sync.build_ass()`의 `outline_color`/`bold`/
  `shadow`/`pop_in`). 1초짜리 오프닝/엔딩 타이틀 카드(`render.opening_card`/`closing_card`)도 추가 —
  본편 앞뒤로 붙고 BGM은 카드까지 안 끊고 이어진다. 이 장식 에셋들은 요청마다 만드는 게 아니라
  `scripts/generate_christmas_assets.py`(개발 시점 1회 실행, Pillow+ffmpeg)가 미리 구워서 `assets/overlay/`에
  올려두는 정적 파일이다 — BGM/폰트와 같은 취급. 진짜 디자인이 나오면 이 스크립트와 `christmas.json`의
  관련 블록만 고치면 된다.
- **TTS는 쓰지 않는다** (2026-11-02 결정, 이전 "TTS 사용" 결정을 뒤집음). 영상에는 음성 내레이션이 없고 자막과 BGM만 있다.
- **BGM은 쓴다.** 콘셉트별 기본곡 1곡, 사용자 선택 기능 없음. 덕킹 대상(TTS)이 없으므로 덕킹도 없다.
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
| 해상도 다르면 레터박스 | 9:16 중앙 크롭으로 꽉 채우기 |
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
- **(완료, 로컬 가정)** 업로드/다운로드·콜백 전송 — Supabase Storage 대신 `STORAGE_ROOT` 환경변수가 가리키는 로컬 디렉터리를 버킷처럼 쓴다
  (`media[].key`/`results/.../reel.mp4` 그대로 `STORAGE_ROOT / key`로 resolve). 콜백은 `NEXT_BASE_URL`이 설정된 경우에만 실제로 보내고,
  안 보내면 조용히 스킵(로컬 개발용). 둘 다 "아직 팀과 확정 안 된 것" 참고.
- **(완료)** 오류 코드: `CAPTION_TOO_LONG`, `SCENE_MISMATCH`, `ALREADY_RENDERING`, `NO_USABLE_MEDIA`, `RENDER_FAILED`, `TIMEOUT` 전부 구현.

## 작업 순서

1. **(완료)** 로컬 최소 파이프라인: 메뉴 1개짜리 테스트 영상 4개(hook, closeup, detail, outro) + 직접 쓴 자막 JSON
   → `templates/simple.json` 기반 13초 MP4. 순서: 템플릿 JSON 스키마 → 9:16 크롭 정규화 → 장면 길이 맞춤 → ASS 자막 → BGM.
   길이가 제각각(2초/5초/15초)인 영상과 가로 영상으로 테스트했다.
2. **(완료)** 메뉴 2·3개 장면 규칙, 전환 효과(xfade), 콘셉트 2종(`christmas.json` 추가)
3. **(완료)** LLM 대본 생성 (`script_generator.py`)
4. **(완료)** FastAPI 래핑(명세의 4개 엔드포인트 + 콜백), 상태·진행 단계, 실패 대체 (`api.py`, `job_store.py`) —
   업로드/결과 저장은 Supabase Storage가 아니라 로컬 `STORAGE_ROOT`를 그 버킷처럼 흉내만 냄. 작업 상태도 프로세스 재시작하면 날아가는 인메모리 dict.
5. **(다음)** Supabase Storage 연동(2026-11-02 결정: 백엔드가 Supabase Storage를 쓰기로 해서 S3 연동은 불필요해졌다 — 아래 참고),
   Docker, 속도 측정(목표 30초) — `STORAGE_ROOT`를 실제 Supabase Storage 업로드/다운로드로, 인메모리 `JobStore`를 영속 저장소로 교체

각 단계마다 unittest를 추가한다. 기존 테스트 중 폐기된 규칙(2초 컷, 컷당 자막 2줄, 현장음)에 묶인 것은 지우지 말고 새 규칙에 맞게 고치거나, 지워야 하면 사용자에게 먼저 알린다.

## 아직 팀과 확정 안 된 것 (가정으로 구현하되, 바꾸기 쉽게)

- 작업 등록 방식: Next.js → 미디어 서버 직접 호출 vs SQS — 지금 구현은 직접 호출(FastAPI 엔드포인트) 전제
- 콜백 경로·서버 간 인증 방식 — 구현은 `POST {NEXT_BASE_URL}/api/internal/reels/callback` + 헤더 `X-Internal-Api-Key`로
  가정하고 `job_store.send_callback()`에 이미 짜 놓았다. `NEXT_BASE_URL`/`REELS_CALLBACK_API_KEY` 환경변수로만 바뀐다 — 실제
  경로·헤더 이름이 확정되면 그 함수 하나만 고치면 된다. `NEXT_BASE_URL` 미설정 시 콜백은 조용히 스킵(로컬 개발 지원용).
- **S3는 안 쓴다 (2026-11-02 결정)** — 백엔드가 Supabase Storage를 쓰기로 해서 S3 연동 자체가 필요 없어졌다.
  `docs/reels-api-spec.md`의 "파일은 S3 객체 키로 주고받는다"도 Supabase Storage 기준으로 고쳤다.
- Supabase Storage 버킷 이름·경로 규칙 — 아직 미정. 지금 구현은 `uploads/{user_id}/{ad_id}/{scene_index}.mp4`,
  `results/{user_id}/{ad_id}/reel.mp4|thumb.jpg` 가정대로 키(경로)를 만들지만, 실제 Supabase Storage가 아니라
  `STORAGE_ROOT`(환경변수, 기본값 `./storage`) 밑 로컬 파일로 읽고 쓴다. `media[].storage` 필드는 받기만 하고 무시한다 —
  Supabase Storage 연동(5단계)에서 `api.py`의 파일 읽기/쓰기 지점만 Supabase 클라이언트 호출로 바꾸면 된다.
- 작업 상태 저장소 — 지금은 `job_store.JobStore`가 프로세스 메모리의 dict다. 재시작하면 모든 진행 중 작업이 사라지고,
  워커를 여러 개 띄우면 서로 다른 메모리를 본다(단일 워커 가정). Docker/다중 워커로 갈 때(5·6단계) Supabase나 Redis 같은
  공유 저장소로 바꿔야 한다.
- 메뉴당 2장면 규칙에 대한 프론트 최종 동의
- LLM 제공자 — 지금은 `script_generator.AnthropicLlmClient`(표준 라이브러리 `urllib`만 사용, SDK 의존성 없음)가 기본값이지만 placeholder다.
  `LlmClient` 프로토콜만 맞추면 다른 제공자로 교체 가능(`generate_script(..., llm_client=...)`), `ANTHROPIC_API_KEY`/`LLM_MODEL` 환경변수로 키·모델 교체.
- 심플·크리스마스 콘셉트의 디자인 전체

## 명령어
```bash
pip install -r requirements.txt           # fastapi, pydantic, uvicorn, httpx (stdlib 외 첫 의존성, 4단계부터)
python -m unittest discover -s tests -v    # 테스트 (stdlib unittest + FastAPI TestClient)

# FastAPI 서버 (docs/reels-api-spec.md의 4개 엔드포인트 + /healthz)
python -m uvicorn src.quote_video.api:app --reload

# CLI (POST /reels/renders 본문을 JSON 파일로 저장해 로컬 테스트, 서버 없이 한 번 렌더)
python -m src.quote_video.pipeline --request tests/fixtures/render_request_1menu.json --media-root assets/videos --output output/reel.mp4
```

개발 환경은 Windows(winget으로 설치한 FFmpeg), 배포는 Linux Docker다. 경로는 `pathlib`로 다루고 OS 의존 코드를 넣지 않는다.
API 키(LLM, Supabase)는 `.env`로만 받고 커밋하지 않는다. 서버 환경변수: `STORAGE_ROOT`(기본 `./storage`), `NEXT_BASE_URL`·
`REELS_CALLBACK_API_KEY`(콜백, 미설정 시 스킵), `ANTHROPIC_API_KEY`·`LLM_MODEL`(대본 생성).
