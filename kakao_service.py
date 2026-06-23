# ============================================================
# kakao_service.py  |  카카오 REST API 래퍼
# ============================================================
# 역할: 카카오 API 기반 실시간 데이터 제공  🗺️ 카카오 API
#   1. API 키 유효성 테스트
#   2. 숙소/출발지 검색 (키워드 + 주소 → 장소 목록)
#   3. 카카오 네비게이션 경로 계산 (거리/시간)
#   4. 직선거리(Haversine) 계산 유틸
# ============================================================

import requests
import math
from typing import List, Dict, Optional


# ── 유틸: 직선거리 계산 ─────────────────────────────────────
def haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """두 좌표 간 직선거리(km) 반환  |  수식 계산 (API 불필요)"""
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlng / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(max(0, a)))


class KakaoService:
    """카카오 REST API 통합 서비스  🗺️ 카카오 API"""

    SEARCH_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"
    ADDRESS_URL = "https://dapi.kakao.com/v2/local/search/address.json"
    NAVI_URL   = "https://apis-navi.kakaomobility.com/v1/directions"
    JEJU_X, JEJU_Y = "126.5311884", "33.4996213"   # 제주 중심 좌표 (카카오 기본)

    def __init__(self, api_key: str = ""):
        self.key = (api_key or "").strip()
        self.headers = {"Authorization": f"KakaoAK {self.key}"} if self.key else {}

    # ── API 키 유효성 테스트 ─────────────────────────────────
    def test_connection(self) -> bool:
        """카카오 API 연결 테스트  🗺️ 카카오 API"""
        if not self.key:
            return False
        try:
            r = requests.get(
                self.SEARCH_URL,
                headers=self.headers,
                params={"query": "제주", "size": 1},
                timeout=5,
            )
            return r.status_code == 200
        except Exception:
            return False

    # ── 숙소 검색 ────────────────────────────────────────────
    def search_accommodation(self, name: str) -> List[Dict]:
        """숙소명·브랜드명·주소로 출발지 검색  🗺️ 카카오 API"""
        keyword = " ".join((name or "").split())
        if not self.key or not keyword:
            return []

        # 변경: '그랜드 하얏트'처럼 띄어쓰기/브랜드 표기가 흔들리는 입력도 잡기 위해 검색어 변형을 만든다.
        query_candidates = self._build_search_queries(keyword)

        # 변경: 제주 여행 앱이므로 제주 반경 키워드 검색을 먼저 수행해 제주 결과를 우선 노출한다.
        merged_results: List[Dict] = []
        for query in query_candidates:
            merged_results.extend(self._keyword_search(query, size=15, page=1, use_jeju_bias=True))
            merged_results.extend(self._keyword_search(query, size=15, page=2, use_jeju_bias=True))

        # 변경: 도로명/지번 주소 입력도 바로 찾을 수 있도록 주소 검색을 함께 수행한다.
        merged_results.extend(self._address_search(keyword, size=10))
        unique_results = self._dedupe_results(merged_results)

        # 변경: 제주 우선 검색 결과가 부족하면 전국 검색으로 한 번 더 확장해 카카오맵 검색 체감에 가깝게 맞춘다.
        if len(unique_results) < 3:
            nationwide_results: List[Dict] = []
            for query in query_candidates:
                nationwide_results.extend(self._keyword_search(query, size=15, page=1, use_jeju_bias=False))
            unique_results = self._dedupe_results(unique_results + nationwide_results)

        return unique_results[:30]

    # 변경: 사용자 입력을 제주 우선/전국 검색 모두에 재사용할 수 있도록 검색어 후보를 정리한다.
    def _build_search_queries(self, keyword: str) -> List[str]:
        queries: List[str] = []

        def add_query(query: str):
            normalized = " ".join(query.split())
            if normalized and normalized not in queries:
                queries.append(normalized)

        add_query(keyword)

        compact_keyword = keyword.replace(" ", "")
        if compact_keyword != keyword:
            add_query(compact_keyword)

        if "제주" not in keyword:
            add_query(f"제주 {keyword}")
            add_query(f"{keyword} 제주")
            if compact_keyword != keyword:
                add_query(f"제주 {compact_keyword}")
                add_query(f"{compact_keyword} 제주")

        return queries

    def _keyword_search(self, query: str, size: int = 15, page: int = 1,
                        use_jeju_bias: bool = True) -> List[Dict]:
        """카카오 키워드 장소 검색 공통 메서드  🗺️ 카카오 API"""
        if not self.key:
            return []
        try:
            params = {"query": query, "size": size, "page": page, "sort": "accuracy"}
            if use_jeju_bias:
                params.update({"x": self.JEJU_X, "y": self.JEJU_Y, "radius": 60000})

            r = requests.get(
                self.SEARCH_URL,
                headers=self.headers,
                params=params,
                timeout=5,
            )
            if r.status_code != 200:
                return []
            return r.json().get("documents", [])
        except Exception:
            return []

    # 변경: 주소 검색 결과를 keyword 검색 결과와 같은 형태로 맞춰 UI에서 바로 재사용한다.
    def _address_search(self, query: str, size: int = 5) -> List[Dict]:
        """카카오 주소 검색 결과를 장소 목록 형식으로 정규화  🗺️ 카카오 API"""
        if not self.key:
            return []
        try:
            r = requests.get(
                self.ADDRESS_URL,
                headers=self.headers,
                params={"query": query, "size": size, "analyze_type": "similar"},
                timeout=5,
            )
            if r.status_code != 200:
                return []

            normalized_results: List[Dict] = []
            for doc in r.json().get("documents", []):
                road = doc.get("road_address") or {}
                address_name = doc.get("address_name", "")
                x = road.get("x") or doc.get("x")
                y = road.get("y") or doc.get("y")
                if not x or not y:
                    continue

                normalized_results.append({
                    "id": f"address:{address_name}:{x}:{y}",
                    "place_name": road.get("building_name") or road.get("address_name") or address_name,
                    "road_address_name": road.get("address_name", ""),
                    "address_name": address_name,
                    "x": x,
                    "y": y,
                    "place_url": "",
                    "category_name": "주소 검색 결과",
                })

            return normalized_results
        except Exception:
            return []

    # 변경: 키워드 검색과 주소 검색 결과를 합칠 때 같은 장소가 반복 노출되지 않도록 중복 제거한다.
    def _dedupe_results(self, results: List[Dict]) -> List[Dict]:
        """검색 결과를 입력 순서대로 유지하면서 중복 제거  🗺️ 카카오 API"""
        unique_results: List[Dict] = []
        seen = set()

        for item in results:
            dedupe_key = (
                item.get("id", ""),
                item.get("place_name", ""),
                item.get("road_address_name", ""),
                item.get("address_name", ""),
                item.get("x", ""),
                item.get("y", ""),
            )
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            unique_results.append(item)

        return unique_results

    # ── 장소 전화번호 조회 ───────────────────────────────────
    def get_phone(self, name: str, lat: float, lng: float) -> str:
        """장소명 + 좌표로 카카오 검색 후 전화번호 반환  🗺️ 카카오 API"""
        if not self.key or not name:
            return ""
        try:
            r = requests.get(
                self.SEARCH_URL,
                headers=self.headers,
                params={"query": name, "x": str(lng), "y": str(lat), "radius": 300, "size": 1},
                timeout=5,
            )
            if r.status_code != 200:
                return ""
            docs = r.json().get("documents", [])
            return docs[0].get("phone", "") if docs else ""
        except Exception:
            return ""

    # ── 네비게이션 경로 계산 ─────────────────────────────────
    def get_route(self, ox: float, oy: float, dx: float, dy: float) -> Optional[Dict]:
        """카카오 네비게이션 기준 경로·시간·거리 계산  🗺️ 카카오 API

        Args:
            ox, oy: 출발 경도·위도
            dx, dy: 도착 경도·위도
        Returns:
            {distance_km, duration_min, source} 또는 None
        """
        if not self.key:
            return None
        try:
            r = requests.get(
                self.NAVI_URL,
                headers=self.headers,
                params={"origin": f"{ox},{oy}", "destination": f"{dx},{dy}"},
                timeout=8,
            )
            if r.status_code != 200:
                return None
            routes = r.json().get("routes", [])
            if not routes:
                return None
            s = routes[0].get("summary", {})
            return {
                "distance_km":  round(s.get("distance", 0) / 1000, 1),
                "duration_min": round(s.get("duration", 0) / 60),
                "source": "카카오 네비게이션 API",   # 출처 명시
            }
        except Exception:
            return None
