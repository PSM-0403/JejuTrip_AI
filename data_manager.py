# ============================================================
# data_manager.py  |  CSV 데이터 로딩 및 관리
# ============================================================
# 역할: jeju_crawling_100.csv를 읽고 표준화
#   - 인코딩 자동 감지 (cp949 / utf-8 등)
#   - 카테고리 정규화 (5개 표준 카테고리)
#   - 텍스트 검색, 카테고리 필터링 제공
# 데이터 출처: 📊 CSV 데이터 (팀 직접 수집)
# ============================================================

import os
import pandas as pd
import streamlit as st
from config import CATEGORY_MAP


class DataManager:
    """data.csv 기반 장소 데이터 관리자  |  📊 CSV 데이터"""

    CSV_FILES  = ["jeju_crawling_100.csv"]
    ENCODINGS  = ["utf-8", "utf-8-sig", "cp949", "euc-kr"]
    # CSV에 반드시 있어야 할 컬럼 목록
    REQUIRED   = ["name", "category", "lat", "lng", "address",
                  "rating", "total_cnt", "reviews_text", "keywords", "place_url"]

    def __init__(self):
        self.df: pd.DataFrame = pd.DataFrame()
        self._load()

    # ── 내부: 로딩 ──────────────────────────────────────────
    def _load(self):
        """CSV 파일을 자동으로 찾아 로딩"""
        for path in self.CSV_FILES:
            if not os.path.exists(path):
                continue
            for enc in self.ENCODINGS:
                try:
                    df = pd.read_csv(path, encoding=enc)
                    self.df = self._clean(df)
                    return
                except UnicodeDecodeError:
                    continue
                except Exception as e:
                    st.error(f"CSV 로딩 오류: {e}")
                    return
        st.warning("⚠️ jeju_crawling_100.csv를 찾지 못했습니다. 파일 경로를 확인해주세요.")

    # jeju_crawling_100.csv 컬럼 → 표준 컬럼 매핑
    COL_RENAME = {
        "place_name": "name",
        "x": "lng",
        "y": "lat",
        "address_name": "address",
        "category_group_name": "category",
    }

    def _clean(self, raw: pd.DataFrame) -> pd.DataFrame:
        """컬럼 정규화, 타입 변환, 카테고리 매핑"""
        df = raw.copy()
        # 컬럼명 표준화 (jeju_crawling_100.csv 지원)
        df = df.rename(columns=self.COL_RENAME)
        # 없는 컬럼은 빈 문자열/0으로 채움
        for col in self.REQUIRED:
            if col not in df.columns:
                df[col] = "" if col not in ["total_cnt", "rating"] else 0
        # 타입 변환
        df["name"]         = df["name"].fillna("").astype(str).str.strip()
        df["address"]      = df["address"].fillna("").astype(str)
        df["lat"]          = pd.to_numeric(df["lat"], errors="coerce")
        df["lng"]          = pd.to_numeric(df["lng"], errors="coerce")
        df["rating"]       = pd.to_numeric(df["rating"], errors="coerce")
        df["total_cnt"]    = pd.to_numeric(df["total_cnt"], errors="coerce").fillna(0)
        df["reviews_text"] = df["reviews_text"].fillna("").astype(str)
        df["keywords"]     = df["keywords"].fillna("").astype(str)
        df["place_url"]    = df["place_url"].fillna("").astype(str)
        # 카테고리 정규화 → 5개 표준 카테고리
        df["category"]     = df["category"].fillna("기타").astype(str).apply(self._norm_cat)
        # 데이터 출처 컬럼 추가 (UI에서 뱃지로 표시)
        df["data_source"]  = "CSV"
        # 좌표 없는 행 제거
        return df.dropna(subset=["lat", "lng"]).reset_index(drop=True)

    def _norm_cat(self, raw: str) -> str:
        """CSV 카테고리 값 → 5개 표준 카테고리 변환"""
        raw = raw.strip()
        for std, aliases in CATEGORY_MAP.items():
            if raw == std or raw in aliases:
                return std
        return "기타"

    # ── 공개 메서드 ─────────────────────────────────────────
    def filter_by_cats(self, cats: list) -> pd.DataFrame:
        """선택한 카테고리 목록으로 필터링  |  📊 CSV"""
        if not cats:
            return self.df.copy()
        return self.df[self.df["category"].isin(cats)].copy()

    def search_text(self, text: str) -> pd.DataFrame:
        """키워드/리뷰/장소명 전문 검색  |  📊 CSV"""
        if not text.strip():
            return self.df.copy()
        t = text.strip()
        mask = (
            self.df["name"].str.contains(t, case=False, na=False) |
            self.df["keywords"].str.contains(t, case=False, na=False) |
            self.df["reviews_text"].str.contains(t, case=False, na=False)
        )
        return self.df[mask].copy()

    def stats(self) -> dict:
        """카테고리별 통계  |  📊 CSV"""
        return {
            "total": len(self.df),
            "by_cat": self.df["category"].value_counts().to_dict(),
        }
