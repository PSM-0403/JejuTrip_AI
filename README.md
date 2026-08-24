# 🍊 JejuTrip AI — Personalized Travel Recommender

제주 여행 코스를 자동으로 생성하고, AI 챗봇으로 실시간 수정할 수 있는 여행 추천 서비스입니다. 팀이 직접 수집한 CSV 데이터에 카카오 API·OpenAI GPT·Chroma 벡터 검색을 결합했습니다.

🔗 **라이브 데모**: [jejutripai.streamlit.app](https://jejutripai.streamlit.app/)

---

## 핵심 기능

- 🚀 **AI 맞춤 코스 생성** — 슬롯별(아침 카페 → 오전 관광 → 점심 → 오후 관광 → 오후 카페 → 저녁) 취향을 입력하면 CSV 데이터 + Chroma 리뷰 유사도로 자동 배치
- 💬 **AI 챗봇** — "2일차 점심 해산물로 바꿔줘"처럼 즉시 교체하거나, "추천 리스트 줘"로 후보 5곳을 받아 번호로 선택하는 등 의도를 인식해 자연어로 코스를 수정
- 🛡️ **데이터 기반 추천** — 취향 키워드는 실제 CSV 리뷰·장소명에 매칭되는 곳만 후보로 남기는 하드 필터를 거쳐, 존재하지 않는 장소·특징을 지어내지 않도록 방지
- 🧭 **동선 최적화** — 숙소가 아닌 직전 방문 장소 기준으로 다음 장소를 골라 하루 이동 동선을 최소화
- 🗺️ **카카오 API 연동** — 실시간 숙소·주소 검색, 네비게이션 경로·시간, 지도 링크 (전화번호·경로 조회는 캐싱해 반복 호출 최소화)
- 💾 **코스 저장/불러오기** — 완성한 코스를 JSON으로 내보내고 다시 불러와 복원
- 📊 **코스 분석** — 추천 근거·평점·리뷰 요약을 한눈에 확인

## 기술 스택

`Streamlit` `Kakao REST API` `OpenAI GPT-4o-mini` `Chroma (벡터 검색)` `Pandas` `Folium`

## 어떻게 작동하나

```
사용자 입력 (숙소·기간·취향)
    │
    ├─ 카카오 API ──────── 숙소 검색·좌표
    ├─ Chroma 유사도 ───── 취향과 비슷한 리뷰 → 장소 부스트
    └─ 추천 엔진 ───────── 평점·리뷰·키워드·거리 종합 점수로 슬롯별 장소 선택
                           (직전 장소 기준 거리로 동선까지 고려)
    │
    └─ 결과: 일차별 코스 · 전체 지도 · 코스 분석 · AI 챗봇
```

## 로컬 실행

```bash
pip install -r requirements.txt
cp .env.example .env     # KAKAO_API_KEY(필수) · OPENAI_API_KEY(선택) 입력
streamlit run app.py     # http://localhost:8501
```

- 카카오 API 키: [카카오 개발자 콘솔](https://developers.kakao.com) → 앱 생성 → REST API 키
- OpenAI API 키(선택): [OpenAI Platform](https://platform.openai.com/api-keys) — 없어도 CSV 기반 추천은 동작

## Streamlit Cloud 배포

GitHub 저장소를 [share.streamlit.io](https://share.streamlit.io)에 연결하고, **Secrets**에 `KAKAO_API_KEY`/`OPENAI_API_KEY`를 등록하면 됩니다. Chroma 벡터 DB(`chroma_jeju_reviews/`)는 이미 레포에 포함되어 있어 별도 빌드 없이 바로 동작합니다.

## 데이터

팀이 카카오맵을 직접 크롤링해 수집한 `jeju_crawling_100.csv` (장소 약 1,000개 — 장소명·좌표·주소·평점·리뷰). 추천 점수는 평점·리뷰 수·키워드 매칭·Chroma 유사도·거리를 종합해 계산하고, 상위 5개 후보 중 무작위 1개를 선택해 매번 다른 코스를 생성합니다.
