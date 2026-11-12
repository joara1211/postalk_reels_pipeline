# Railway에서 릴스 실행

POSTALK 백엔드와 같은 Railway 프로젝트 및 환경에 이 저장소를 별도 Docker 서비스로 추가합니다. 공개 도메인 없이 내부 주소를 사용합니다. 서비스 상태 경로는 `/healthz`입니다.

| 릴스 서비스 변수 | 값 |
| --- | --- |
| `SUPABASE_URL` | POSTALK 백엔드와 같은 Supabase 프로젝트의 HTTPS URL |
| `REELS_INTERNAL_API_KEY` | POSTALK 백엔드와 공유하는 비밀 값 |
| `PORT` | 백엔드 내부 URL에 넣을 서비스 포트. 예시는 `8000` |

POSTALK 백엔드에는 `REELS_API_URL=http://<릴스 서비스 이름>.railway.internal:8000`과 같은 `REELS_INTERNAL_API_KEY`를 설정합니다. 릴스 서비스가 호출하는 Supabase URL은 작업에 필요한 다운로드 및 업로드 URL로 한정됩니다. Supabase 서비스 역할 키는 백엔드에서 관리합니다.

`POST /reels/renders`는 장면별 `media[].download_url`과 결과 저장용 `uploads.video_url`, `uploads.thumbnail_url`을 받습니다. 백엔드는 같은 작업 ID로 등록을 다시 요청하고, 상태 조회로 완료를 확인합니다. 렌더 작업 상태는 Python 프로세스에 저장되며 재시작하면 백엔드에서 작업을 다시 등록합니다. 완성 영상과 썸네일은 Supabase에 저장합니다.

영상에 템플릿의 배경음악과 자막이 들어갑니다. 원본 영상의 현장음은 제거합니다. 대본 API에서 LLM을 직접 쓰는 경우에만 릴스 서비스에 `ANTHROPIC_API_KEY`가 필요합니다. 이번 POSTALK 연동은 백엔드에서 만든 대본을 합성 요청에 넘깁니다.
