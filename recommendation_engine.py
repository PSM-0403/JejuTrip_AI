# ============================================================
# recommendation_engine.py  |  여행 코스 추천 엔진
# ============================================================
# 역할: CSV 데이터 기반 추천 로직 (두 가지 모드)
#   1. 자동 추천: 시간대별 슬롯에 맞는 장소 자동 배치
#   2. 직접 구성: 사용자 선택 활동 순서에 장소 매칭
#
# 점수 계산 기준 (📊 CSV 데이터):
#   - 평점(rating) * 10
#   - 리뷰 수(total_cnt) / 10 (최대 20점)
#   - 슬롯 키워드 매칭 +5점/개
#   - 사용자 선호 키워드 매칭 +10점/개 (reviews_text +3점)
#   - 거리 패널티 -0.5점/km
# ============================================================

import random
import pandas as pd
from typing import List, Dict, Optional
from config import TIME_SLOTS, CATEGORIES, OPENAI_MODEL
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
                       pref_slots: Dict = {},  # {day(int): {slot_key: 취향 텍스트}}
                       radius_km: float = 30,
                       chroma_boost: Dict = {}) -> List[Dict]:
        """시간대별 자동 추천 코스 생성  |  📊 CSV 데이터
        일차·슬롯별 독립 취향 적용. 입력 없는 슬롯은 기본 추천."""
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

            slots = []
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

                place = self._pick(df, pick_cat, slot["kw"], ulat, ulng, used, pref_kw, radius_km, chroma_boost)
                if place:
                    used.add(place["name"])
                    pos_rv, neg_rv = self._classify_reviews(place.get("reviews_text", ""))
                    slots.append({
                        "slot":        slot,
                        "place":       place,
                        "reason":      self._reason(place, slot, pref_kw),
                        "pos_reviews": pos_rv,
                        "neg_reviews": neg_rv,
                    })
            itinerary.append({"day": day, "slots": slots, "pref_kw_map": kw_map})

        # OpenAI 추천 사유 보강 (일차별 kw_map 사용)
        if self.ai and any(d.get("pref_kw_map") for d in itinerary):
            itinerary = self._ai_enrich(itinerary)

        return itinerary

    # ── 직접 구성 ───────────────────────────────────────────
    def manual_recommend(self, schedule: List[Dict],
                         ulat: float, ulng: float,
                         cats: List[str], preferences: str = "") -> List[Dict]:
        """사용자 직접 구성 일정에 장소 매칭  |  📊 CSV 데이터"""
        df = self.dm.filter_by_cats(cats)
        used = set()
        days: Dict[int, list] = {}
        pref_kw = self._extract_pref_keywords(preferences) if preferences else []

        for item in schedule:
            day = item["day"]
            cat = self._activity_to_cat(item["activity"])
            slot = {"key": f"manual_{day}", "label": item["activity"],
                    "time": item["time_slot"], "cat": cat, "kw": []}
            place = self._pick(df, cat, [], ulat, ulng, used, pref_kw)
            if place:
                used.add(place["name"])
                days.setdefault(day, []).append({
                    "slot": slot, "place": place,
                    "reason": self._reason(place, slot, pref_kw),
                })

        return [{"day": d, "slots": s, "pref_kw": pref_kw} for d, s in sorted(days.items())]

    # ── 내부: 장소 선택 ─────────────────────────────────────
    def _pick(self, df: pd.DataFrame, cat,   # cat: str 또는 List[str]
              kw: list,
              ulat: float, ulng: float, used: set,
              pref_kw: List[str] = [], radius_km: float = 30,
              chroma_boost: Dict = {}) -> Optional[Dict]:
        """카테고리+키워드+거리+평점 종합 점수로 최적 장소 선택  |  📊 CSV
        cat에 리스트를 넘기면 해당 카테고리들을 통합 풀로 사용 (관광 슬롯 등)"""
        def _cat_filter(d: pd.DataFrame) -> pd.DataFrame:
            if isinstance(cat, list):
                return d[d["category"].isin(cat)]
            return d[d["category"] == cat]

        pool = _cat_filter(df).copy()
        pool = pool[~pool["name"].isin(used)]
        if pool.empty:
            pool = _cat_filter(df).copy()  # used 제한 해제
        if pool.empty:
            return None

        pool = pool.copy()
        # 반경 필터링: 선택한 km 이내 장소만 포함
        pool["_dist"] = pool.apply(
            lambda r: haversine(ulat, ulng, float(r["lat"]), float(r["lng"])), axis=1
        )
        in_radius = pool[pool["_dist"] <= radius_km]
        if not in_radius.empty:
            pool = in_radius.copy()
        # 반경 내 장소가 없으면 필터 없이 전체에서 선택 (fallback)

        # 4-0. 취향 키워드 하드 필터 — 매칭 장소가 있으면 반드시 그 장소들로만 후보 제한
        #      (데이터에 없는 음식/특징을 가진 장소를 추천하는 할루시네이션 방지)
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
        # 5. 거리 패널티
        pool["_score"] -= pool["_dist"].clip(0, 60) * 0.5

        # 상위 5개 중 무작위 1개 (다양성 확보)
        top5 = pool.nlargest(5, "_score")
        return top5.sample(1).iloc[0].to_dict()

    # ── 내부: 추천 이유 생성 ────────────────────────────────
    def _reason(self, place: Dict, slot: Dict, pref_kw: List[str]) -> str:
        """추천 근거 문장 생성  |  📊 CSV 데이터 기반"""
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
                    model="gpt-4o-mini",
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
        예) '카페는 상관없어' → '카페' 제거,  '오션뷰 카페 좋아함' → '카페' 유지"""
        result = []
        for kw in keywords:
            idx = original.find(kw)
            if idx == -1:
                # 원문에 없는 경우 (AI가 바꿔 표현) → 부정 확인 불가, 유지
                result.append(kw)
                continue
            # 키워드 직후 20자 윈도우에서 부정·무관심 표현 탐색
            window = original[idx + len(kw): idx + len(kw) + 20]
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

    def _classify_reviews(self, reviews_text: str):
        """GPT로 리뷰 전체를 읽고 긍정/부정 요약문 생성. 실패 시 키워드 기반 폴백."""
        reviews = [
            r.strip() for r in str(reviews_text).split("|")
            if len(r.strip()) > 10 and r.strip().lower() != "nan"
        ]
        if not reviews:
            return [], []

        # GPT 요약 시도
        if self.ai:
            try:
                import random
                sample = random.sample(reviews, min(20, len(reviews)))
                all_reviews = " / ".join(sample)
                prompt = (
                    f"다음은 한국어 장소 리뷰들이야:\n{all_reviews}\n\n"
                    f"이 리뷰들을 읽고 긍정적인 내용 2가지, 부정적인 내용 2가지를 각각 한 문장씩 요약해줘.\n"
                    f"부정적인 내용이 1가지뿐이면 neg 배열에 1개만, 없으면 빈 배열로.\n"
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

    # ── 내부: 활동 → 카테고리 변환 ─────────────────────────
    @staticmethod
    def _activity_to_cat(activity: str) -> str:
        m = {"카페/디저트": "카페", "산책/자연": "자연",
             "관광/문화": "문화", "맛집": "맛집", "쇼핑/시장": "기타"}
        return m.get(activity, "기타")

    # ── OpenAI 추천 사유 보강 (선택) ─────────────────────────
    def _ai_enrich(self, itinerary: list) -> list:
        """OpenAI로 추천 사유 문장을 자연스럽게 보강 (코스 자체는 변경 안 함).
        일차별 pref_kw_map에서 슬롯 키워드 조회 — 미매칭 장소는 건너뜀."""
        try:
            for day_info in itinerary:
                kw_map = day_info.get("pref_kw_map", {})
                for s in day_info["slots"][:2]:   # 과금 방지: 일차별 2개만
                    p = s["place"]
                    pref_kw = kw_map.get(s["slot"].get("key", ""), [])

                    place_text = (
                        str(p.get("keywords", "")) + " " +
                        str(p.get("reviews_text", "")) + " " +
                        str(p.get("name", ""))
                    ).lower()

                    # 해당 슬롯의 취향 키워드가 장소 데이터에 있는지 확인
                    if pref_kw and not any(w.lower() in place_text for w in pref_kw):
                        continue  # 매칭 안 된 장소는 AI 보강 건너뜀

                    # keywords 컬럼 없는 CSV → reviews_text 앞부분을 참고 데이터로 활용
                    actual_ref = (p.get("keywords") or p.get("reviews_text", ""))[:150]
                    prompt = (
                        f"제주 여행 추천 앱. 추천 장소: {p.get('name')} ({p.get('category')}).\n"
                        f"이 장소의 실제 리뷰/특징: {actual_ref}\n"
                        f"규칙: 위 내용에 없는 정보는 절대 언급하지 말 것.\n"
                        f"이 장소의 특징을 한 문장(20자 내)으로만 설명해줘."
                    )
                    res = self.ai.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=[{"role": "user", "content": prompt}],
                        max_completion_tokens=60,
                    )
                    ai_reason = res.choices[0].message.content.strip()
                    s["reason"] = f"🤖 {ai_reason}"
        except Exception:
            pass   # AI 실패해도 기본 reason 유지
        return itinerary
