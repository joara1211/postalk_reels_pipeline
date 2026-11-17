# 배경음악 출처와 라이선스

렌더할 때마다 콘셉트별 목록(`templates/<concept>.json`의 `audio.bgm_tracks`)에서 한 곡을 무작위로 고른다.
새로 넣은 곡은 원곡의 처음 40초를 잘라 쓰고(끝 2초 페이드 아웃), 음량을 기존 곡과 비슷한 약 -17 LUFS로 맞춰 192kbps MP3로 저장했다.

새 곡은 모두 **CC0 1.0(퍼블릭 도메인 헌정)** 이다. 상업적 사용, 수정, 재배포가 자유롭고 출처 표기 의무도 없다.
그래도 확인할 수 있도록 출처를 남긴다.

## 심플 (`simple`)

| 파일 | 원곡 | 아티스트 | 라이선스 | 출처 |
|---|---|---|---|---|
| `MP_맑은 아침 (Short).mp3` | 맑은 아침 | (기존 곡, 출처 기록 없음) | 확인 필요 | — |
| `simple_go_to_the_picnic.mp3` | Go to the Picnic | Loyalty Freak Music | CC0 1.0 | [POSITIVE ATTITUDE !](https://archive.org/details/LoyaltyFreakMusicPOSITIVEATTITUDE20170920175908839) |
| `simple_ice_cream_with_you.mp3` | Ice Cream with you | Loyalty Freak Music | CC0 1.0 | [POSITIVE ATTITUDE !](https://archive.org/details/LoyaltyFreakMusicPOSITIVEATTITUDE20170920175908839) |
| `simple_sweet_sun.mp3` | Sweet Sun | Loyalty Freak Music | CC0 1.0 | [POSITIVE ATTITUDE !](https://archive.org/details/LoyaltyFreakMusicPOSITIVEATTITUDE20170920175908839) |

## 크리스마스 (`christmas`)

| 파일 | 원곡 | 아티스트 | 라이선스 | 출처 |
|---|---|---|---|---|
| `christmas_hyper_jingle_bells.mp3` | Hyper Jingle Bells | Loyalty Freak Music | CC0 1.0 | [HOLIDAY SEASON SONGS !!](https://archive.org/details/loyalty-freak-music-holiday-season-songs) |
| `christmas_snow_cakes_with_friends.mp3` | Snow Cakes with friends | Loyalty Freak Music | CC0 1.0 | [HOLIDAY SEASON SONGS !!](https://archive.org/details/loyalty-freak-music-holiday-season-songs) |
| `christmas_carol_of_the_internet.mp3` | Carol of the Internet | Loyalty Freak Music | CC0 1.0 | [HOLIDAY SEASON SONGS !!](https://archive.org/details/loyalty-freak-music-holiday-season-songs) |

CC0 전문: https://creativecommons.org/publicdomain/zero/1.0/

## 곡을 추가할 때

- 상업적 사용과 재배포가 모두 허용된 곡만 넣는다(CC0·퍼블릭 도메인 권장). 이 레포는 공개 레포이고, 결과물은 POSTALK 공식 인스타그램에 게시된다.
- 파일을 `assets/bgm/`에 넣고 해당 콘셉트 JSON의 `audio.bgm_tracks`에 경로를 추가한 뒤, 이 표에 출처를 적는다. 코드 변경은 필요 없다.
