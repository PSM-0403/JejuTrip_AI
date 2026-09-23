# ============================================================
# test_core_logic.py  |  추천 로직 핵심 단위 테스트
# ============================================================
# 실행: 프로젝트 루트에서 `pytest`
#
# 범위: 외부 API(카카오/OpenAI) 호출이나 Streamlit 런타임 없이도
# 검증 가능한 "순수 로직"만 테스트한다. RecommendationEngine을
# openai_key="" 로 생성하면 self.ai가 None이 되어 모든 AI 관련
# 메서드가 네트워크 호출 없이 규칙 기반(폴백) 경로로만 동작한다.
# ============================================================

import pandas as pd
import pytest

from data_manager import DataManager
from recommendation_engine import RecommendationEngine
from kakao_service import haversine


@pytest.fixture(scope="module")
def dm():
    """실제 jeju_crawling_100.csv를 로딩한 DataManager. 모듈 내에서 재사용해 반복 로딩을 피한다."""
    return DataManager()


@pytest.fixture(scope="module")
def engine(dm):
    """OpenAI 키 없이 생성 → self.ai=None → 모든 로직이 네트워크 호출 없는 폴백 경로로 동작."""
    return RecommendationEngine(dm, kakao=None, openai_key="")


class _FakeOpenAIClient:
    """실제 API를 호출하지 않고 마지막 프롬프트만 캡처하는 테스트용 가짜 클라이언트."""

    def __init__(self, response_content: str):
        self.last_prompt = None
        self._response_content = response_content
        self.chat = self
        self.completions = self

    def create(self, model, messages, **kwargs):
        self.last_prompt = messages[0]["content"]

        class _Msg:
            def __init__(self, content):
                self.content = content

        class _Choice:
            def __init__(self, content):
                self.message = _Msg(content)

        class _Response:
            def __init__(self, content):
                self.choices = [_Choice(content)]

        return _Response(self._response_content)


# ── haversine (직선거리 계산) ────────────────────────────────

def test_haversine_same_point_is_zero():
    """같은 좌표 두 개의 거리는 0이어야 한다."""
    assert haversine(33.5, 126.5, 33.5, 126.5) == 0


def test_haversine_increases_with_distance():
    """위도 차이가 커질수록 계산된 거리도 단조 증가해야 한다."""
    same  = haversine(33.5, 126.5, 33.5, 126.5)
    near  = haversine(33.5, 126.5, 33.51, 126.5)
    far   = haversine(33.5, 126.5, 34.0, 126.5)
    assert same < near < far


# ── DataManager: 카테고리 정규화 ─────────────────────────────

def test_category_normalization_maps_alias_to_standard(dm):
    """config.CATEGORY_MAP에 등록된 별칭(예: '해수욕장')은 표준 5개 카테고리 중
    '자연'으로 정규화돼야 한다. CSV 원본 카테고리 표기가 들쭉날쭉해도
    추천 로직은 항상 5개 표준 카테고리 기준으로 동작해야 하므로 중요하다."""
    assert dm._norm_cat("해수욕장") == "자연"
    assert dm._norm_cat("카페") == "카페"


def test_category_normalization_falls_back_to_etc(dm):
    """CATEGORY_MAP에 없는 낯선 카테고리 문자열은 '기타'로 떨어져야 한다
    (매핑 안 된 데이터가 조용히 사라지지 않고 '기타'로라도 남도록)."""
    assert dm._norm_cat("전혀 모르는 카테고리") == "기타"


# ── RecommendationEngine._pick: 숙소 기준 반경 필터 ──────────

def test_pick_respects_radius_filter_even_when_farther_place_scores_higher(engine):
    """반경 필터 회귀 테스트.
    '숙소 기준 반경 설정이 실제로 지켜지는지 의심스럽다'는 피드백을 조사하는 과정에서,
    반경 안이어도 하루 동선이 누적되며 숙소에서 점점 멀어질 수 있는 별개의 스코어링 문제를
    찾아 수정했다(숙소 거리 페널티 추가). 이 테스트는 그와 별개로 "반경 밖 장소는 평점이
    아무리 높아도 절대 선택되지 않는다"는 하드 컷오프 자체를 고정해 앞으로도 깨지지 않게 한다."""
    ulat, ulng = 33.5, 126.5
    near = {  # 숙소에서 위도 0.01도(~1.1km) — 반경 5km 이내
        "name": "근처카페", "lat": ulat + 0.01, "lng": ulng, "category": "카페",
        "rating": 3.0, "total_cnt": 0, "reviews_text": "",
    }
    far = {  # 숙소에서 위도 0.5도(~55km) — 반경 5km 밖. 평점·리뷰수는 훨씬 좋게 설정
        "name": "먼카페", "lat": ulat + 0.5, "lng": ulng, "category": "카페",
        "rating": 5.0, "total_cnt": 1000, "reviews_text": "",
    }
    df = pd.DataFrame([near, far])

    picked = engine._pick(df, "카페", [], ulat, ulng, used=set(), radius_km=5)

    assert picked is not None
    assert picked["name"] == "근처카페"


# ── RecommendationEngine: 취향 키워드 추출 (부정 표현 제거) ──

def test_extract_pref_keywords_removes_negated_terms(engine):
    """"카페는 상관없어"처럼 부정/무관심 표현이 바로 뒤에 붙은 키워드는
    검색 키워드에서 제외돼야 한다. 여기서 걸러지지 않으면 하드필터가
    사용자가 원치 않는다고 명시한 카테고리까지 강제로 매칭시켜버린다."""
    keywords = engine._extract_pref_keywords("흑돼지 좋아하는데 카페는 상관없어")

    assert "흑돼지" in keywords
    assert "카페" not in keywords


# ── RecommendationEngine._reason: 추천 이유 문구 ─────────────

def test_reason_includes_accommodation_distance(engine):
    """reason 문자열에는 실제 스코어링(숙소 거리 페널티)에 쓰인 숙소 거리(_dist)가
    그대로 노출돼야 한다. '추천 이유가 너무 단순하다'는 피드백을 반영해 추가한 부분의
    회귀 테스트 — 취향 미입력 슬롯도 왜 이 장소가 뽑혔는지 알 수 있어야 한다."""
    place = {
        "name": "테스트카페", "rating": 3.0, "total_cnt": 0,
        "reviews_text": "", "_dist": 3.8,
    }
    slot = {"label": "☕ 아침 카페", "kw": []}

    reason = engine._reason(place, slot, pref_kw=[])

    assert "숙소" in reason
    assert "3.8" in reason


def test_reason_flags_when_preference_keyword_not_found_in_data(engine):
    """취향 키워드가 하드필터 fallback으로 인해 실제로는 이 장소 데이터에 없는 채로
    선택된 경우, reason에 경고(⚠️)가 남아야 한다. 이게 바로 '데이터에 없는 특징을
    지어내지 않는다'는 할루시네이션 방지 설계의 핵심이라, 조용히 숨기면 안 된다."""
    place = {
        "name": "아무카페", "rating": 3.0, "total_cnt": 0,
        "reviews_text": "그냥 평범한 카페입니다", "_dist": 1.0,
    }
    slot = {"label": "☕ 아침 카페", "kw": []}

    reason = engine._reason(place, slot, pref_kw=["오션뷰"])

    assert "⚠️" in reason
    assert "오션뷰" in reason


# ── RecommendationEngine._classify_reviews: 리뷰 요약 프롬프트 ─

def test_classify_reviews_prompt_includes_place_name(dm):
    """회귀 테스트. 크롤링된 리뷰(특히 블로그 후기)는 한 글 안에 그날 들른 다른 가게
    이야기가 섞여 있는 경우가 있다(예: 카페 리뷰인데 '짬뽕집 탕수육은 맛있었다'는
    무관한 내용이 포함). 장소명을 프롬프트에 안 넣으면 GPT가 이걸 걸러낼 방법이
    없으므로, 반드시 프롬프트에 장소명이 포함돼야 한다.
    실제 API를 부르지 않고 가짜 클라이언트로 프롬프트 내용만 검사한다."""
    fake = _FakeOpenAIClient('{"pos": ["좋음"], "neg": []}')
    engine = RecommendationEngine(dm, kakao=None, openai_key="")
    engine.ai = fake  # 실제 네트워크 호출 없이 프롬프트만 캡처

    engine._classify_reviews(
        "리뷰 하나 입니다 글자수 넘게 채움 | 리뷰 둘 입니다 글자수 넘게 채움",
        place_name="나모나모베이커리",
    )

    assert fake.last_prompt is not None
    assert "나모나모베이커리" in fake.last_prompt
