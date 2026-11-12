# POSTALK 백엔드 연동 안내

이 문서는 릴스 파이프라인과 POSTALK 백엔드의 운영 연동 기준이다. 릴스 서비스는 프론트엔드가 직접 호출하는 공개 API가 아니라, 백엔드가 요청한 영상 4개를 합성하는 Railway 내부 서비스다. 사용자 권한, 파일 업로드, 생성 결과 보관, 진행률 제공은 백엔드가 맡는다.

## 1. 구성 요소별 역할

| 구성 요소 | 담당 역할 | 외부 공개 여부 |
| --- | --- | --- |
| 프론트엔드 | 영상 업로드, 릴스 생성 시작, 백엔드 진행률 표시 | 공개 |
| POSTALK 백엔드 | 세션·파일 검증, Supabase 서명 URL 발급, 릴스 작업 상태와 결과 저장 | 공개 |
| 릴스 파이프라인 | 4개 영상 다운로드, 자막·BGM·전환 합성, MP4·썸네일 업로드 | Railway 내부 전용 |
| Supabase Storage | 원본 영상, 완성 MP4, 썸네일 보관 | 서명 URL로만 접근 |

브라우저는 `/reels/*` 경로와 `REELS_INTERNAL_API_KEY`를 사용하지 않는다. 프론트는 POSTALK 백엔드의 생성 세션·생성 작업 API만 호출한다.

## 2. 실제 생성 흐름

1. 사용자가 프론트에서 영상을 올리면 백엔드가 Supabase Storage에 저장한다.
2. 백엔드는 릴스 세션에서 네 개 장면을 순서대로 연결한다. 기본 순서는 간판, 대표 메뉴, 대표 메뉴 상세, 가게 입구다.
3. 네 번째 영상이 제출되면 백엔드는 생성 작업을 만들고, 각 원본의 서명 다운로드 URL과 결과 MP4·썸네일의 서명 업로드 URL을 발급한다.
4. 백엔드는 Railway 내부 주소의 `POST /reels/renders`를 호출한다.
5. 릴스 서비스는 원본 현장음을 제거하고 템플릿의 자막, 전환, BGM을 적용해 세로형 MP4를 생성한다.
6. MP4와 JPG 썸네일을 백엔드가 지정한 Supabase 경로에 올린다.
7. 백엔드는 릴스 서비스의 상태 조회 결과를 생성 작업에 반영한다. 프론트는 백엔드의 진행률 API만 2~3초 간격으로 조회한다.

현재 백엔드 연동 콘셉트는 `simple`, `christmas` 두 가지다. TTS는 사용하지 않으며 원본 영상의 소리는 결과물에 포함하지 않는다.

## 3. 백엔드 호출 계약

### 인증과 내부 주소

| 항목 | 값 | 주의 사항 |
| --- | --- | --- |
| 내부 주소 | `http://postalk-reels.railway.internal:8000` | Railway 서비스 이름이 다르면 해당 이름으로 변경 |
| 상태 확인 | `GET /healthz` | 인증 없음, `{ "status": "ok" }` 반환 |
| API 접두사 | `/reels` | `GET /healthz`를 제외하고 내부 키 필요 |
| 인증 헤더 | `x-reels-api-key` | `REELS_INTERNAL_API_KEY` 값 전송 |

### 렌더링 요청

백엔드는 `POST /reels/renders`에 아래 정보를 보낸다.

| 필드 | 필수 | 규칙 |
| --- | --- | --- |
| `user_id` | 예 | 현재 백엔드가 사용하는 사용자 식별값 |
| `ad_id` | 예 | 백엔드 생성 작업 ID. 영문·숫자·`_`·`-`, 최대 100자 |
| `concept` | 예 | `simple` 또는 `christmas` |
| `store` | 예 | 가게 이름과 소개 |
| `menus` | 예 | 1~3개 대표 메뉴 |
| `media` | 예 | 장면별 Storage 키와 Supabase 서명 다운로드 URL |
| `scenes` | 예 | 백엔드가 만든 장면별 자막 |
| `caption`, `hashtags` | 선택 | 게시물 본문과 해시태그 |
| `uploads` | 예 | MP4·JPG용 Supabase 서명 업로드 URL |

`media` 항목은 `storage: "supabase"`여야 하며, `key`와 `download_url`은 같은 Supabase 객체를 가리켜야 한다. 결과 파일 이름은 각각 `<ad_id>.mp4`, `<ad_id>.jpg`로 끝나는 생성 경로여야 한다. 서비스는 입력·출력 URL이 현재 `SUPABASE_URL` 프로젝트의 유효한 서명 URL인지 검증한다.

수락 응답은 `202 Accepted`이며 `render_id`를 돌려준다.

```json
{ "render_id": "rr_123", "ad_id": "<job-id>", "status": "queued" }
```

백엔드는 `GET /reels/renders/{render_id}`로 상태를 조회한다. 상태는 `queued`, `rendering`, `done`, `failed` 중 하나이며, 완료되면 MP4·썸네일 Storage 키, 영상 길이, 캡션, 해시태그가 포함된다.

## 4. 환경 변수

| 변수 | 릴스 서비스 | 백엔드 | 설명 |
| --- | --- | --- | --- |
| `SUPABASE_URL` | 필요 | 필요 | 같은 Supabase 프로젝트 URL. 릴스 서비스에서는 서명 URL 검증에 사용 |
| `REELS_INTERNAL_API_KEY` | 필요 | 필요 | 서버 간 인증용 공유 비밀 값 |
| `REELS_API_URL` | 불필요 | 필요 | Railway 내부 릴스 서비스 주소 |
| `PORT` | 필요 | 불필요 | 기본 예시 `8000` |
| `ANTHROPIC_API_KEY` | 선택 | 불필요 | `/reels/scripts`에서 LLM 대본 생성 기능을 직접 사용할 때만 필요 |
| `LLM_MODEL` | 선택 | 불필요 | 대본 생성에 사용할 모델 |

실제 API 키, 서명 URL, 내부 주소의 인증 정보는 코드·문서·PR 본문·로그에 적지 않는다.

## 5. Railway 배포와 점검

1. 이 저장소를 백엔드와 같은 Railway 프로젝트·환경의 별도 Docker 서비스로 배포한다.
2. 서비스에 `SUPABASE_URL`, `REELS_INTERNAL_API_KEY`, `PORT=8000`을 설정한다.
3. 백엔드에 `REELS_API_URL=http://postalk-reels.railway.internal:8000`과 동일한 `REELS_INTERNAL_API_KEY`를 설정한다.
4. 릴스 서비스 로그에서 FFmpeg가 포함된 컨테이너가 정상 기동하는지 확인한다.
5. `GET /healthz`와 백엔드의 준비 상태 확인을 각각 수행한다.
6. 테스트 영상 4개로 생성 작업을 한 건 실행하고, 결과 MP4·JPG가 Supabase에 저장되는지 확인한다.

작업 상태는 현재 Python 프로세스 메모리에 보관한다. 서비스가 재시작되면 진행 중이던 `render_id`를 잃을 수 있으므로, 백엔드는 실패 또는 미완료 작업을 안전하게 다시 등록할 수 있어야 한다.

## 6. 장애 확인 순서

| 증상 | 먼저 확인할 곳 | 대표 원인 |
| --- | --- | --- |
| 백엔드에서 생성 요청이 실패 | 백엔드 환경 변수와 로그 | `REELS_API_URL` 또는 내부 키 누락 |
| `401 Invalid reels service credentials` | 두 서비스의 내부 키 | 키 불일치 |
| `400 INVALID_RENDER_INPUT` | 백엔드 렌더 요청 | 서명 URL·Storage 키·결과 경로 불일치 |
| `400 SCENE_MISMATCH` 또는 `CAPTION_TOO_LONG` | 자막·장면 입력 | 템플릿 장면 규칙을 벗어난 요청 |
| `409 ALREADY_RENDERING` | 동일 `ad_id`의 현재 작업 | 이미 큐 대기 또는 렌더링 중. 기존 `render_id` 조회 |
| `failed / NO_USABLE_MEDIA` | 입력 영상과 서비스 로그 | 다운로드 실패 또는 사용할 수 있는 영상 없음 |
| `failed / RENDER_FAILED` | 릴스 Railway 로그 | FFmpeg 합성 또는 Supabase 업로드 실패 |

오류를 공유할 때는 작업 ID와 오류 코드만 사용하고, 다운로드 URL·업로드 URL·내부 키를 포함하지 않는다.

## 7. 로컬 검증

```powershell
pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m uvicorn src.quote_video.api:app --reload
```

로컬에서 실제 Supabase 연동을 확인할 때에는 운영 키가 아닌 테스트용 내부 키를 사용한다. FFmpeg와 한글 폰트는 Dockerfile에서 설치되므로, 로컬 렌더링 환경은 운영 컨테이너와 동일한지 별도로 확인한다.
