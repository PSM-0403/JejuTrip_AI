# ============================================================
# recommendation_engine.py  |  여행 코스 추천 엔진
# ============================================================
# 역할: CSV 데이터 기반 자동 추천 로직
#   - 시간대별 슬롯에 맞는 장소 자동 배치
#
# 추천은 두 단계로 이루어진다:
#   1. 품질 점수 (📊 CSV 데이터, _pick_candidates)
#      - 평점(rating) * 10
#      - 리뷰 수(total_cnt) / 10 (최대 20점)
#      - 슬롯 키워드 매칭 +5점/개
#      - 사용자 선호 키워드 매칭 +20~50점/개
#      → 슬롯별 상위 5개 후보까지만 남김
#   2. 동선 최적화 (_optimize_day_route)
#      - 슬롯 순서(시간대)는 고정한 채, 숙소 출발→...→숙소 복귀 총 이동거리가
#        최소가 되는 후보 조합을 완전탐색으로 선택 (슬롯별 그리디 선택이 아님)
# ============================================================

import random
import pandas as pd
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Optional
from config import TIME_SLOTS, OPENAI_MODEL
from data_manager import DataManager
from kakao_service import KakaoService, haversine

try:
    from openai import OpenAI
    OPENAI_OK = True
except ImportError:
    OPENAI_OK = False
    OpenAI = None


class RecommendationEngine:
    """여행 코스 추천 엔진  |  📊 CSV 데이터 기반"""

    def __init__(self, dm: DataManager,
                 kakao: Optional[KakaoService] = None,
                 openai_key: str = ""):
        self.dm = dm
        self.kakao = kakao
        self.ai = None
        if openai_key and OPENAI_OK:
            try:
                self.ai = OpenAI(api_key=openai_key)
            except Exception:
                pass

    # ── 자동 추천 ───────────────────────────────────────────
    def auto_recommend(self, num_days: int, cats: List[str],
                       ulat: float, ulng: float,
                       pref_slots: Optional[Dict] = None,  # {day(int): {slot_key: 취향 텍스트}}
                       radius_km: float = 30,
                       chroma_boost: Optional[Dict] = None) -> List[Dict]:
        """시간대별 자동 추천 코스 생성  |  📊 CSV 데이터
        일차·슬롯별 독립 취향 적용. 입력 없는 슬롯은 기본 추천."""
        pref_slots = pref_slots or {}
        chroma_boost = chroma_boost or {}
        df = self.dm.filter_by_cats(cats)
        used = set()
        itinerary = []

        for day in range(1, num_days + 1):
            # 이 일차에 입력된 슬롯별 취향 키워드 추출
            day_text = pref_slots.get(day, {})
            kw_map: Dict[str, List[str]] = {
                key: self._extract_pref_keywords(text)
                for key, text in day_text.items()
                if text and text.strip()
            }

            # 1단계: 슬롯별로 후보를 여러 개(최대 5개) 모아둔다 (아직 확정하지 않음)
            slot_candidates = []  # [(slot, pref_kw, [candidate, ...]), ...]
            for slot in TIME_SLOTS:
                slot_cat = slot["cat"]

                if slot_cat in self._SIGHTSEEING_CATS:
                    pick_cat = [c for c in ["자연", "문화", "기타"] if c in cats]
                    if not pick_cat:
                        continue
                else:
                    if slot_cat in cats:
                        pick_cat = slot_cat
                    else:
                        pick_cat = self._fallback_cat(slot_cat, cats)
                        if pick_cat is None:
                            continue
                    if slot_cat in self._DINING_CATS and pick_cat in self._SIGHTSEEING_CATS:
                        continue

                # 이 일차·슬롯에 입력된 취향 키워드 (없으면 빈 리스트 → 기본 추천)
                pref_kw = kw_map.get(slot["key"], [])

                candidates = self._pick_candidates(df, pick_cat, slot["kw"], ulat, ulng, used,
                                                   pref_kw, radius_km, chroma_boost)
                if candidates:
                    slot_candidates.append((slot, pref_kw, candidates))

            # 2단계: 슬롯 순서(시간대)는 고정한 채, 숙소 출발→...→숙소 복귀 총 이동거리가
            # 최소가 되는 후보 조합을 찾는다 (진짜 동선 최적화 — 슬롯별 그리디 선택이 아님)
            chosen = self._optimize_day_route(slot_candidates, ulat, ulng)

            slots = []
            for slot, pref_kw, place in chosen:
                used.add(place["name"])
                slots.append({
                    "slot":        slot,
                    "place":       place,
                    "reason":      self._reason(place, slot, pref_kw, chroma_boost),
                    "pos_reviews": [],  # 아래에서 병렬로 채움
                    "neg_reviews": [],
                })
            itinerary.append({"day": day, "slots": slots, "pref_kw_map": kw_map})

        # 장소별 리뷰 긍정/부정 요약: 장소마다 독립적인 GPT 호출이라 순차 실행하면
        # (최대 7일 x 6곳 = 42회) 코스 생성이 느려짐 → 병렬로 한 번에 처리
        self._classify_reviews_parallel(itinerary)

        return itinerary

    # ── 내부: 슬롯별 후보 장소 목록 ─────────────────────────
    def _pick_candidates(self, df: pd.DataFrame, cat,   # cat: str 또는 List[str]
                         kw: list,
                         ulat: float, ulng: float, used: set,
                         pref_kw: Optional[List[str]] = None, radius_km: float = 30,
                         chroma_boost: Optional[Dict] = None, top_k: int = 5,
                         pool_size: int = 10) -> List[Dict]:
        """카테고리+키워드+평점 종합 품질 점수로 후보를 추려 _optimize_day_route에 넘긴다  |  📊 CSV
        cat에 리스트를 넘기면 해당 카테고리들을 통합 풀로 사용 (관광 슬롯 등)
        radius_km 반경 필터는 항상 숙소(ulat/ulng) 기준.

        거리(동선) 관련 점수는 여기서 매기지 않는다 — 하루치 슬롯의 후보를 모두 모은 뒤
        _optimize_day_route()가 "숙소 출발→...→숙소 복귀" 총 이동거리를 최소화하는 조합을
        따로 찾기 때문에, 여기서는 순수 품질(평점·리뷰·키워드매칭)로만 후보를 추린다.

        상위 pool_size(기본 10)개 중 top_k(기본 5)개를 무작위로 뽑아 반환한다 —
        완전탐색(DP)이 결정론적이라, 이 단계에서 무작위성을 넣지 않으면 같은 조건에선
        항상 똑같은 코스만 나온다. DP는 여기서 뽑힌 후보들 안에서는 여전히 최적 동선을
        찾으므로, "괜찮은 후보들 중 매번 다른 조합 + 그 안에서 최적 동선"이 유지된다."""
        pref_kw = pref_kw or []
        chroma_boost = chroma_boost or {}

        def _cat_filter(d: pd.DataFrame) -> pd.DataFrame:
            if isinstance(cat, list):
                return d[d["category"].isin(cat)]
            return d[d["category"] == cat]

        pool = _cat_filter(df).copy()
        pool = pool[~pool["name"].isin(used)]
        if pool.empty:
            pool = _cat_filter(df).copy()  # used 제한 해제
        if pool.empty:
            return []

        pool = pool.copy()
        # 반경 필터링: 숙소 기준 선택한 km 이내 장소만 포함 (사이드바 설정 그대로 유지)
        pool["_dist"] = pool.apply(
            lambda r: haversine(ulat, ulng, float(r["lat"]), float(r["lng"])), axis=1
        )
        in_radius = pool[pool["_dist"] <= radius_km]
        if not in_radius.empty:
            pool = in_radius.copy()
        # 반경 내 장소가 없으면 필터 없이 전체에서 선택 (fallback)

        # 취향 키워드 하드 필터 — 매칭 장소가 있으면 반드시 그 장소들로만 후보 제한
        # (데이터에 없는 음식/특징을 가진 장소를 추천하는 할루시네이션 방지)
        if pref_kw:
            pref_mask = pd.Series(False, index=pool.index)
            for w in pref_kw:
                pref_mask |= pool["reviews_text"].str.contains(w, na=False, case=False)
                pref_mask |= pool["name"].str.contains(w, na=False, case=False)
            pref_pool = pool[pref_mask]
            if not pref_pool.empty:
                pool = pref_pool.copy()  # 매칭 장소만 사용 (.copy()로 SettingWithCopyWarning 방지)
            # 매칭 없으면 전체 pool 유지 (fallback) — reason에서 ⚠️ 경고 표시됨

        pool["_score"] = 0.0
        # 1. 평점 점수
        pool["_score"] += pool["rating"].fillna(3.5) * 10
        # 2. 리뷰 수 점수 (최대 20)
        pool["_score"] += pool["total_cnt"].fillna(0).clip(0, 200) / 10
        # 3. 슬롯 키워드 매칭 (reviews_text 기반 — keywords 컬럼 없는 CSV 대응)
        for w in kw:
            pool["_score"] += pool["reviews_text"].str.contains(w, na=False, case=False).astype(int) * 5
        # 4. 사용자 취향 키워드 매칭 (하드 필터 통과 후 세부 점수 조정)
        if pref_kw:
            for w in pref_kw:
                rv_hit  = pool["reviews_text"].str.contains(w, na=False, case=False).astype(int)
                nm_hit  = pool["name"].str.contains(w, na=False, case=False).astype(int)
                pool["_score"] += nm_hit * 50
                pool["_score"] += rv_hit * 20
        # 4-1. Chroma 리뷰 유사도 부스트 (취향 입력 시)
        if chroma_boost:
            pool["_score"] += pool["name"].map(chroma_boost).fillna(0)

        top_n = pool.nlargest(min(pool_size, len(pool)), "_score")
        if len(top_n) <= top_k:
            return top_n.to_dict("records")
        return top_n.sample(top_k).to_dict("records")

    # ── 내부: 하루 동선 최적화 ───────────────────────────────
    def _optimize_day_route(self, slot_candidates: List[tuple],
                            ulat: float, ulng: float) -> List[tuple]:
        """슬롯 순서(시간대)는 고정한 채, 각 슬롯의 후보 조합 중
        "숙소 출발 → 슬롯1 → 슬롯2 → ... → 마지막 슬롯 → 숙소 복귀"의
        총 이동거리가 최소가 되는 조합을 완전탐색으로 찾는다.

        슬롯 최대 6개 x 슬롯당 후보 최대 5개 = 최대 5^6(=15,625)가지뿐이라
        완전탐색으로도 충분히 빠르다 (실제로는 카테고리 겹침 등으로 이보다 적음).
        같은 장소가 하루 안에서 중복 선택되지 않도록 방지하고, 한 슬롯의 후보가
        모두 이미 다른 슬롯에서 쓰였다면 그 슬롯은 건너뛴다(기존 동작과 동일).

        slot_candidates: [(slot, pref_kw, [candidate dict, ...]), ...]
        반환: [(slot, pref_kw, chosen place dict), ...] (선택된 슬롯만 포함)
        """
        best: Dict[str, Optional[list]] = {"dist": None, "combo": None}

        def dfs(i: int, prev_lat: float, prev_lng: float,
                used_names: set, acc_dist: float, combo: list):
            if i == len(slot_candidates):
                total = acc_dist + haversine(prev_lat, prev_lng, ulat, ulng)  # 숙소 복귀 거리 포함
                if best["dist"] is None or total < best["dist"]:
                    best["dist"] = total
                    best["combo"] = list(combo)
                return

            slot, pref_kw, candidates = slot_candidates[i]
            available = [c for c in candidates if c["name"] not in used_names]
            if not available:
                # 이 슬롯의 후보가 모두 다른 슬롯에서 이미 쓰임 → 이 슬롯은 건너뛰고 다음으로
                dfs(i + 1, prev_lat, prev_lng, used_names, acc_dist, combo)
                return

            for c in available:
                name = c["name"]
                d = haversine(prev_lat, prev_lng, float(c["lat"]), float(c["lng"]))
                used_names.add(name)
                combo.append((slot, pref_kw, c))
                dfs(i + 1, float(c["lat"]), float(c["lng"]), used_names, acc_dist + d, combo)
                combo.pop()
                used_names.discard(name)

        dfs(0, ulat, ulng, set(), 0.0, [])
        return best["combo"] or []

    # ── 내부: 추천 이유 생성 ────────────────────────────────
    def _reason(self, place: Dict, slot: Dict, pref_kw: List[str],
               chroma_boost: Optional[Dict] = None) -> str:
        """추천 근거 문장 생성  |  📊 CSV 데이터 + 실제 스코어링에 반영된 신호 기반
        (취향 키워드 > 슬롯 기본 키워드 > Chroma 유사도 순으로 가장 관련 높은 매칭 하나만 표시,
        마지막에 숙소 거리를 항상 덧붙여 동선 정보도 함께 보여줌)"""
        chroma_boost = chroma_boost or {}
        parts = []
        r = place.get("rating")
        if r and float(r) >= 4.5:
            parts.append(f"⭐ 평점 {r}")
        cnt = int(place.get("total_cnt", 0) or 0)
        if cnt >= 100:
            parts.append(f"💬 리뷰 {cnt}개")
        if pref_kw:
            matched = False
            for w in pref_kw[:3]:
                rv_hit = w in str(place.get("reviews_text", ""))
                nm_hit = w in str(place.get("name", ""))
                if rv_hit or nm_hit:
                    parts.append(f"🎯 '{w}' 관련 장소")
                    matched = True
                    break
            if not matched:
                # 취향 키워드가 이 장소에 없음을 명시
                parts.append(f"⚠️ '{pref_kw[0]}' 데이터 없음")
        else:
            # 취향 입력이 없을 때: 슬롯 기본 키워드 매칭 → Chroma 유사도 순으로 다음 관련 신호 표시
            rv_text = str(place.get("reviews_text", ""))
            slot_kw_hit = next((w for w in slot.get("kw", []) if w in rv_text), None)
            if slot_kw_hit:
                parts.append(f"🏷️ '{slot_kw_hit}' 키워드 매칭")
            elif place.get("name") in chroma_boost:
                parts.append("🧠 리뷰 유사도 상위 매칭")

        # 동선 정보: 스코어링에 실제 반영된 숙소 거리를 그대로 노출
        dist = place.get("_dist")
        if dist is not None:
            try:
                parts.append(f"📍 숙소 {float(dist):.1f}km")
            except (TypeError, ValueError):
                pass

        if not parts:
            parts.append(f"📍 {slot['label']} 시간대 추천 장소")
        return " · ".join(parts)

    # ── 내부: 취향 입력 → 핵심 검색 키워드 추출 ───────────────
    # 감정·동사·부사 등 검색에 무의미한 노이즈 단어 목록
    _PREF_NOISE = {
        # 감정·선호 표현
        "좋아함", "좋아요", "좋아", "선호", "원함", "원해", "하고싶음", "하고싶어",
        "먹고싶음", "먹고싶어", "가고싶음", "가고싶어", "싫어", "싫음",
        "좋은", "싫은", "원하는", "하는",
        # 부사 (강조어 — 검색 키워드로 무의미)
        "정말", "진짜", "매우", "너무", "아주", "굉장히", "엄청", "완전",
        "꽤", "좀", "조금", "약간", "별로", "그냥", "그저", "꼭", "반드시",
        "항상", "자주", "가끔", "특히", "무조건",
        # 조사·어미 독립형
        "동반", "있음", "없음", "이에요", "예요", "임", "이런", "저런",
        "그런", "같은", "이고", "이나", "또는", "및", "등", "것", "거",
        # 부정·무관심 표현 (단독으로 쓰일 때도 노이즈)
        "상관없어", "상관없음", "상관없는", "상관없고",
        "괜찮아", "괜찮음", "괜찮은",
        "필요없어", "필요없음", "안해도", "안가도", "안먹어도",
        "제외", "빼고", "말고",
    }

    # 부정·무관심 표현 목록 (키워드 뒤에 오면 해당 키워드를 결과에서 제거)
    _NEGATION_MARKERS = {
        "상관없어", "상관없음", "상관없는", "상관없고",
        "괜찮아", "괜찮음",
        "필요없어", "필요없음",
        "싫어", "싫음", "싫은",
        "제외", "빼고", "말고",
        "안해도", "안가도", "안먹어도",
        "별로야", "별로임",
    }

    # 제거할 한국어 조사 목록 (단어 끝에 붙은 조사 제거용)
    _KR_PARTICLES = (
        "를", "을", "이", "가", "은", "는", "도", "만", "에서", "에게",
        "에", "로", "으로", "와", "과", "의", "한테", "께", "서", "부터",
        "까지", "라고", "이라고", "으로서", "로서",
    )

    def _extract_pref_keywords(self, preferences: str) -> List[str]:
        """취향 입력에서 핵심 검색 명사 추출.
        AI 사용 가능 시 LLM으로 정확하게 추출, 없으면 한국어 휴리스틱 폴백.
        두 경로 모두 부정·무관심 표현 짝지어진 키워드 후처리로 제거."""
        if self.ai:
            try:
                prompt = (
                    f"다음 취향 입력에서 장소 검색에 쓸 핵심 명사만 추출해줘.\n"
                    f"입력: '{preferences}'\n"
                    f"규칙:\n"
                    f"1. 음식명·재료·장소특징·활동만 포함.\n"
                    f"2. 감정·동사(좋아함/선호/원함/동반/먹고싶어 등) 제외.\n"
                    f"3. '상관없어', '괜찮아', '싫어', '필요없어', '제외', '빼고', '말고' 등 "
                    f"부정·무관심 표현이 바로 뒤에 오는 키워드는 절대 포함하지 말 것.\n"
                    f"   예) '카페는 상관없어' → 카페 제외 / '해산물 좋아함' → 해산물 포함\n"
                    f"콤마 구분 단어 목록만 반환. 예시) 말고기,흑돼지,바다뷰"
                )
                res = self.ai.chat.completions.create(
                    model=OPENAI_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    max_completion_tokens=60,
                )
                content = res.choices[0].message.content.strip()
                keywords = [k.strip() for k in content.split(",") if k.strip() and len(k.strip()) >= 2]
                # 부정 문맥 재확인 (AI가 놓친 경우 대비)
                keywords = self._remove_negated_keywords(preferences, keywords)
                if keywords:
                    print(f"[AI 키워드 추출] '{preferences}' → {keywords}")
                    return keywords
            except Exception as e:
                print(f"[키워드 추출 오류] {e}")
        result = self._heuristic_keywords(preferences)
        return self._remove_negated_keywords(preferences, result)

    def _remove_negated_keywords(self, original: str, keywords: List[str]) -> List[str]:
        """키워드가 원문에서 부정·무관심 표현과 짝지어진 경우 제거.
        예) '카페는 상관없어' → '카페' 제거,  '오션뷰 카페 좋아함' → '카페' 유지

        탐색 범위는 "다음 키워드가 시작되기 전까지"로 제한한다. 그렇지 않으면
        '흑돼지 좋아하는데 카페는 상관없어'처럼 여러 취향이 한 문장에 섞였을 때,
        뒤쪽 키워드('카페')에 대한 부정 표현이 앞쪽 키워드('흑돼지')의 탐색 범위까지
        침범해 사용자가 명시적으로 원한다고 한 키워드까지 같이 지워져버린다."""
        positions = [original.find(kw) for kw in keywords]
        result = []
        for i, kw in enumerate(keywords):
            idx = positions[i]
            if idx == -1:
                # 원문에 없는 경우 (AI가 바꿔 표현) → 부정 확인 불가, 유지
                result.append(kw)
                continue
            # 키워드 직후 20자, 단 다음 키워드가 그 전에 시작되면 거기서 탐색을 끊는다
            window_end = idx + len(kw) + 20
            for j, other_idx in enumerate(positions):
                if j != i and idx < other_idx < window_end:
                    window_end = other_idx
            window = original[idx + len(kw): window_end]
            negated = any(neg in window for neg in self._NEGATION_MARKERS)
            if negated:
                print(f"[부정 키워드 제거] '{kw}' → 부정/무관심 표현 감지, 검색 제외")
            else:
                result.append(kw)
        return result

    def _heuristic_keywords(self, preferences: str) -> List[str]:
        """AI 없을 때 한국어 휴리스틱으로 핵심 키워드 추출.
        1단계: 부사·감정·동사 노이즈 제거
        2단계: 단어 끝 조사 제거 ('회를' → '회', '흑돼지가' → '흑돼지')
        """
        words = preferences.replace(",", " ").replace(".", " ").split()
        result = []
        for w in words:
            if w in self._PREF_NOISE or len(w) < 1:
                continue
            # 조사 제거: 긴 조사부터 시도해야 짧은 것이 앞 글자를 잘라내지 않음
            clean = w
            for particle in sorted(self._KR_PARTICLES, key=len, reverse=True):
                if clean.endswith(particle) and len(clean) - len(particle) >= 1:
                    clean = clean[: -len(particle)]
                    break
            if len(clean) >= 1:  # 한국어는 1글자도 의미있는 명사일 수 있음 (회, 탕, 국 등)
                result.append(clean)
        print(f"[휴리스틱 키워드 추출] '{preferences}' → {result}")
        return result

    # ── 내부: GPT 리뷰 긍정/부정 분류 ─────────────────────────
    # 키워드 기반 폴백용
    _POS_KW = ["좋아", "맛있", "최고", "추천", "훌륭", "깔끔", "친절", "만족", "완벽", "신선", "맛나", "감동", "좋았", "좋은", "맛집", "대박"]
    _NEG_KW = ["별로", "실망", "나쁘", "최악", "아쉽", "불친절", "비싸", "후회", "형편없", "안 좋", "별점 1", "별점1"]

    def _classify_reviews(self, reviews_text: str, place_name: str = ""):
        """GPT로 리뷰 전체를 읽고 긍정/부정 요약문 생성. 실패 시 키워드 기반 폴백.

        place_name을 프롬프트에 명시하는 이유: 크롤링된 리뷰(특히 블로그 후기)는
        한 리뷰 글 안에 그날 들른 다른 가게·다른 장소 이야기가 섞여 있는 경우가 있다
        (예: 카페 리뷰인데 "짬뽕집 탕수육은 맛있었다"는 무관한 내용이 포함).
        장소명을 모른 채 리뷰 텍스트만 던지면 GPT가 그런 무관한 내용까지 그대로
        요약에 포함시키므로, 반드시 이 장소에 대한 내용만 추리도록 명시한다."""
        reviews = [
            r.strip() for r in str(reviews_text).split("|")
            if len(r.strip()) > 10 and r.strip().lower() != "nan"
        ]
        if not reviews:
            return [], []

        # GPT 요약 시도
        if self.ai:
            try:
                sample = random.sample(reviews, min(20, len(reviews)))
                all_reviews = " / ".join(sample)
                place_ref = place_name or "이 장소"
                prompt = (
                    f"장소명: {place_ref}\n"
                    f"다음은 위 장소에 대해 수집된 한국어 리뷰들이야 (' / '로 구분됨):\n{all_reviews}\n\n"
                    f"주의: 크롤링 특성상 일부 리뷰는 '{place_ref}'와 전혀 무관한 다른 가게 방문기일 수 있어. "
                    f"그런 리뷰는 통째로 완전히 무시하고, '다른 가게 이야기가 있었다' 같은 언급조차 "
                    f"요약에 남기지 마. 오직 '{place_ref}' 자체에 대해 쓰인 문장만 사용해.\n"
                    f"긍정적인 내용과 부정적인 내용을 각각 최대 2가지까지 한 문장씩 요약해줘. "
                    f"'{place_ref}'에 대한 내용이 부족하면 절대 억지로 채우지 말고 있는 만큼만 반환하고, "
                    f"전혀 없으면 반드시 빈 배열로 반환해.\n"
                    f"코드블록 없이 JSON만 반환: {{\"pos\": [\"요약1\", \"요약2\"], \"neg\": [\"요약1\", \"요약2\"]}}"
                )
                res = self.ai.chat.completions.create(
                    model=OPENAI_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    max_completion_tokens=400,
                )
                import json, re
                content = res.choices[0].message.content
                if not content:
                    raise ValueError("empty response from model")
                raw = re.sub(r"```(?:json)?\s*|\s*```", "", content.strip()).strip()
                data = json.loads(raw)
                pos_list = [s.strip() for s in data.get("pos", []) if isinstance(s, str) and s.strip()]
                neg_list = [s.strip() for s in data.get("neg", []) if isinstance(s, str) and s.strip()]
                return pos_list[:2], neg_list[:2]
            except Exception as e:
                print(f"[리뷰 분류 GPT 오류] {e}")

        # 폴백: 키워드 기반 분류
        pos, neg = [], []
        for rv in reviews:
            is_neg = any(w in rv for w in self._NEG_KW)
            is_pos = any(w in rv for w in self._POS_KW)
            if is_neg and not is_pos:
                neg.append(rv)
            else:
                pos.append(rv)
        return pos[:2], neg[:2]

    def _classify_reviews_parallel(self, itinerary: List[Dict]) -> None:
        """코스 전체 장소들의 리뷰 긍정/부정 요약을 병렬로 채운다 (itinerary를 in-place 수정).
        장소마다 독립적인 GPT 호출이라 병렬로 처리하면 코스 생성 시간이 크게 줄어든다."""
        tasks = [s for day in itinerary for s in day.get("slots", [])]
        if not tasks:
            return
        with ThreadPoolExecutor(max_workers=min(10, len(tasks))) as pool:
            future_to_slot = {
                pool.submit(
                    self._classify_reviews,
                    s["place"].get("reviews_text", ""),
                    s["place"].get("name", ""),
                ): s
                for s in tasks
            }
            for future, s in future_to_slot.items():
                try:
                    pos, neg = future.result()
                except Exception:
                    pos, neg = [], []
                s["pos_reviews"] = pos
                s["neg_reviews"] = neg

    # ── 내부: 슬롯 카테고리 미선택 시 유사 카테고리 fallback ──
    # 관광지 슬롯 / 식음료 슬롯 — 절대 교차 불가
    _SIGHTSEEING_CATS = frozenset({"자연", "문화"})   # 관광지 성격
    _DINING_CATS      = frozenset({"카페", "맛집"})   # 식음료 성격

    # 카테고리 성격별 우선순위 대체 목록
    # ※ 자연/문화는 카페·맛집을 대체 후보에 절대 포함하지 않음
    _CAT_FALLBACK = {
        "자연": ["문화", "기타"],      # 자연 없으면 문화(관광지) 우선
        "문화": ["자연", "기타"],      # 문화 없으면 자연 우선
        "카페": ["맛집"],              # 카페 없으면 맛집으로 (디저트 식당 등)
        "맛집": ["카페", "기타"],
        "기타": ["맛집", "카페"],
    }

    @staticmethod
    def _fallback_cat(slot_cat: str, cats: list) -> Optional[str]:
        """슬롯의 원래 카테고리가 선택 안 된 경우, 성격이 가까운 카테고리 반환.
        맞는 것이 없으면 None 반환 → 해당 슬롯 건너뜀."""
        for alt in RecommendationEngine._CAT_FALLBACK.get(slot_cat, []):
            if alt in cats:
                return alt
        return None

# ── AI 코스 브리핑 ────────────────────────────────────────────
def generate_briefing(itinerary: List[Dict], openai_key: str = "") -> str:
    """전체 코스를 한눈에 소개하는 한두 문장 요약 생성.
    실제 선택된 장소 구성에 기반해 작성하도록 지시해 과장/지어내기 방지.
    OpenAI 미사용 시 카테고리 비중 기반 규칙 문장으로 대체."""
    places = [
        (s["slot"]["label"], s["place"])
        for day in itinerary for s in day.get("slots", [])
    ]
    if not places:
        return ""

    if openai_key and OPENAI_OK:
        try:
            client = OpenAI(api_key=openai_key)
            lines = [
                f"{day['day']}일차 {s['slot']['label']}: {s['place'].get('name','')} ({s['place'].get('category','')})"
                for day in itinerary for s in day.get("slots", [])
            ]
            prompt = (
                f"다음은 제주 여행 코스 전체 일정입니다:\n" + "\n".join(lines) + "\n\n"
                f"이 코스의 전체적인 분위기와 특징을 여행자에게 소개하듯 2문장 이내로 요약해줘. "
                f"장소 구성(카테고리 비중, 테마)에 근거해서 작성하고, 일정에 없는 내용은 언급하지 마."
            )
            res = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_completion_tokens=120,
            )
            content = res.choices[0].message.content
            if content and content.strip():
                return content.strip()
        except Exception as e:
            print(f"[코스 브리핑 오류] {e}")

    # 폴백: 카테고리 비중 기반 규칙 요약 (AI 없이도 항상 뭔가 보여줌)
    cat_counts: Dict[str, int] = {}
    for _, p in places:
        c = p.get("category", "기타")
        cat_counts[c] = cat_counts.get(c, 0) + 1
    top_cat = max(cat_counts, key=cat_counts.get)
    num_days = len(itinerary)
    return f"이번 {num_days}일 코스는 총 {len(places)}곳 중 '{top_cat}' 비중이 가장 높은 일정입니다."
