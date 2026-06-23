# ============================================================
# chroma_retriever.py  |  Chroma 리뷰 유사도 검색
# ============================================================
# 역할: preferences 텍스트 → 유사 리뷰 검색 → 장소명 랭킹 반환
#       build_chroma.py 로 DB를 먼저 적재한 후 사용 가능
# ============================================================

import os
from typing import Dict, List

from langchain_openai import OpenAIEmbeddings
from langchain_chroma import Chroma

CHROMA_DIR = "./chroma_jeju_reviews"
COLLECTION  = "jeju_reviews"


def is_chroma_ready() -> bool:
    """Chroma DB 디렉터리가 존재하는지 확인"""
    return os.path.isdir(CHROMA_DIR)


def get_similar_places(
    query: str,
    openai_key: str,
    k: int = 50,
    score_threshold: float = 0.25,
    top_n: int = 20,
) -> Dict[str, float]:
    """
    query와 유사한 리뷰를 Chroma에서 검색하고,
    place_name → 부스트 점수 딕셔너리를 반환한다.

    반환값 예시: {"흑돼지식당": 50.0, "제주갈비집": 42.5, ...}
    순위가 높을수록 높은 부스트 점수 (50점 ~ 최소 5점)
    """
    if not is_chroma_ready():
        return {}

    try:
        embeddings = OpenAIEmbeddings(
            model="text-embedding-3-small",
            api_key=openai_key,
        )
        vectorstore = Chroma(
            collection_name=COLLECTION,
            embedding_function=embeddings,
            persist_directory=CHROMA_DIR,
        )
        retriever = vectorstore.as_retriever(
            search_type="similarity_score_threshold",
            search_kwargs={"k": k, "score_threshold": score_threshold},
        )
        results = retriever.invoke(query)

        # place_name 중복 제거 (첫 등장 = 가장 유사도 높은 리뷰)
        seen: set = set()
        ranked: List[str] = []
        for doc in results:
            pname = doc.metadata.get("place_name")
            if pname and pname not in seen:
                seen.add(pname)
                ranked.append(pname)
            if len(ranked) >= top_n:
                break

        # 순위 기반 부스트: 1위=50점, 이후 2.5점씩 감소, 최소 5점
        boost: Dict[str, float] = {}
        for rank, name in enumerate(ranked):
            score = max(5.0, 50.0 - rank * 2.5)
            boost[name] = score

        return boost

    except Exception as e:
        print(f"[Chroma 검색 오류] {e}")
        return {}
