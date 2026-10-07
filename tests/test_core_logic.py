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


# ── RecommendationEngine._pick_candidates: 숙소 기준 반경 필터 ─

def test_pick_candidates_respects_radius_filter_even_when_farther_place_scores_higher(engine):
    """반경 필터 회귀 테스트. "반경 밖 장소는 평점이 아무리 높아도 후보에조차
    들어가면 안 된다"는 하드 컷오프를 고정해 앞으로도 깨지지 않게 한다."""
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

    candidates = engine._pick_candidates(df, "카페", [], ulat, ulng, used=set(), radius_km=5)
    names = [c["name"] for c in candidates]

    assert "근처카페" in names
    assert "먼카페" not in names, (
        "반경 5km 밖에 있는 '먼카페'가 평점이 훨씬 높다는 이유로 후보에 포함되면 안 된다"
    )


# ── RecommendationEngine._optimize_day_route: 동선 최적화 ────

def test_optimize_day_route_fills_all_slots_without_duplicate_places(engine):
    """동선 최적화 회귀 테스트. 슬롯별로 그때그때 가장 가까운 곳만 그리디하게 고르면
    카테고리 제약 때문에 지그재그(교차) 동선이 나올 수 있었던 문제를 개선하기 위해,
    슬롯 순서(시간대)는 고정한 채 숙소 출발→...→숙소 복귀 총 이동거리가 최소가 되는
    조합을 완전탐색으로 찾도록 바꿨다. 두 슬롯의 후보 목록이 겹칠 때도 같은 장소가
    중복 배정되지 않고 두 슬롯 모두 채워지는지 확인한다."""
    ulat, ulng = 33.5, 126.5
    slot_a = {"key": "a", "label": "A", "kw": []}
    slot_b = {"key": "b", "label": "B", "kw": []}

    east = {"name": "동쪽", "lat": ulat, "lng": ulng + 0.1, "category": "기타",
            "rating": 4.0, "total_cnt": 0, "reviews_text": ""}
    west = {"name": "서쪽", "lat": ulat, "lng": ulng - 0.1, "category": "기타",
            "rating": 4.0, "total_cnt": 0, "reviews_text": ""}

    # 두 슬롯 모두 같은 후보 목록(동쪽/서쪽)을 공유하는 상황
    slot_candidates = [
        (slot_a, [], [east, west]),
        (slot_b, [], [east, west]),
    ]

    chosen = engine._optimize_day_route(slot_candidates, ulat, ulng)
    chosen_names = [c["name"] for _, _, c in chosen]

    assert len(chosen_names) == 2          # 두 슬롯 모두 채워져야 한다
    assert len(set(chosen_names)) == 2     # 같은 장소가 중복 배정되면 안 된다


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


# ── RecommendationEngine._pick_candidates: 다양성(무작위성) ──

def test_pick_candidates_returns_varying_results_across_calls(engine, dm):
    """회귀 테스트. _optimize_day_route가 결정론적(완전탐색)으로 바뀌면서,
    후보 자체가 매번 고정이면 같은 조건에서 항상 같은 코스만 나오게 된다
    (다양성 확보 기능 상실). _pick_candidates가 상위 후보 중 일부를
    무작위로 뽑아 반환해 다양성을 유지하는지 확인한다."""
    df = dm.filter_by_cats(["카페"])
    ulat, ulng = 33.4996213, 126.5311884

    results = [
        tuple(sorted(c["name"] for c in engine._pick_candidates(
            df, "카페", [], ulat, ulng, used=set(), radius_km=60,
        )))
        for _ in range(10)
    ]

    assert len(set(results)) > 1, "10번을 돌려도 후보 조합이 항상 똑같으면 다양성이 사라진 것"


# ── text_match / _pick_candidates: 취향 키워드 매칭 ──────────

def test_keyword_view_does_not_match_the_word_review():
    """회귀 테스트. "뷰"는 "리뷰"라는 단어에도 들어 있어서, 리뷰 본문에 "리뷰"만 있어도
    "뷰 좋은 카페"에 매칭되던 문제(카페 '뷰' 후보 141곳 중 31곳)를 고정한다."""
    from text_match import text_contains

    assert not text_contains("리뷰 이벤트 참여했어요", "뷰")
    assert text_contains("오션뷰가 정말 좋아요", "뷰")


def test_pick_candidates_ranks_place_with_more_keyword_mentions_first(engine):
    """취향 키워드 점수는 "한 번이라도 나왔는지"가 아니라 "몇 %의 리뷰에 나왔는지"로 준다.
    평점·리뷰 수가 같으면, 키워드를 더 많은 리뷰에서 언급한 곳이 먼저 와야 한다."""
    ulat, ulng = 33.5, 126.5
    base = {"lat": ulat + 0.01, "lng": ulng, "category": "카페", "rating": 4.5, "total_cnt": 100}
    few = dict(base, name="한번언급카페",
               reviews_text=" | ".join(["조용해요"] + ["맛있어요"] * 19))
    many = dict(base, name="자주언급카페",
                reviews_text=" | ".join(["조용해요"] * 8 + ["맛있어요"] * 12))
    df = pd.DataFrame([few, many])

    top = engine._pick_candidates(df, "카페", [], ulat, ulng, used=set(),
                                  pref_kw=["조용"], radius_km=5, top_k=1, pool_size=1)

    assert top[0]["name"] == "자주언급카페"


def test_pick_candidates_hard_filter_ignores_review_word_for_view(engine):
    """'뷰'를 취향으로 넣었을 때 '리뷰'라는 단어만 있는 장소는 후보에서 빠져야 한다."""
    ulat, ulng = 33.5, 126.5
    base = {"lat": ulat + 0.01, "lng": ulng, "category": "카페", "rating": 4.5, "total_cnt": 100}
    review_only = dict(base, name="쿠키주는카페", reviews_text="리뷰 쓰면 쿠키 줘요")
    real_view = dict(base, name="바다보이는카페", reviews_text="뷰가 정말 예뻐요")
    df = pd.DataFrame([review_only, real_view])

    names = [c["name"] for c in engine._pick_candidates(
        df, "카페", [], ulat, ulng, used=set(), pref_kw=["뷰"], radius_km=5)]

    assert names == ["바다보이는카페"]


def test_single_char_keyword_false_matches_are_ignored():
    """한 글자 키워드는 다른 단어에 자주 걸린다. 리뷰에서 많이 나온 오탐("주차", "후회")은 거른다."""
    from text_match import text_contains

    assert not text_contains("주차장이 넓어요", "차")
    assert text_contains("차가 향긋해요", "차")
    assert not text_contains("후회 없는 선택", "회")
    assert text_contains("물회가 시원해요", "회")


def test_ai_keyword_extraction_keeps_single_char_keyword(dm):
    """회귀 테스트. GPT 추출 결과에서 두 글자 미만을 버려서 "뷰 좋은 카페"가 ['카페']만 남고
    '뷰' 취향이 무시되던 문제를 고정한다."""
    engine = RecommendationEngine(dm, kakao=None, openai_key="")
    engine.ai = _FakeOpenAIClient("뷰,카페")

    assert "뷰" in engine._extract_pref_keywords("뷰 좋은 카페")


# ── chatbot: 챗 키워드로 후보 찾기 ────────────────────────────

def _chat_pool(rows):
    df = pd.DataFrame(rows)
    df["_dist"] = 1.0
    return df


def test_chat_ranking_prefers_place_matching_all_keywords():
    """"해산물이 들어간 국수집"처럼 키워드가 여러 개면, 모두 언급된 곳만 남겨야 한다."""
    from chatbot import _rank_by_keywords

    base = {"rating": 4.5, "total_cnt": 100}
    pool = _chat_pool([
        dict(base, name="국수만집", reviews_text="국수가 맛있어요"),
        dict(base, name="해산물만집", reviews_text="해산물이 신선해요"),
        dict(base, name="둘다집", reviews_text="해산물 듬뿍 들어간 국수"),
    ])

    ranked = _rank_by_keywords(pool, ["해산물", "국수"])

    assert list(ranked["name"]) == ["둘다집"]


def test_chat_ranking_falls_back_to_partial_match_with_more_hits_first():
    """모두 언급된 곳이 없으면 하나라도 맞는 곳에서 고르고, 전혀 안 맞는 곳은 뺀다."""
    from chatbot import _rank_by_keywords

    base = {"rating": 4.5, "total_cnt": 100}
    pool = _chat_pool([
        dict(base, name="국수집", reviews_text="국수가 맛있어요"),
        dict(base, name="카페", reviews_text="커피가 맛있어요"),
    ])

    ranked = _rank_by_keywords(pool, ["해산물", "국수"])

    assert list(ranked["name"]) == ["국수집"]


def test_split_keywords_handles_space_and_comma():
    from text_match import split_keywords

    assert split_keywords("해산물 국수") == ["해산물", "국수"]
    assert split_keywords("해산물,국수") == ["해산물", "국수"]
    assert split_keywords("") == []


def test_generic_words_are_dropped_from_preference_keywords(dm):
    """'카페' 같은 일반 단어는 거의 모든 카페에 걸려서, 같이 뽑힌 '뷰'가 후보 거르기에
    반영되지 않았다. 시간대가 이미 정해 주는 단어는 키워드에서 뺀다."""
    engine = RecommendationEngine(dm, kakao=None, openai_key="")
    engine.ai = _FakeOpenAIClient("카페,뷰")

    assert engine._extract_pref_keywords("뷰 좋은 카페") == ["뷰"]


def test_pick_candidates_keeps_only_places_matching_all_preference_keywords(engine):
    """코스 생성도 챗봇과 같은 기준: "흑돼지 구이"면 둘 다 언급된 곳만 남긴다.
    흑돼지만 언급된 곳은 평점이 더 높아도 후보에 들어가면 안 된다."""
    ulat, ulng = 33.5, 126.5
    base = {"lat": ulat + 0.01, "lng": ulng, "category": "맛집", "total_cnt": 100}
    stew = dict(base, name="전골집", rating=5.0, reviews_text="흑돼지 김치전골이 맛있어요")
    grill = dict(base, name="구이집", rating=4.0, reviews_text="흑돼지 구이가 최고예요")
    df = pd.DataFrame([stew, grill])

    names = [c["name"] for c in engine._pick_candidates(
        df, "맛집", [], ulat, ulng, used=set(), pref_kw=["흑돼지", "구이"], radius_km=5)]

    assert names == ["구이집"]


def test_keywords_in_separate_reviews_rank_below_keywords_in_same_review():
    """'흑돼지'와 '구이'가 서로 다른 리뷰에 따로 있는 생선구이집보다,
    같은 리뷰에 함께 나온 흑돼지구이집이 후보로 남아야 한다."""
    from text_match import filter_by_keywords

    pool = pd.DataFrame([
        {"name": "생선구이집", "reviews_text": "흑돼지 먹고 다음날 왔어요 | 고등어구이가 최고"},
        {"name": "돼지집", "reviews_text": "흑돼지 구이가 두툼해요 | 친절해요"},
    ])

    kept, _ = filter_by_keywords(pool, ["흑돼지", "구이"])

    assert list(kept["name"]) == ["돼지집"]
