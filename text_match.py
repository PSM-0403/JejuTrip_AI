# ============================================================
# text_match.py  |  리뷰 텍스트 키워드 매칭
# ============================================================
# 역할: 추천 엔진과 챗봇이 같은 기준으로 키워드를 매칭하도록 한 곳에 모음
#   - review_contains : 리뷰에 키워드가 있는지 (후보 하드 필터용)
#   - review_hit_ratio: 키워드가 나온 리뷰의 비율 (점수용)
#
# 부분 문자열 오탐 방지:
#   "뷰"는 "리뷰"라는 단어에도 들어 있어서, 리뷰 본문에 "리뷰"만 있어도
#   "뷰 좋은 카페"에 매칭됐다 (카페 '뷰' 후보 141곳 중 31곳이 이 경우).
#   키워드를 포함하는 다른 단어를 먼저 지운 뒤 매칭한다.
#   → analysis/keyword_score_simulation.py
# ============================================================

import pandas as pd

# 키워드 → 그 키워드를 포함하지만 뜻이 다른 단어
# 리뷰 본문에서 한 글자 키워드가 들어간 단어를 세어, 자주 나오는 오탐만 골랐다
# (예: "차"는 "주차" 333회 vs "차" 107회). "면"처럼 "가면·보면" 같은 말끝에 걸리는
# 경우는 목록으로 막을 수 없어 남아 있다.
FALSE_MATCH_WORDS = {
    "뷰": ["리뷰"],
    "차": ["주차", "차라리", "차량"],
    "회": ["후회", "기회", "회원", "회사"],
    "탕": ["설탕"],
    "국": ["결국", "이국"],
    "술": ["미술", "예술"],
}

REVIEW_SEP = "|"
MIN_REVIEWS = 10   # 리뷰가 적은 곳은 1~2개만 맞아도 비율이 크게 튀지 않도록 분모 하한


def _strip_false_matches(text: str, kw: str) -> str:
    for word in FALSE_MATCH_WORDS.get(kw, []):
        text = text.replace(word, "")
    return text


def text_contains(text, kw: str) -> bool:
    """문자열 하나에 키워드가 있는지 (대소문자 무시)"""
    t = _strip_false_matches(str(text or ""), kw)
    return kw.lower() in t.lower()


def review_contains(reviews: pd.Series, kw: str) -> pd.Series:
    """리뷰 텍스트 Series에서 키워드가 있는 행"""
    return reviews.fillna("").astype(str).apply(lambda t: text_contains(t, kw))


def review_hit_ratio(reviews: pd.Series, kw: str) -> pd.Series:
    """키워드가 나온 리뷰 수 / max(리뷰 수, MIN_REVIEWS)

    "한 번이라도 나오면 같은 점수"로 주면 리뷰를 많이 모은 곳일수록 유리하고,
    후보 안에서는 키워드가 얼마나 자주 언급되는지가 순위에 반영되지 않는다."""
    def ratio(text: str) -> float:
        parts = [p for p in str(text or "").split(REVIEW_SEP) if p.strip()]
        if not parts:
            return 0.0
        hits = sum(text_contains(p, kw) for p in parts)
        return hits / max(len(parts), MIN_REVIEWS)

    return reviews.fillna("").astype(str).apply(ratio)


# 시간대(슬롯)가 이미 정해 주는 일반 단어. 키워드로 쓰면 거의 모든 장소에 걸려서
# "뷰 좋은 카페"가 ['카페', '뷰']로 뽑히면 '카페'만으로도 모든 카페가 후보를 통과했다.
GENERIC_WORDS = {"카페", "맛집", "식당", "음식점", "관광지", "명소", "장소", "곳", "가게", "여행지"}


def drop_generic(keywords: list) -> list:
    return [k for k in keywords if k not in GENERIC_WORDS]


def split_keywords(keyword: str) -> list:
    """챗봇 키워드 문자열("해산물 국수", "해산물,국수")을 단어 목록으로 나눈다"""
    return drop_generic([k for k in str(keyword or "").replace(",", " ").split() if k])


def keyword_hits(pool: pd.DataFrame, kws: list) -> pd.Series:
    """장소마다 키워드 몇 개가 리뷰(reviews_text)나 장소명(name)에 있는지"""
    hits = pd.Series(0, index=pool.index)
    for kw in kws:
        hits += (review_contains(pool["reviews_text"], kw)
                 | pool["name"].str.contains(kw, na=False, case=False)).astype(int)
    return hits


def together_ratio(pool: pd.DataFrame, kws: list) -> pd.Series:
    """키워드가 모두 함께 나온 리뷰의 비율

    키워드가 여러 개일 때 리뷰 어딘가에 각각 있기만 하면 생선구이집도 "흑돼지 구이"에
    걸렸다 ("흑돼지 먹고 왔어요" 리뷰 1개 + "갈치구이 최고" 리뷰 1개). 같은 리뷰 안에
    함께 나와야 그 메뉴에 대한 이야기로 본다. 장소명은 보지 않는다 — "생선구이집"처럼
    이름에 '구이'가 있다고 리뷰의 '흑돼지'와 묶으면 다시 생선구이집이 걸린다.
    (장소명 매칭은 점수의 장소명 가산점으로 따로 반영된다)"""
    def ratio(text: str) -> float:
        parts = [p for p in str(text or "").split(REVIEW_SEP) if p.strip()]
        if not parts:
            return 0.0
        hits = sum(all(text_contains(p, kw) for kw in kws) for p in parts)
        return hits / max(len(parts), MIN_REVIEWS)

    return pool["reviews_text"].fillna("").astype(str).apply(ratio)


def filter_by_keywords(pool: pd.DataFrame, kws: list):
    """후보 거르기 우선순위:
      1. 키워드가 같은 리뷰 안에 함께 나온 곳 (키워드가 2개 이상일 때)
      2. 키워드가 리뷰·장소명 어딘가에 모두 있는 곳
      3. 하나라도 맞는 곳
      4. 없으면 그대로
    (걸러진 pool, 장소별 맞은 키워드 수) 반환"""
    hits = keyword_hits(pool, kws)
    keep = None
    if len(kws) > 1:
        together = together_ratio(pool, kws) > 0
        if together.any():
            keep = together
    if keep is None and (hits == len(kws)).any():
        keep = hits == len(kws)
    if keep is None and (hits > 0).any():
        keep = hits > 0
    if keep is None:
        return pool, hits
    return pool[keep].copy(), hits[keep]
