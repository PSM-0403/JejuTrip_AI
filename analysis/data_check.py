# ============================================================
# analysis/data_check.py  |  jeju_crawling_100.csv 데이터 점검
# ============================================================
# 실행: python analysis/data_check.py   (저장소 루트에서)
#
# CSV는 읽기만 하고 수정하지 않는다.
# 점검 항목:
#   1. 행 수·빈 행·결측·중복
#   2. 카테고리 분포
#   3. 장소당 리뷰 수 분포
#   4. 리뷰 4개 장소 (블로그 미리보기 여부)
#   5. 텍스트 품질 (이모지 손실, 아주 짧은 리뷰, 장소 간 중복 리뷰)
#   6. 평점·후기 수 분포와 추천 점수에서의 영향
# ============================================================

import os
import re
from collections import defaultdict

import pandas as pd

CSV_PATH = os.path.join(os.path.dirname(__file__), "..", "jeju_crawling_100.csv")
ENCODINGS = ["utf-8", "utf-8-sig", "cp949", "euc-kr"]   # data_manager.py와 같은 순서
SEP = " | "                                              # reviews_text 리뷰 구분자


def load_csv(path):
    for enc in ENCODINGS:
        try:
            return pd.read_csv(path, encoding=enc), enc
        except UnicodeDecodeError:
            continue
    raise ValueError("CSV 인코딩을 읽을 수 없음")


def split_reviews(text):
    if pd.isna(text):
        return []
    return [t.strip() for t in str(text).split(SEP) if t.strip()]


def section(title):
    print(f"\n── {title} " + "─" * (50 - len(title)))


raw, enc = load_csv(CSV_PATH)

# 1. 행 수·빈 행·결측·중복 ──────────────────────────────
section("1. 행 수 · 결측 · 중복")
empty_rows = raw.isna().all(axis=1)
df = raw[~empty_rows].copy()
print(f"인코딩: {enc}")
print(f"전체 행: {len(raw):,}  /  빈 행: {empty_rows.sum()}  /  장소: {len(df):,}")
print(f"리뷰 없는 장소: {df['reviews_text'].isna().sum()}")
print(f"장소명 중복: {df['place_name'].duplicated().sum()}  /  URL 중복: {df['place_url'].duplicated().sum()}")

# 2. 카테고리 분포 ──────────────────────────────────────
section("2. 카테고리")
print(df["category_group_name"].value_counts().to_string())

# 3. 장소당 리뷰 수 ─────────────────────────────────────
section("3. 장소당 리뷰 수")
df["reviews"] = df["reviews_text"].apply(split_reviews)
df["n_reviews"] = df["reviews"].apply(len)
print(f"리뷰 합계: {df['n_reviews'].sum():,}")
print(df["n_reviews"].describe().round(1).to_string())
print(f"20개 초과 장소: {(df['n_reviews'] > 20).sum()}  /  최대: {df['n_reviews'].max()}")
top = df["n_reviews"].value_counts().head(5)
print("가장 많은 리뷰 수 값 (리뷰 수: 장소 수):", ", ".join(f"{k}개: {v}" for k, v in top.items()))

# 4. 리뷰 4개 장소 ──────────────────────────────────────
section("4. 리뷰가 정확히 4개인 장소")
four = df[df["n_reviews"] == 4]
four_len = four["reviews"].explode().str.len()
other_len = df[df["n_reviews"] != 4]["reviews"].explode().str.len()
print(f"장소 수: {len(four)}")
print(f"리뷰 길이 중앙값: 4개 장소 {four_len.median():.0f}자  /  나머지 {other_len.median():.0f}자")
print(f"길이 190~200자 비율: 4개 장소 {four_len.between(190, 200).mean():.0%}  /  나머지 {other_len.between(190, 200).mean():.0%}")
sponsored = four["reviews"].explode().str.contains("협찬|제공받|원고료|소정의", na=False)
print(f"협찬 표기가 있는 글: {sponsored.sum()}개 ({sponsored.groupby(level=0).any().sum()}곳)")
print("카테고리:", four["category_group_name"].value_counts().to_dict())

# 5. 텍스트 품질 ────────────────────────────────────────
section("5. 텍스트 품질")
all_reviews = df["reviews"].explode().dropna()
broken = all_reviews.str.contains(r"\?\?")
print(f"'??'가 들어간 리뷰: {broken.sum():,}개 ({broken.groupby(level=0).any().sum()}곳)  ← 이모지가 cp949 저장에서 깨진 흔적")
print(f"2자 이하 리뷰: {(all_reviews.str.len() <= 2).sum()}개")

owners = defaultdict(set)
for idx, reviews in df["reviews"].items():
    for t in set(reviews):
        if len(t) > 15:
            owners[t].add(idx)
shared = {t: o for t, o in owners.items() if len(o) > 1}
print(f"여러 장소에 똑같이 들어간 리뷰: {len(shared)}개 (관련 장소 {len(set().union(*shared.values())) if shared else 0}곳)")

# 6. 평점·후기 수와 추천 점수 ───────────────────────────
section("6. 평점 · 후기 수 분포와 추천 점수")
q = df["rating"].quantile([0.1, 0.25, 0.5, 0.75, 0.9])
print("평점 분위수:", ", ".join(f"{int(k * 100)}%={v}" for k, v in q.items()))
print("카테고리별 평점 중앙값:", df.groupby("category_group_name")["rating"].median().to_dict())
iqr_points = (q[0.75] - q[0.25]) * 10
print(f"recommendation_engine 점수: 평점 × 10 → 중간 50% 장소 간 차이 {iqr_points:.0f}점")
cap = (df["total_cnt"] >= 200).mean()
print(f"후기 수 점수는 200개에서 상한(20점) → 상한에 걸린 장소 {cap:.0%}")
print("취향 키워드 매칭: 리뷰 +20점, 장소명 +50점 (평점 차이보다 큼)")

# 리뷰 수가 많을수록 키워드가 리뷰에 걸릴 확률이 높은지
bins = pd.cut(df["n_reviews"], [0, 10, 20, 50], labels=["1~10개", "11~20개", "21개 이상"])
for kw in ["바다", "조용", "가성비"]:
    hit = df["reviews_text"].str.contains(kw, na=False)
    rates = hit.groupby(bins, observed=True).mean()
    print(f"'{kw}' 매칭률 —", ", ".join(f"{k} {v:.0%}" for k, v in rates.items()))
