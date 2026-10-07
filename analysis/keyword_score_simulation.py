# ============================================================
# analysis/keyword_score_simulation.py  |  취향 키워드 점수 방식 비교
# ============================================================
# 실행: python analysis/keyword_score_simulation.py   (저장소 루트에서)
#
# recommendation_engine._pick_candidates 의 점수 계산을 단순화해 재현하고,
# 취향 키워드 점수를 두 방식으로 계산해 후보 상위 10곳을 비교한다.
#   현재 방식: 리뷰에 키워드가 한 번이라도 있으면 +20
#   비율 방식: +20 × (키워드가 나온 리뷰 수 / max(리뷰 수, MIN_REVIEWS))
# 반경 필터, 슬롯 키워드, Chroma 부스트는 빼고 품질 점수만 비교한다.
# CSV는 읽기만 한다.
# ============================================================

import os

import pandas as pd

CSV_PATH = os.path.join(os.path.dirname(__file__), "..", "jeju_crawling_100.csv")
ENCODINGS = ["utf-8", "utf-8-sig", "cp949", "euc-kr"]
SEP = " | "
TOP_N = 10          # _pick_candidates 의 pool_size 기본값
MIN_REVIEWS = 10    # 리뷰가 적은 곳은 비율이 과하게 커지지 않도록 분모 하한

CASES = {
    "카페":     ["조용", "오션뷰", "디저트"],   # "뷰"는 "리뷰"에도 걸려서 제외 (아래 참고)
    "음식점":   ["흑돼지", "가성비", "해장"],
    "관광명소": ["바다", "산책"],
}


def load_csv(path):
    for enc in ENCODINGS:
        try:
            return pd.read_csv(path, encoding=enc)
        except UnicodeDecodeError:
            continue
    raise ValueError("CSV 인코딩을 읽을 수 없음")


df = load_csv(CSV_PATH).dropna(how="all").copy()
df["reviews"] = df["reviews_text"].fillna("").apply(
    lambda t: [r.strip() for r in t.split(SEP) if r.strip()]
)
df["n_reviews"] = df["reviews"].apply(len)
df["base"] = df["rating"].fillna(3.5) * 10 + df["total_cnt"].fillna(0).clip(0, 200) / 10

rows = []
for cat, keywords in CASES.items():
    d = df[df["category_group_name"] == cat]
    for w in keywords:
        rv_hit = d["reviews_text"].str.contains(w, na=False)
        nm_hit = d["place_name"].str.contains(w, na=False)
        pool = d[rv_hit | nm_hit].copy()                  # 하드 필터
        pool["hits"] = pool["reviews"].apply(lambda rs: sum(w in r for r in rs))
        pool["share"] = pool["hits"] / pool["n_reviews"].clip(lower=MIN_REVIEWS)
        name_pts = pool["place_name"].str.contains(w).astype(int) * 50
        rv_pts = pool["reviews_text"].str.contains(w, na=False).astype(int) * 20

        pool["score_now"] = pool["base"] + name_pts + rv_pts
        pool["score_new"] = pool["base"] + name_pts + 20 * pool["share"]

        top_now = pool.nlargest(TOP_N, "score_now")
        top_new = pool.nlargest(TOP_N, "score_new")
        kept = len(set(top_now.index) & set(top_new.index))
        rows.append({
            "카테고리": cat,
            "키워드": w,
            "후보 수": len(pool),
            "후보 중 키워드 점수 받은 비율": f"{(rv_pts > 0).mean():.0%}",
            "상위10 유지": kept,
            "키워드 언급 비율(현재)": f"{top_now['share'].mean():.0%}",
            "키워드 언급 비율(비율 방식)": f"{top_new['share'].mean():.0%}",
            "평균 평점(현재)": round(top_now["rating"].mean(), 2),
            "평균 평점(비율 방식)": round(top_new["rating"].mean(), 2),
        })

result = pd.DataFrame(rows)
pd.set_option("display.width", 200)
print(result.to_string(index=False))

# 부분 문자열 매칭 문제: "뷰"는 "리뷰"라는 단어에도 걸린다
cafe = df[df["category_group_name"] == "카페"]
pool = cafe[cafe["reviews_text"].str.contains("뷰", na=False)]
only_review_word = (~pool["reviews_text"].str.replace("리뷰", "").str.contains("뷰")).sum()
print()
print(f"카페 '뷰' 후보 {len(pool)}곳 중 '리뷰'라는 단어 때문에만 걸린 곳: {only_review_word}곳")
