# POSTALK Reels Pipeline

POSTALK 백엔드가 전달한 영상 4개를 세로형 릴스로 합성하는 내부 서비스입니다. 원본 영상의 소리는 제거하고, 템플릿의 자막·전환·배경음악을 적용해 MP4와 썸네일을 만듭니다. 프론트엔드는 이 서비스에 직접 요청하지 않습니다.

현재 백엔드 연동은 `simple`, `christmas` 두 콘셉트를 사용합니다. 릴스 결과는 1080×1920 MP4, 약 13초이며 Supabase Storage 서명 URL로만 입력과 출력을 주고받습니다.

## 실행 환경

| 항목 | 용도 |
| --- | --- |
| `PORT` | FastAPI 포트. 기본 예시는 `8000` |
| `SUPABASE_URL` | 서명 URL의 프로젝트 검증에 사용 |
| `REELS_INTERNAL_API_KEY` | 백엔드와 공유하는 내부 호출 키 |
| `ANTHROPIC_API_KEY` | 이 서비스에서 대본 API를 직접 사용할 때만 필요 |
| `LLM_MODEL` | 선택한 대본 생성 모델 |

Railway에서는 공개 도메인 대신 내부 주소를 사용합니다. 백엔드의 `REELS_API_URL`은 `http://<service>.railway.internal:8000` 형식이며, 두 서비스의 `REELS_INTERNAL_API_KEY` 값은 같아야 합니다.

## 내부 API

`GET /healthz`를 제외한 모든 경로는 `x-reels-api-key` 헤더가 필요합니다.

| 메서드·경로 | 설명 |
| --- | --- |
| `GET /healthz` | 서비스 상태 확인 |
| `GET /reels/templates` | 사용할 수 있는 콘셉트와 장면 규칙 조회 |
| `POST /reels/scripts` | 선택 기능. 자막·캡션·해시태그 초안 생성 |
| `GET /reels/scripts/:scriptId` | 대본 작업 상태 조회 |
| `POST /reels/renders` | 릴스 렌더링 시작 |
| `GET /reels/renders/:renderId` | 렌더링 상태와 결과 조회 |

### 렌더링 요청

```json
{
  "user_id": 1,
  "ad_id": "백엔드 생성 작업 ID",
  "concept": "christmas",
  "store": { "name": "가게 이름", "description": "가게 소개" },
  "menus": [{ "menu_index": 0, "name": "대표 메뉴" }],
  "media": [
    { "scene_index": 0, "menu_index": null, "key": "Storage 경로", "storage": "supabase", "download_url": "서명 다운로드 URL" }
  ],
  "scenes": [{ "scene_index": 0, "text": "화면 자막" }],
  "caption": "게시물 본문",
  "hashtags": ["#시장맛집"],
  "uploads": {
    "video_url": "MP4 서명 업로드 URL",
    "thumbnail_url": "JPG 서명 업로드 URL"
  }
}
```

백엔드 연동에서는 간판, 대표 메뉴, 메뉴 상세, 가게 입구 순서의 영상 4개를 보냅니다. 같은 `ad_id`가 처리 중이면 새 작업을 만들지 않고 `409 ALREADY_RENDERING`과 기존 `render_id`를 반환합니다.

상태는 `queued`, `rendering`, `done`, `failed`입니다. 진행 단계는 `preparing`, `composing`, `uploading`이며, 완료 응답에는 MP4·썸네일 Storage 경로와 길이가 포함됩니다. 입력 URL, 업로드 URL, 내부 API 키는 서버 간 통신에만 사용합니다.

자세한 필드와 오류 코드는 [릴스 API 명세](docs/reels-api-spec.md), Railway 연결 방식은 [배포 문서](docs/deployment.md)를 참고하세요.

## 로컬 확인

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m uvicorn src.quote_video.api:app --reload
```
