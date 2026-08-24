# ============================================================
# app.py  |  제주 여행 맞춤 추천 v4.0  메인 앱
# ============================================================
# 실행: streamlit run app.py
#
# 전체 기능:
#   1. API 키 수동 입력 + 연결 상태 자동 확인
#   2. 숙소 검색 (🗺️ 카카오 API)
#   3. 여행 기간 / 카테고리 / AI 맞춤 조건 설정
#   4. 자동 추천 코스 생성 OR 사용자 직접 구성
#   5. 결과: 일차별 탭 + 전체 지도 탭 + 코스 분석 탭
#   6. 우측 하단 플로팅 AI 챗봇 (OpenAI)
# ============================================================

import streamlit as st
import datetime
import json
from config import PAGE_CONFIG, CATEGORIES, KAKAO_API_KEY, OPENAI_API_KEY
from data_manager import DataManager
from kakao_service import KakaoService
from recommendation_engine import RecommendationEngine
from ui_components import render_day_course, render_full_map, render_analysis
from chatbot import render_chatbot
from chroma_retriever import is_chroma_ready, get_similar_places

# ── 페이지 설정 ─────────────────────────────────────────────
st.set_page_config(**PAGE_CONFIG)

# ── 플로팅 챗봇 버튼 CSS ────────────────────────────────────
st.markdown("""
<style>
div[data-testid="stAppViewContainer"] { background: #e0f2fe; }
section[data-testid="stMain"] { background: #e0f2fe; }
div[data-testid="stMainBlockContainer"] { background: #e0f2fe; }
.source-tip { font-size:12px; color:#64748b; border-left:3px solid #3b82f6;
              padding:4px 8px; margin:4px 0; background:#eff6ff; border-radius:4px; }
</style>
""", unsafe_allow_html=True)


# ── 세션 상태 초기화 ────────────────────────────────────────
def _init():
    defaults = dict(
        kakao_key=KAKAO_API_KEY,
        openai_key=OPENAI_API_KEY,
        kakao_ok=False, openai_ok=False,
        user_lat=33.4996213, user_lng=126.5311884,
        stay_name="제주시 (기본 출발지)",
        itinerary=[], chat_open=False,
    )
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

def _check_api():
    """앱 시작 시 .env 키로 연결 상태 자동 확인"""
    if not st.session_state.kakao_ok and st.session_state.kakao_key:
        ks = KakaoService(st.session_state.kakao_key)
        st.session_state.kakao_ok = ks.test_connection()
    if not st.session_state.openai_ok and st.session_state.openai_key:
        try:
            from openai import OpenAI
            OpenAI(api_key=st.session_state.openai_key).models.list()
            st.session_state.openai_ok = True
        except Exception as e:
            print("OPENAI CONNECT ERROR:", repr(e))
            st.session_state.openai_ok = False

_init()
_check_api()


# ════════════════════════════════════════════════════════════
# 사이드바
# ════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown("## 🍊 JejuTrip AI")
    st.caption("📊 CSV 데이터 + 🗺️ 카카오 API")
    st.divider()

    # ── 1. API 연결 상태 ────────────────────────────────────
    kakao_status  = "✅ 연결됨" if st.session_state.kakao_ok  else ("❌ 연결 실패" if st.session_state.kakao_key  else "⚠️ 키 없음")
    openai_status = "✅ 연결됨" if st.session_state.openai_ok else ("❌ 연결 실패" if st.session_state.openai_key else "⚠️ 키 없음")
    st.caption(f"🔑 카카오 API: {kakao_status}")
    st.caption(f"🔑 OpenAI API: {openai_status}")

    st.divider()

    # ── 2. 저장된 코스 불러오기 ──────────────────────────────
    today = datetime.date.today()
    st.markdown("### 📂 저장된 코스 불러오기")
    uploaded = st.file_uploader("코스 JSON 파일 선택", type=["json"], key="course_upload",
                                label_visibility="collapsed")
    if uploaded is not None and st.session_state.get("_last_loaded_file") != uploaded.name:
        try:
            data = json.load(uploaded)
            loaded_itin = (data.get("itinerary") or [])[:7]
            loaded_days = max(1, len(loaded_itin))
            st.session_state.itinerary = loaded_itin
            st.session_state.stay_name = data.get("stay_name", st.session_state.stay_name)
            st.session_state.user_lat  = float(data.get("user_lat", st.session_state.user_lat))
            st.session_state.user_lng  = float(data.get("user_lng", st.session_state.user_lng))
            st.session_state["trip_dates"] = (today, today + datetime.timedelta(days=loaded_days - 1))
            st.session_state["_last_loaded_file"] = uploaded.name
            st.success(f"✅ 저장된 코스를 불러왔습니다 ({loaded_days}일 코스)")
            st.rerun()
        except Exception as e:
            st.error(f"⚠️ 코스 파일을 읽지 못했습니다: {e}")
    st.caption("이전에 저장(💾)한 JSON 파일을 올리면 코스·숙소 위치가 복원됩니다")

    st.divider()

    # ── 3. 숙소 검색 (🗺️ 카카오 API) ──────────────────────
    st.markdown("### 🏨 숙소 / 출발지 설정")
    # 변경: 숙소명뿐 아니라 브랜드명·도로명 주소·지번 주소도 같은 입력창에서 검색할 수 있게 안내 문구를 확장한다.
    st.caption("🗺️ 카카오 API로 숙소명·브랜드명·주소를 실시간 검색합니다")
    accom_q = st.text_input(
        "숙소명 또는 주소 입력",
        placeholder="예: 그랜드 하얏트 제주, 제주 노연로 12, 제주특별자치도 제주시 ..."
    )

    if accom_q and st.session_state.kakao_ok:
        ks = KakaoService(st.session_state.kakao_key)
        results = ks.search_accommodation(accom_q)
        if results:
            # 변경: 주소 검색 결과도 섞여 들어오므로 선택 목록에서 결과 유형을 함께 보여준다.
            options = [
                f"{r['place_name']}  ({r.get('road_address_name') or r.get('address_name','')})"
                f"{' [주소]' if r.get('category_name') == '주소 검색 결과' else ''}"
                for r in results
            ]
            sel_idx = st.selectbox("🗺️ 카카오 검색 결과", range(len(options)),
                                   format_func=lambda i: options[i])
            if st.button("✅ 이 숙소로 설정", use_container_width=True):
                r = results[sel_idx]
                st.session_state.user_lat  = float(r["y"])
                st.session_state.user_lng  = float(r["x"])
                st.session_state.stay_name = r["place_name"]
                st.success(f"📍 {r['place_name']} 선택 완료")
        else:
            st.warning("검색 결과 없음. 숙소명, 건물명, 도로명 주소, 지번 주소를 다시 확인해주세요.")
    elif accom_q and not st.session_state.kakao_ok:
        st.info("카카오 API 키를 먼저 입력하고 연결 확인을 해주세요.")

    st.caption(f"📍 현재 기준 위치: **{st.session_state.stay_name}**")
    st.divider()

    # ── 4. 여행 설정 ────────────────────────────────────────
    st.markdown("### ⚙️ 여행 설정")
    if "trip_dates" not in st.session_state:
        st.session_state["trip_dates"] = (today, today + datetime.timedelta(days=1))
    date_range = st.date_input(
        "🗓️ 여행 기간",
        key="trip_dates",
        min_value=today,
    )
    if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
        start_date, end_date = date_range[0], date_range[1]
    elif isinstance(date_range, (list, tuple)) and len(date_range) == 1:
        start_date = end_date = date_range[0]
    else:
        start_date = end_date = date_range
    num_days   = max(1, (end_date - start_date).days + 1)
    st.caption(f"총 {num_days}일 여행")
    if num_days > 7:
        st.warning("⚠️ 최대 7일까지 추천 가능합니다.")

    st.markdown("**📁 카테고리 선택**  *(📊 CSV 데이터 기준)*")
    all_sel = st.checkbox("전체 카테고리 선택", value=True, key="cat_all")
    if all_sel:
        sel_cats = CATEGORIES.copy()
        for c in CATEGORIES:
            st.checkbox(c, value=True, disabled=True, key=f"c_{c}")
    else:
        sel_cats = [c for c in CATEGORIES if st.checkbox(c, key=f"c_{c}")]

    st.markdown("**📍 추천 반경 설정**")
    radius_km = st.slider(
        "숙소 기준 반경 (km)",
        min_value=5, max_value=60, value=30, step=5,
        help="선택한 반경 내의 장소만 추천에 포함됩니다"
    )
    st.caption(f"📍 숙소에서 **{radius_km}km** 이내 장소만 추천")

    st.markdown("**🤖 AI 맞춤 추천 조건**")
    st.caption("일차별로 원하는 슬롯만 입력하세요. 비워두면 기본 추천합니다.")
    _SLOT_DEFS = [
        ("morning_cafe",   "☕ 아침 카페",  "예: 오션뷰, 브런치"),
        ("morning_tour",   "🌅 오전 관광",  "예: 감귤 체험, 오름"),
        ("lunch",          "🍽️ 점심 식사",  "예: 해산물, 국수"),
        ("afternoon_tour", "🏛️ 오후 관광",  "예: 박물관, 액티비티"),
        ("afternoon_cafe", "🍰 오후 카페",  "예: 감성카페, 디저트"),
        ("dinner",         "🌙 저녁 식사",  "예: 흑돼지, 회"),
    ]
    pref_slots = {}  # {day: {slot_key: text}}
    for d in range(1, num_days + 1):
        with st.expander(f"📅 {d}일차 조건", expanded=(d == 1)):
            day_prefs = {}
            for slot_key, label, ph in _SLOT_DEFS:
                val = st.text_input(label, placeholder=ph, key=f"ps_{d}_{slot_key}")
                if val:
                    day_prefs[slot_key] = val
            pref_slots[d] = day_prefs
    preferences = " ".join(
        v for day_p in pref_slots.values() for v in day_p.values()
    )  # 챗봇·Chroma용 통합
    if preferences:
        if is_chroma_ready():
            st.caption("🧠 Chroma + 📊 CSV 키워드에서 각 조건을 분석해 반영합니다")
        else:
            st.caption("📊 CSV 키워드·리뷰에서 각 조건을 분석해 반영합니다")

    st.divider()

    # ── 5. 챗봇 토글 버튼 ───────────────────────────────────
    chat_lbl = "💬 AI 챗봇 닫기 ✕" if st.session_state.chat_open else "💬 AI 챗봇 열기 ▲"
    if st.button(chat_lbl, use_container_width=True, type="secondary"):
        st.session_state.chat_open = not st.session_state.chat_open
        st.rerun()
    st.caption("OpenAI API 키가 있으면 추천 코스 관련 대화 가능")


# ════════════════════════════════════════════════════════════
# 메인 화면
# ════════════════════════════════════════════════════════════
st.title("🍊 JejuTrip AI — Personalized Travel Recommender")
st.markdown(
    '<p class="source-tip">📊 <b>CSV 데이터</b>: 팀 직접 수집한 제주 장소 정보 (장소명·주소·평점·리뷰·키워드)　|　'
    '🗺️ <b>카카오 API</b>: 숙소 검색·네비경로·지도 링크 (실시간)</p>',
    unsafe_allow_html=True,
)

# ── 데이터 로딩 & 통계 표시 ────────────────────────────────
dm    = DataManager()
stats = dm.stats()
cat_summary = "  ·  ".join([f"{k}: {v}개" for k, v in stats["by_cat"].items()])
st.success(f"📊 CSV 데이터 로딩 완료: 총 **{stats['total']}개** 장소  |  {cat_summary}")

st.divider()

# ── 추천 생성 버튼 ──────────────────────────────────────────
gen_btn = st.button(
    "🚀 여행 코스 추천 생성", type="primary",
    use_container_width=True,
    disabled=(not sel_cats or num_days > 7),
)

if not sel_cats:
    st.warning("⚠️ 카테고리를 1개 이상 선택해주세요.")

if gen_btn and sel_cats:
    with st.spinner("✨ 추천 코스를 생성 중입니다... (📊 CSV 분석 중)"):
        kakao  = KakaoService(st.session_state.kakao_key) if st.session_state.kakao_ok else None
        engine = RecommendationEngine(
            dm, kakao,
            st.session_state.openai_key if st.session_state.openai_ok else ""
        )
        ulat = st.session_state.user_lat
        ulng = st.session_state.user_lng

        # Chroma 리뷰 유사도 부스트 (취향 입력 + DB 준비된 경우)
        chroma_boost = {}
        if preferences and is_chroma_ready() and st.session_state.openai_ok:
            with st.spinner("🧠 리뷰 유사도 분석 중..."):
                chroma_boost = get_similar_places(
                    preferences,
                    st.session_state.openai_key,
                )

        st.session_state.itinerary = engine.auto_recommend(
                num_days, sel_cats, ulat, ulng,
                pref_slots=pref_slots,
                radius_km=radius_km, chroma_boost=chroma_boost,
            )

    st.success("✅ 추천 코스 생성 완료!")

    # 취향 키워드 반영 결과 피드백
    if preferences and st.session_state.itinerary:
        matched_places = []
        for day_info in st.session_state.itinerary:
            for s in day_info["slots"]:
                if "🎯" in s.get("reason", ""):
                    matched_places.append(s["place"].get("name", ""))
        if matched_places:
            st.success(f"✅ **{len(matched_places)}개** 장소에서 취향 키워드 반영됨: {', '.join(matched_places)}")
        else:
            st.warning("⚠️ CSV 데이터에 일치하는 장소가 없거나 적습니다. 다른 표현으로 시도해보세요.")

# ── 초기화 / 저장 버튼 (코스 생성 직후 바로 노출) ────────────
if st.session_state.itinerary:
    bc2, bc3 = st.columns(2)
    with bc2:
        if st.button("🔄 초기화", use_container_width=True):
            st.session_state.itinerary = []
            st.rerun()
    with bc3:
        def _json_default(o):
            # numpy 스칼라(float64/int64 등) → 순수 파이썬 타입 변환
            return o.item() if hasattr(o, "item") else str(o)

        save_payload = {
            "version": 1,
            "saved_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "stay_name": st.session_state.stay_name,
            "user_lat": st.session_state.user_lat,
            "user_lng": st.session_state.user_lng,
            "itinerary": st.session_state.itinerary,
        }
        st.download_button(
            "💾 저장", use_container_width=True,
            data=json.dumps(save_payload, ensure_ascii=False, default=_json_default, indent=2).encode("utf-8"),
            file_name=f"jejutrip_course_{today.isoformat()}.json",
            mime="application/json",
        )

# ── 결과 탭 ─────────────────────────────────────────────────
if st.session_state.itinerary:
    itin  = st.session_state.itinerary
    kakao = KakaoService(st.session_state.kakao_key) if st.session_state.kakao_ok else None

    day_tabs   = [f"📅 {d}일차" for d in range(1, num_days + 1)]
    extra_tabs = ["🗺️ 전체 지도", "📊 코스 생성 분석"]
    all_tabs   = st.tabs(day_tabs + extra_tabs)

    # 일차별 탭
    for i, day_info in enumerate(itin):
        with all_tabs[i]:
            st.markdown(f"## 📅 {day_info['day']}일차 여행 코스")
            st.caption("📊 장소 정보: CSV 데이터  ·  🗺️ 경로·지도: 카카오 API")
            render_day_course(
                day_info,
                st.session_state.user_lat,
                st.session_state.user_lng,
                kakao,
                stay_name=st.session_state.stay_name,
            )

    # 전체 지도 탭
    with all_tabs[num_days]:
        st.markdown("### 🗺️ 전체 일정 지도  *(일차별 색상 구분)*")
        st.caption("마커 위치: 📊 CSV 좌표 데이터  ·  지도 렌더링: Folium + 카카오 연동")
        render_full_map(itin, st.session_state.user_lat,
                        st.session_state.user_lng, st.session_state.stay_name)

    # 코스 분석 탭
    with all_tabs[num_days + 1]:
        render_analysis(itin, sel_cats, preferences, num_days, "자동 추천 코스 생성")


# ── AI 챗봇 패널 (하단 표시) ────────────────────────────────
if st.session_state.chat_open:
    st.divider()
    st.markdown("### 💬 AI 여행 챗봇  *(OpenAI API 사용)*")
    st.caption(
        "현재 추천 코스 관련 질문이나 제주 여행 정보를 자유롭게 질문하세요. "
        "📊 CSV 기반 추천 코스 컨텍스트를 AI가 참고합니다."
    )
    render_chatbot(st.session_state.itinerary, st.session_state.openai_key, dm, radius_km=radius_km)
