# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 이 레포가 무엇이 되는가

이 레포의 기존 코드(`src/quote_video/`, 실제 영상 + 손으로 쓴 자막 대본 → ASS 자막 → FFmpeg 합성)를
**POSTALK 릴스 파이프라인**으로 개조한다. POSTALK는 2026 CO-SHOW 전시용 서비스로, 사용자가 음식·가게를
장면별로 촬영하면 AI가 인스타그램용 광고(카드뉴스·릴스)를 만들어 POSTALK 공식 인스타그램에 게시한다.

- 이 레포 담당: **릴스 파이프라인(아라)**. 입력을 받아 MP4·썸네일·캡션을 내놓기까지가 범위다.
- 범위 밖: 화면(준서, React PWA), Next.js API·Supabase·S3 발급·인스타 게시(성준), 카드뉴스 이미지 AI(지우).
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
   S3 영상 다운로드 → 정규화(1080x1920, 30fps, 9:16 중앙 크롭, 원본 오디오 제거)
   → 장면 길이 맞춤 → TTS 생성 → ASS 자막 → 장면 합성 + 전환(xfade) + BGM(덕킹) + TTS
   → MP4(H.264/AAC) + 썸네일 → S3 업로드 → 콜백
```

### 확정된 결정 (사용자와 합의됨, 임의로 바꾸지 말 것)

- **생성형 영상 AI는 쓰지 않는다.** 템플릿 + FFmpeg만.
- **장면 구성은 메뉴 수에 따라 달라진다.** `hook`(가게, 촬영) → 메뉴마다 `closeup`·`detail`(촬영) → `outro`(가게, 갤러리 영상).
  메뉴 1/2/3개 → 장면 4/6/8개, 전체 13/14/15초. hook·outro는 2초, 메뉴 블록은 9/10/11초를 메뉴 장면 수로 나눈다.
- **콘셉트 2종**: `simple`(심플), `christmas`(크리스마스). 릴스·카드뉴스 공통.
  **두 콘셉트의 디자인(컷 속도, 전환, 자막 스타일, 색, 폰트, BGM 곡)은 아직 미정이다.** 템플릿 JSON 값으로만 표현하고
  코드에 콘셉트별 분기를 하드코딩하지 않는다. 디자인이 정해지기 전까지는 임시 값(기본 페이드, 흰 글씨+반투명 박스, 임의 BGM)으로 둔다.
  두 콘셉트 모두 **자막 사용(has_captions=true), TTS 사용**. `has_captions` 필드는 프론트 분기용으로 남겨 두되 false인 콘셉트는 현재 없다.
- **BGM은 쓴다.** 콘셉트별 기본곡 1곡, 사용자 선택 기능 없음. TTS가 나오는 동안 BGM 자동 덕킹.
- **촬영 원본 소리는 모두 제거**한다(기존 코드의 "현장음 유지" 규칙은 폐기).
- **자막 텍스트 = TTS 내레이션 텍스트.** 장면당 자막 1개, 장면별 최대 글자 수 제한.
- **가격 입력란은 없다.** 메뉴 설명에서 LLM이 가격을 뽑아 detail 장면에 넣고, 없으면 생략.
- **사진 움직임 효과(켄 번즈)는 기본 기능에서 제외.** 입력은 영상이다.
- **자막은 우선 전부 ASS로 만든다**(BorderStyle=3으로 박스형 가능). Pillow PNG 자막은 둥근 박스·스티커가 꼭 필요해질 때만 추가.
- 템플릿은 코드와 분리된 **JSON 파일**(`templates/<concept>.json`). 콘셉트 추가·수정 시 코드를 바꾸지 않는다.
- 렌더 목표 **30초 이내**, 하드 타임아웃 **60초**(초과 시 `TIMEOUT`으로 실패 처리).
- 사용자는 고정 계정 `user_id = 1`. 가게 이름은 Next.js가 회원 정보에서 채워 보낸다.

### 실패 대체 (한 단계가 실패해도 가능하면 완성)

- LLM 실패: 1회 재시도 → 템플릿 기본 문구 + 가게명·메뉴명으로 채우고 `is_fallback: true`
- 장면 영상 없음/읽기 실패: 같은 메뉴의 다른 장면 → 다른 장면 영상의 다른 구간으로 대체, `fallbacks`에 기록
- TTS 실패: 자막 + BGM만으로 진행, `fallbacks`에 기록
- FFmpeg 실패: 1회 자동 재시도 → `RENDER_FAILED`
- 쓸 수 있는 영상이 하나도 없음: `NO_USABLE_MEDIA`

## 기존 코드에서 반드시 지킬 것 (시행착오로 얻은 것)

- **자막은 `.srt`가 아니라 `.ass`, 헤더에 `PlayResX`/`PlayResY`를 영상 크기로 지정.** SRT를 libass로 태우면
  기본 해상도(384x288) 기준으로 MarginV 등이 ~6.67배 틀어진다. 위치를 다시 검증할 땐 실제 영상 말고 단색 테스트 클립으로 픽셀 위치를 확인한다.
- **폰트 `.woff`/`.woff2`는 `fontTools`로 `.ttf` 변환** 후 사용(libass가 woff를 못 읽음).
  ```python
  from fontTools.ttLib import TTFont
  f = TTFont("assets/font/Name.woff"); f.flavor = None; f.save("assets/font/Name.ttf")
  ```
- **BGM 음량은 기준 트랙의 실측 음량 대비로 보정**: `probe_mean_volume_db()`(volumedetect) + `compute_bgm_gain_db()`,
  `amix ... normalize=0` + `alimiter`. 기준 트랙이 현장음 → **TTS**로 바뀐다.
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
| 현장음 0.7 + BGM 20% | 원본 오디오 제거, BGM + TTS, TTS 구간 BGM 덕킹(sidechaincompress) |
| 클립 concat 후 합성(2회 인코딩) | xfade 전환을 위해 하나의 filter_complex로 합성 |
| CLI 전용 | FastAPI 서비스(작업·상태·진행 단계·콜백). CLI는 로컬 테스트용 유지 |
| 타임아웃 20초 | 하드 60초, 목표 30초 |

## 새로 만들 것

- LLM 대본 생성(`/reels/scripts`): 장면별 자막, 캡션(≤2,200자), 해시태그(≤30개), 가격 추출
- TTS 생성 및 장면 길이와 맞추기
- 콘셉트별 전환(xfade)·자막 스타일 (2종, 디자인 미정이므로 JSON 값으로 교체 가능하게)
- 썸네일(hook 장면에서 1080x1920 JPG)
- 진행 단계 보고: `preparing → narrating → composing → uploading` (TTS 없으면 narrating 생략), 한국어 label 포함
- S3 다운로드/업로드, Next.js 콜백 전송
- 오류 코드: `CAPTION_TOO_LONG`, `SCENE_MISMATCH`, `ALREADY_RENDERING`, `NO_USABLE_MEDIA`, `RENDER_FAILED`, `TIMEOUT`

## 작업 순서

1. **(지금) 로컬 최소 파이프라인**: 메뉴 1개짜리 테스트 영상 4개(hook, closeup, detail, outro) + 직접 쓴 자막 JSON
   → `templates/simple.json` 기반 13초 MP4. 순서: 템플릿 JSON 스키마 → 9:16 크롭 정규화 → 장면 길이 맞춤 → ASS 자막 → BGM.
   길이가 제각각(2초/5초/15초)인 영상과 가로 영상으로 테스트한다.
2. 메뉴 2·3개 장면 규칙, 전환 효과, 콘셉트 2종(`christmas.json` 추가)
3. TTS + 덕킹
4. LLM 대본 생성
5. FastAPI 래핑(명세의 4개 엔드포인트 + 콜백), 상태·진행 단계, 실패 대체
6. S3 연동, Docker, 속도 측정(목표 30초)

각 단계마다 unittest를 추가한다. 기존 테스트 중 폐기된 규칙(2초 컷, 컷당 자막 2줄, 현장음)에 묶인 것은 지우지 말고 새 규칙에 맞게 고치거나, 지워야 하면 사용자에게 먼저 알린다.

## 아직 팀과 확정 안 된 것 (가정으로 구현하되, 바꾸기 쉽게)

- 작업 등록 방식: Next.js → 미디어 서버 직접 호출 vs SQS
- 콜백 경로·서버 간 인증 방식 (가정: `POST {NEXT_BASE_URL}/api/internal/reels/callback`, 헤더 API 키)
- S3 키 규칙·버킷 이름 (가정: `uploads/{user_id}/{ad_id}/{scene_index}.mp4`, `results/{user_id}/{ad_id}/reel.mp4|thumb.jpg`)
- 메뉴당 2장면 규칙에 대한 프론트 최종 동의
- LLM·TTS 제공자 (환경변수로 교체 가능하게)
- 심플·크리스마스 콘셉트의 디자인 전체

## 명령어

```bash
python -m unittest discover -s tests -v   # 테스트 (stdlib unittest)

# 기존 CLI (개조 전까지 유지)
python -m src.quote_video.pipeline --video assets/videos --caption assets/caption/<name>.md --bgm "assets/bgm/<name>.mp3" --output output/<name>.mp4
```

개발 환경은 Windows(winget으로 설치한 FFmpeg), 배포는 Linux Docker다. 경로는 `pathlib`로 다루고 OS 의존 코드를 넣지 않는다.
API 키(LLM, TTS, AWS)는 `.env`로만 받고 커밋하지 않는다.
