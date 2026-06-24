# 🍊 JejuTrip AI — Personalized Travel Recommender

제주도 여행 맞춤 추천 시스템. 팀이 직접 수집한 CSV 데이터와 카카오 API, OpenAI GPT를 결합해 일차별 여행 코스를 자동 생성하고 AI 챗봇으로 실시간 수정합니다.

---

## 주요 기능

| 기능 | 설명 |
|------|------|
| 🏨 숙소 검색 | 카카오 API 실시간 검색 — 숙소명·도로명 주소 모두 지원 |
| 🚀 코스 자동 생성 | 일차별 6개 슬롯(아침 카페→오전 관광→점심→오후 관광→오후 카페→저녁) 자동 배치 |
| 🤖 AI 맞춤 조건 | 슬롯별 취향 입력 → 키워드 추출 → CSV 데이터 매칭 |
| 🧠 Chroma 리뷰 검색 | 리뷰 임베딩 유사도로 취향에 맞는 장소 부스트 |
| 🗺️ 전체 지도 | Folium으로 일차별 색상 구분 마커 + 동선 표시 |
| 💬 AI 챗봇 | 코스 장소 즉시 교체 / 후보 리스트 제시 / 제주 여행 Q&A |
| 📊 코스 분석 | 추천 근거·평점·리뷰 요약 탭 |
| 📍 반경 필터 | 숙소 기준 5~60km 반경 내 장소만 추천 |

---

## 프로젝트 구조

```
jeju/
├── app.py                   # Streamlit 메인 앱 (진입점)
├── config.py                # API 키, 카테고리, 슬롯 설정
├── data_manager.py          # CSV 로딩 및 카테고리 정규화
├── recommendation_engine.py # 추천 엔진 (점수 계산, 슬롯 배치)
├── kakao_service.py         # 카카오 REST API 연동
├── chatbot.py               # OpenAI 챗봇 (코스 수정 + 대화)
├── chroma_retriever.py      # Chroma 벡터 DB 유사도 검색
├── build_chroma.py          # Chroma DB 최초 적재 스크립트
├── ui_components.py         # Streamlit UI 컴포넌트
├── recrawl.py               # CSV 데이터 재크롤링 스크립트
├── jeju_crawling_100.csv    # 장소 데이터 (팀 직접 수집)
├── chroma_jeju_reviews/     # Chroma 벡터 DB (빌드 완료본 포함)
├── .env                     # API 키 환경변수 (직접 생성, Git 제외)
├── .env.example             # 환경변수 템플릿
├── .python-version          # Python 3.11 지정 (Streamlit Cloud용)
└── requirements.txt
```

---

## 로컬 실행

### 요구사항

- Python 3.11 이상
- 카카오 REST API 키 (필수)
- OpenAI API 키 (선택 — 없어도 기본 CSV 추천 동작)

### 1. 패키지 설치

```bash
pip install -r requirements.txt
```

### 2. 환경변수 설정

`.env.example`을 복사해 `.env`로 이름을 바꾸고 API 키를 입력합니다.

```bash
cp .env.example .env
```

```env
KAKAO_API_KEY=your_kakao_rest_api_key
OPENAI_API_KEY=your_openai_api_key   # 선택
OPENAI_MODEL=gpt-4o-mini             # 기본값
```

**API 키 발급 방법**

- **카카오 API 키**: [카카오 개발자 콘솔](https://developers.kakao.com) → 앱 생성 → REST API 키
- **OpenAI API 키**: [OpenAI Platform](https://platform.openai.com/api-keys)

> `.env` 설정 없이도 앱 실행 후 사이드바에서 직접 키를 입력할 수 있습니다.

### 3. (선택) Chroma 리뷰 DB 재구축

`chroma_jeju_reviews/`가 이미 레포에 포함되어 있으므로 일반적으로 불필요합니다.  
CSV 데이터를 새로 수집한 경우에만 재실행합니다.

```bash
python build_chroma.py
```

### 4. 앱 실행

```bash
streamlit run app.py
```

브라우저에서 `http://localhost:8501` 접속

---

## Streamlit Cloud 배포

### 배포 방법

1. GitHub에 코드 push (Chroma DB 포함)
2. [share.streamlit.io](https://share.streamlit.io) 접속 → GitHub 로그인
3. **Create app** → **Deploy a public app from GitHub**
4. Repository: `PSM-0403/JejuTrip_AI` / Branch: `main` / Main file: `app.py`
5. **Advanced settings** → Secrets에 API 키 입력:

```toml
KAKAO_API_KEY = "your_kakao_rest_api_key"
OPENAI_API_KEY = "your_openai_api_key"
OPENAI_MODEL = "gpt-4o-mini"
```

6. **Deploy** 클릭

### 클라우드 동작 방식

| 항목 | 내용 |
|------|------|
| Chroma DB | `chroma_jeju_reviews/`가 레포에 포함되어 있어 클라우드에서도 즉시 사용 가능 |
| API 키 | Streamlit Secrets에 등록하거나 앱 사이드바에서 직접 입력 |
| Python | `.python-version` 파일로 3.11 고정 |

---

## 사용 방법

1. **숙소/출발지 설정** — 사이드바에서 숙소명이나 주소를 검색해 기준 위치를 지정합니다.
2. **여행 기간 선택** — 달력에서 출발일·도착일을 선택합니다 (최대 7일).
3. **카테고리 선택** — 맛집·카페·자연·문화·기타 중 원하는 카테고리를 체크합니다.
4. **반경 설정** — 숙소 기준 추천 반경(5~60km)을 슬라이더로 조정합니다.
5. **AI 맞춤 조건 입력** (선택) — 일차별 슬롯에 원하는 조건을 입력합니다.
   - 예시: `오전 관광 → "오름, 뷰 좋은 곳"`, `저녁 식사 → "흑돼지"`
6. **코스 생성** — `🚀 여행 코스 추천 생성` 버튼을 클릭합니다.
7. **결과 확인** — 일차별 탭, 전체 지도 탭, 코스 분석 탭에서 결과를 확인합니다.
8. **AI 챗봇 활용** — 사이드바 하단 `💬 AI 챗봇 열기`로 코스를 실시간 수정합니다.

### 챗봇 명령어 예시

| 입력 | 동작 |
|------|------|
| `"2일차 저녁을 해산물로 바꿔줘"` | 해산물 키워드 기반 즉시 교체 |
| `"1일차 점심 추천 리스트 줘"` | 후보 5개 제시 |
| `"3번으로 변경해줘"` | 제시된 후보 중 선택 |
| `"성산일출봉 주변 카페 추천해줘"` | 일반 여행 Q&A |

---

## 데이터 구조

`jeju_crawling_100.csv` 컬럼:

| 컬럼 | 설명 |
|------|------|
| `place_name` | 장소명 |
| `x` / `y` | 경도 / 위도 |
| `address_name` | 주소 |
| `category_group_name` | 카테고리 (맛집·카페·자연·문화·기타) |
| `place_url` | 카카오맵 URL |
| `rating` | 평점 |
| `total_cnt` | 리뷰 수 + 블로그 수 합산 |
| `reviews_text` | 리뷰 본문 (`|` 구분) |

### 데이터 재크롤링

기존 CSV에서 리뷰가 비어있는 장소만 재수집합니다.

```bash
python recrawl.py
```

실패 목록은 `recrawl_fail_list.csv`에 저장됩니다.

---

## 추천 점수 계산 방식

```
점수 = 평점 × 10
     + 리뷰 수 / 10  (최대 20점)
     + 슬롯 키워드 이름 매칭 × 20점
     + 슬롯 키워드 리뷰 매칭 × 5점
     + 취향 키워드 이름 매칭 × 50점
     + 취향 키워드 리뷰 매칭 × 20점
     + Chroma 유사도 부스트 (최대 50점, 순위 기반)
     - 숙소 거리 × 0.5점/km
```

상위 5개 후보 중 랜덤 1개 선택으로 매 실행마다 다양한 코스를 생성합니다.

---

## 기술 스택

| 분류 | 기술 |
|------|------|
| 프론트엔드 | Streamlit |
| 지도 | Folium + streamlit-folium |
| AI 챗봇 | OpenAI GPT-4o-mini |
| 벡터 검색 | Chroma + OpenAI text-embedding-3-small |
| 장소 검색·지도 | 카카오 REST API |
| 크롤링 | Selenium + ChromeDriver |
| 데이터 처리 | Pandas |
| 환경변수 | python-dotenv |

---

## 아키텍처 흐름

```
사용자 입력
    │
    ├─ 숙소 검색 ──────────── 카카오 REST API
    │
    ├─ 취향 조건 입력
    │      │
    │      ├─ CSV 키워드 매칭 ── jeju_crawling_100.csv
    │      └─ Chroma 유사도 ──── 리뷰 임베딩 DB
    │
    ├─ 추천 엔진 (recommendation_engine.py)
    │      └─ 점수 계산 → 슬롯 배치 → 일정 생성
    │
    └─ 결과 표시
           ├─ 일차별 코스 탭
           ├─ 전체 지도 (Folium)
           ├─ 코스 분석 탭
           └─ AI 챗봇 (실시간 수정)
```
