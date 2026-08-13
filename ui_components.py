# ============================================================
# ui_components.py  |  UI 렌더링 컴포넌트
# ============================================================
# 역할: 장소 카드, 일정 뷰, 지도, 분석 탭 등 UI 구성요소
#
# ★ 데이터 출처 구분 원칙 (기획서 핵심 요구사항)
#   📊 CSV 데이터  → 팀이 직접 수집한 data.csv 기반 정보
#                    (장소명, 주소, 카테고리, 평점, 리뷰, 키워드)
#   🗺️ 카카오 API  → 카카오 REST API에서 실시간 가져온 정보
#                    (숙소 검색, 네비경로, 지도 링크, 길찾기)
# ============================================================

import streamlit as st
import folium
from streamlit.components.v1 import html as st_html
from urllib.parse import quote
from typing import Dict, List, Optional
from kakao_service import KakaoService, haversine
from config import DAY_COLORS

# ── 뱃지 HTML ────────────────────────────────────────────────
_CSV_BADGE   = '<span style="background:#1d4ed8;color:#fff;padding:2px 7px;border-radius:4px;font-size:11px;margin-right:4px">📊 CSV 데이터</span>'
_KAKAO_BADGE = '<span style="background:#fee500;color:#3c1e1e;padding:2px 7px;border-radius:4px;font-size:11px;margin-right:4px">🗺️ 카카오 API</span>'


def csv_badge():
    st.markdown(_CSV_BADGE, unsafe_allow_html=True)

def kakao_badge():
    st.markdown(_KAKAO_BADGE, unsafe_allow_html=True)


# ── 장소 카드 ────────────────────────────────────────────────
def render_place_card(slot: Dict, place: Dict, reason: str,
                      prev_lat: float, prev_lng: float,
                      kakao: Optional[KakaoService] = None,
                      pos_reviews: Optional[List] = None, neg_reviews: Optional[List] = None,
                      prev_name: str = "출발지"):
    """단일 장소 카드 렌더링 (데이터 출처 구분 포함)"""
    pos_reviews = pos_reviews or []
    neg_reviews = neg_reviews or []
    with st.container():
        # ── 헤더 ──
        c1, c2 = st.columns([5, 2])
        with c1:
            st.markdown(f"#### {slot['label']}")
        with c2:
            st.markdown(_CSV_BADGE + _KAKAO_BADGE, unsafe_allow_html=True)

        # ── 기본 정보 (📊 CSV) ──
        left, right = st.columns([3, 2])
        with left:
            st.markdown(f"**📍 {place.get('name', '?')}**")
            st.caption(f"📊 CSV: {place.get('address', '')} | {place.get('category', '')}")
            rating = place.get("rating")
            cnt    = int(place.get("total_cnt", 0) or 0)
            st.caption(
                f"{'⭐ ' + str(rating) if rating else '⭐ -'}  ·  💬 리뷰 {cnt}개"
            )
            st.info(f"💡 추천 이유: {reason}")

        with right:
            # 🗺️ 카카오 길찾기 링크 + 전화번호
            name_enc = quote(str(place.get("name", "")))
            plat = place.get("lat", "")
            plng = place.get("lng", "")
            if plat and plng:
                from_enc = quote(str(prev_name))
                navi_url = (
                    f"https://map.kakao.com/link/from/{from_enc},{prev_lat},{prev_lng}"
                    f"/to/{name_enc},{plat},{plng}"
                )
                st.markdown(f"[🗺️ 카카오 길찾기]({navi_url})")
                st.caption(f"🗺️ {prev_name} → {place.get('name', '')}")
            if kakao and kakao.key and plat and plng:
                phone = kakao.get_phone(str(place.get("name", "")), float(plat), float(plng))
                if phone:
                    st.caption(f"📞 {phone}")

            # 🗺️ 카카오 네비게이션 실경로 (API 있을 때)
            if kakao and kakao.key and plat and plng:
                route = kakao.get_route(float(prev_lng), float(prev_lat),
                                        float(plng), float(plat))
                if route:
                    st.caption(f"🚗 {route['duration_min']}분 · {route['distance_km']}km")
                    st.caption(f"🗺️ {route['source']}")
                else:
                    d = haversine(prev_lat, prev_lng, float(plat), float(plng))
                    st.caption(f"📏 직선거리 약 {d:.1f}km")

        # ── 대표 리뷰 (📊 CSV) ──
        if pos_reviews or neg_reviews:
            with st.expander("💬 리뷰 요약  (📊 CSV 데이터에서 추출)", expanded=False):
                if pos_reviews:
                    st.markdown("**👍 좋은 리뷰**")
                    for rv in pos_reviews:
                        st.markdown(f"• {rv[:120]}")
                if neg_reviews:
                    st.markdown("**👎 아쉬운 리뷰**")
                    for rv in neg_reviews:
                        st.markdown(f"• {rv[:120]}")

        # 카카오맵 상세 페이지 링크 (🗺️ 카카오 API 원본 URL)
        url = str(place.get("place_url", ""))
        if url and url.startswith("http"):
            st.caption(f"[🔗 카카오맵 상세보기]({url})  ← 🗺️ 카카오 API")
        st.divider()


# ── 일차 전체 일정 ───────────────────────────────────────────
def render_day_course(day_info: Dict, ulat: float, ulng: float,
                      kakao: Optional[KakaoService] = None,
                      stay_name: str = "숙소"):
    """1개 일차의 전체 슬롯 순서대로 렌더링"""
    slots = day_info.get("slots", [])
    if slots:
        total_dist = 0.0
        p_lat, p_lng = ulat, ulng
        for s in slots:
            plat = float(s["place"].get("lat", p_lat))
            plng = float(s["place"].get("lng", p_lng))
            total_dist += haversine(p_lat, p_lng, plat, plng)
            p_lat, p_lng = plat, plng
        st.caption(f"🧭 이 날 총 이동거리(직선거리 기준): 약 **{total_dist:.1f}km**  ·  동선 최적화 적용됨")

    prev_lat, prev_lng = ulat, ulng
    prev_name = stay_name
    for s in day_info.get("slots", []):
        render_place_card(
            s["slot"], s["place"], s["reason"],
            prev_lat, prev_lng, kakao,
            pos_reviews=s.get("pos_reviews", []),
            neg_reviews=s.get("neg_reviews", []),
            prev_name=prev_name,
        )
        prev_name = s["place"].get("name", prev_name)
        prev_lat = float(s["place"].get("lat", prev_lat))
        prev_lng = float(s["place"].get("lng", prev_lng))


# ── 전체 지도 탭 ─────────────────────────────────────────────
def render_full_map(itinerary: List[Dict], ulat: float, ulng: float,
                    stay_name: str = "숙소"):
    """일차별 동선을 색상으로 구분해 지도에 표시  |  🗺️ Folium + 카카오 연동"""
    m = folium.Map(location=[ulat, ulng], zoom_start=11, tiles="CartoDB positron")
    # 숙소 마커
    folium.Marker(
        [ulat, ulng], tooltip=stay_name,
        icon=folium.Icon(color="black", icon="home")
    ).add_to(m)

    for day_info in itinerary:
        day   = day_info["day"]
        color = DAY_COLORS[(day - 1) % len(DAY_COLORS)]
        coords = []
        for s in day_info.get("slots", []):
            p = s["place"]
            lat, lng = p.get("lat"), p.get("lng")
            if not lat or not lng:
                continue
            coords.append([float(lat), float(lng)])
            popup_html = (
                f"<b>[{day}일차] {s['slot']['label']}</b><br>"
                f"{p.get('name','')}<br>"
                f"📊 CSV: {p.get('address','')}<br>"
                f"{'⭐ ' + str(p.get('rating','')) if p.get('rating') else ''}"
            )
            folium.Marker(
                [float(lat), float(lng)],
                popup=folium.Popup(popup_html, max_width=220),
                tooltip=f"{day}일차 | {p.get('name','')}",
                icon=folium.Icon(color=color, icon="star"),
            ).add_to(m)
        if len(coords) > 1:
            folium.PolyLine(
                coords, color=color, weight=3, opacity=0.75,
                tooltip=f"{day}일차 이동 동선  |  지도: Folium"
            ).add_to(m)

    st.caption("🗺️ 지도 렌더링: Folium  |  마커·동선 데이터: 📊 CSV 장소 좌표")
    st_html(m._repr_html_(), height=520)


# ── 코스 생성 분석 탭 ────────────────────────────────────────
def render_analysis(itinerary: List[Dict], cats: List[str],
                    preferences: str, num_days: int, mode: str):
    """추천 코스 생성 분석 및 데이터 출처 상세 설명"""
    st.markdown("### 📊 추천 코스 생성 분석")
    st.info(
        f"🗓️ 여행 기간: {num_days}일  ·  🎯 추천 방식: {mode}  ·  "
        f"📁 선택 카테고리: {', '.join(cats)}"
    )
    if preferences:
        st.markdown(f"**🤖 AI 맞춤 조건:** `{preferences}`")
        st.caption("→ 📊 CSV의 keywords·reviews_text에서 관련 언급 빈도를 분석해 우선 반영")

    st.markdown("---")
    st.markdown("#### 📋 장소별 추천 근거")
    for day_info in itinerary:
        with st.expander(f"📅 {day_info['day']}일차 분석", expanded=False):
            for s in day_info.get("slots", []):
                p = s["place"]
                st.markdown(f"**{s['slot']['label']} → {p.get('name','')}**")
                st.markdown(
                    f"{_CSV_BADGE} 카테고리: {p.get('category','')} | 평점: {p.get('rating','-')} | "
                    f"리뷰: {int(p.get('total_cnt',0) or 0)}개",
                    unsafe_allow_html=True
                )
                st.caption(f"추천 이유: {s['reason']}")
                st.markdown("---")

    st.markdown("---")
    st.markdown("#### 📑 데이터 출처 가이드")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**📊 CSV 데이터**  (팀 직접 수집)")
        st.caption("• 장소명, 주소, 카테고리")
        st.caption("• 평점, 리뷰 텍스트, 키워드")
        st.caption("• 추천 점수 계산의 핵심 소스")
    with c2:
        st.markdown("**🗺️ 카카오 API**  (실시간 연동)")
        st.caption("• 숙소/출발지 검색")
        st.caption("• 네비게이션 경로·거리·시간")
        st.caption("• 지도 링크, 상세 페이지 URL")
