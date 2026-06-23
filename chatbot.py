# ============================================================
# chatbot.py  |  AI 여행 챗봇
# ============================================================
# 역할: OpenAI API를 활용한 제주 여행 전문 챗봇
#   - 현재 생성된 추천 코스를 컨텍스트로 전달
#   - 코스 관련 질문 및 일반 여행 질문 모두 답변
#   - "바꿔줘"  → 즉시 교체
#   - "추천 리스트 줘" → 후보 5개 제시 후 번호로 선택
#   - 대화 이력 10턴 유지 (과금 방지)
#
# 출처: OpenAI API (gpt-4o-mini)  - 사용자 입력 키 사용
# ============================================================

import json
import streamlit as st

try:
    from openai import OpenAI
    OPENAI_OK = True
except ImportError:
    OPENAI_OK = False

from config import TIME_SLOTS
from kakao_service import haversine

_SLOT_LABELS = {s["key"]: s["label"] for s in TIME_SLOTS}
_NUM_EMOJI   = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]


# ── 컨텍스트 빌더 ────────────────────────────────────────────
def _build_context(itinerary: list) -> str:
    if not itinerary:
        return "현재 생성된 추천 코스가 없습니다."
    lines = ["[현재 추천된 제주 여행 코스 (📊 CSV 데이터 기반)]"]
    for day in itinerary:
        lines.append(f"\n{day['day']}일차:")
        for s in day.get("slots", []):
            p = s.get("place", {})
            lines.append(
                f"  {s['slot']['label']}  →  {p.get('name','?')}"
                f"  ({p.get('address','')})"
                f"  | 평점: {p.get('rating','-')} | 이유: {s.get('reason','')}"
            )
    return "\n".join(lines)


# ── 의도 감지 ────────────────────────────────────────────────
def _detect_intent(user_text: str, itinerary: list, client) -> dict:
    """
    반환 형식:
      {"type": "modify",         "day": int, "slot_key": str, "keyword": str}
      {"type": "recommend_list", "day": int, "slot_key": str, "keyword": str}
      {"type": "select",         "index": int}
      {"type": "chat"}
    """
    num_days  = len(itinerary)
    slot_info = ", ".join(f'{k}({v})' for k, v in _SLOT_LABELS.items())

    pending = st.session_state.get("_candidate_list")
    pending_ctx = ""
    if pending:
        pending_ctx = (
            f"\n현재 대기 중인 후보 목록: {pending['day']}일차 "
            f"{_SLOT_LABELS.get(pending['slot_key'], pending['slot_key'])} "
            f"{len(pending['candidates'])}개 후보가 제시된 상태입니다."
        )

    prompt = (
        f'사용자 메시지: "{user_text}"\n\n'
        f"현재 코스: {num_days}일차까지 있음\n"
        f"슬롯 종류: {slot_info}"
        f"{pending_ctx}\n\n"
        f"다음 중 해당하는 JSON을 반환하세요:\n"
        f'1. 즉시 교체 요청 (바꿔줘/변경해줘): {{"type":"modify","day":<int>,"slot_key":"<key>","keyword":"<str>"}}\n'
        f'2. 후보 리스트 요청 (추천해줘/보여줘/골라볼게/리스트): {{"type":"recommend_list","day":<int>,"slot_key":"<key>","keyword":"<str or "">"}}\n'
        f'3. 후보 선택 (N번/첫번째/두번째 등): {{"type":"select","index":<int>}}\n'
        f'4. 일반 질문: {{"type":"chat"}}\n'
        f"JSON만 반환하세요."
    )
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=80,
            response_format={"type": "json_object"},
        )
        return json.loads(resp.choices[0].message.content)
    except Exception:
        return {"type": "chat"}


# ── 후보 장소 조회 ────────────────────────────────────────────
def _get_candidates(day: int, slot_key: str, keyword: str, dm, ulat: float, ulng: float, n: int = 5) -> list:
    """해당 슬롯·키워드 기준 상위 N개 장소 반환 (수정 없이 조회만)
    챗 입력 keyword + 사이드바 pref_kw_map 조건을 모두 반영."""
    slot_def = next((s for s in TIME_SLOTS if s["key"] == slot_key), None)
    if not slot_def:
        return []

    cat = slot_def["cat"]
    df  = dm.filter_by_cats(["자연", "문화", "기타"] if cat in ("자연", "문화") else [cat])
    if df.empty:
        return []

    itin = st.session_state.itinerary
    used = {s["place"].get("name", "") for d in itin for s in d.get("slots", [])}
    pool = df[~df["name"].isin(used)].copy()
    if pool.empty:
        pool = df.copy()

    pool["_dist"] = pool.apply(
        lambda r: haversine(ulat, ulng, float(r["lat"]), float(r["lng"])), axis=1
    )
    in_radius = pool[pool["_dist"] <= 30]
    if not in_radius.empty:
        pool = in_radius.copy()

    # 챗 키워드 + 원래 일차별 조건(pref_kw_map) 병합
    pref_kws = []
    if 1 <= day <= len(itin):
        pref_kws = itin[day - 1].get("pref_kw_map", {}).get(slot_key, [])
    all_kws = ([keyword] if keyword else []) + pref_kws

    if all_kws:
        kw_mask = pool["reviews_text"].str.contains(all_kws[0], na=False, case=False) | \
                  pool["name"].str.contains(all_kws[0], na=False, case=False)
        for kw in all_kws[1:]:
            kw_mask |= pool["reviews_text"].str.contains(kw, na=False, case=False)
            kw_mask |= pool["name"].str.contains(kw, na=False, case=False)
        matched = pool[kw_mask]
        if not matched.empty:
            pool = matched.copy()

    pool["_score"] = pool["rating"].fillna(3.5) * 10
    pool["_score"] += pool["total_cnt"].fillna(0).clip(0, 200) / 10
    # pref_kw_map 키워드 매칭 시 추가 점수
    for kw in pref_kws:
        pool["_score"] += pool["reviews_text"].str.contains(kw, na=False, case=False).astype(int) * 10
        pool["_score"] += pool["name"].str.contains(kw, na=False, case=False).astype(int) * 20
    pool["_score"] -= pool["_dist"].clip(0, 60) * 0.5

    return pool.nlargest(n, "_score").to_dict("records")


def _format_candidates(day: int, slot_key: str, keyword: str, candidates: list) -> str:
    """후보 리스트를 마크다운 문자열로 포맷"""
    label  = _SLOT_LABELS.get(slot_key, slot_key)
    kw_str = f" (`{keyword}` 기준)" if keyword else ""
    lines  = [f"**{day}일차 {label} 교체 후보{kw_str}**\n"]
    for i, p in enumerate(candidates):
        emoji  = _NUM_EMOJI[i] if i < len(_NUM_EMOJI) else f"{i+1}."
        name   = p.get("name", "?")
        rating = p.get("rating", "-")
        cnt    = int(p.get("total_cnt", 0) or 0)
        addr   = p.get("address", "")
        lines.append(f"{emoji} **{name}**  ⭐ {rating} · 💬 {cnt}개")
        lines.append(f"   📍 {addr}\n")
    lines.append("원하시는 번호를 말씀해주시면 바로 변경해드릴게요!")
    return "\n".join(lines)


# ── 리뷰 긍정/부정 분류 ──────────────────────────────────────
_POS_KW = ["좋아", "맛있", "최고", "추천", "훌륭", "깔끔", "친절", "만족", "완벽", "신선", "맛나", "감동", "좋았", "좋은", "맛집", "대박"]
_NEG_KW = ["별로", "실망", "나쁘", "최악", "아쉽", "불친절", "비싸", "후회", "형편없", "안 좋", "별점 1", "별점1"]

def _classify_reviews(reviews_text: str, client) -> tuple:
    """GPT로 리뷰 긍정/부정 요약, 실패 시 키워드 기반 폴백"""
    import random, re
    reviews = [r.strip() for r in str(reviews_text).split("|") if len(r.strip()) > 10 and r.strip().lower() != "nan"]
    if not reviews:
        return [], []

    if client:
        try:
            sample  = random.sample(reviews, min(20, len(reviews)))
            prompt  = (
                f"다음은 한국어 장소 리뷰들이야:\n{' / '.join(sample)}\n\n"
                f"긍정적인 내용 2가지, 부정적인 내용 2가지를 각각 한 문장씩 요약해줘.\n"
                f"부정적인 내용이 1가지뿐이면 neg 배열에 1개만, 없으면 빈 배열로.\n"
                f"코드블록 없이 JSON만 반환: {{\"pos\": [\"요약1\", \"요약2\"], \"neg\": [\"요약1\"]}}"
            )
            resp    = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_completion_tokens=300,
            )
            raw  = re.sub(r"```(?:json)?\s*|\s*```", "", resp.choices[0].message.content.strip())
            data = json.loads(raw)
            pos  = [s.strip() for s in data.get("pos", []) if isinstance(s, str) and s.strip()]
            neg  = [s.strip() for s in data.get("neg", []) if isinstance(s, str) and s.strip()]
            return pos[:2], neg[:2]
        except Exception:
            pass

    # 키워드 기반 폴백
    pos, neg = [], []
    for rv in reviews:
        if any(w in rv for w in _NEG_KW) and not any(w in rv for w in _POS_KW):
            neg.append(rv)
        else:
            pos.append(rv)
    return pos[:2], neg[:2]


# ── 슬롯 수정 실행 ────────────────────────────────────────────
def _apply_place(day: int, slot_key: str, new_place: dict, client, keyword: str = "") -> str:
    """itinerary의 특정 슬롯을 new_place로 교체, 완료 메시지 반환"""
    itin = st.session_state.itinerary
    if day < 1 or day > len(itin):
        return f"⚠️ {day}일차 코스가 없습니다."

    slot_idx = next(
        (i for i, s in enumerate(itin[day - 1]["slots"]) if s["slot"]["key"] == slot_key),
        None,
    )
    if slot_idx is None:
        label = _SLOT_LABELS.get(slot_key, slot_key)
        return f"⚠️ {day}일차 코스에 '{label}' 슬롯이 없습니다."

    pos_reviews, neg_reviews = _classify_reviews(new_place.get("reviews_text", ""), client)

    reason = f"🔄 '{keyword}' 조건으로 변경" if keyword else "🔄 직접 선택으로 변경"
    st.session_state.itinerary[day - 1]["slots"][slot_idx]["place"]       = new_place
    st.session_state.itinerary[day - 1]["slots"][slot_idx]["reason"]      = reason
    st.session_state.itinerary[day - 1]["slots"][slot_idx]["pos_reviews"] = pos_reviews
    st.session_state.itinerary[day - 1]["slots"][slot_idx]["neg_reviews"] = neg_reviews

    label  = _SLOT_LABELS.get(slot_key, slot_key)
    name   = new_place.get("name", "?")
    rating = new_place.get("rating", "-")
    return (
        f"✅ **{day}일차 {label}**을 **{name}** (⭐ {rating})으로 변경했습니다!\n\n"
        f"위 탭에서 업데이트된 코스를 확인하세요."
    )


def _apply_modification(day: int, slot_key: str, keyword: str, dm, ulat: float, ulng: float, client=None) -> str:
    """키워드 기반으로 최적 장소 1개를 즉시 선택·교체"""
    import random
    candidates = _get_candidates(day, slot_key, keyword, dm, ulat, ulng, n=5)
    if not candidates:
        return "⚠️ 해당 조건에 맞는 장소를 찾지 못했습니다."

    new_place  = random.choice(candidates[:3])
    kw_matched = keyword and (
        keyword.lower() in str(new_place.get("reviews_text", "")).lower()
        or keyword.lower() in str(new_place.get("name", "")).lower()
    )

    msg = _apply_place(day, slot_key, new_place, client, keyword)
    if keyword and not kw_matched:
        msg = (
            f"⚠️ '{keyword}' 키워드와 정확히 일치하는 장소가 없어 "
            f"카테고리 내 최고 평점 장소로 대체했습니다.\n\n" + msg
        )
    return msg


# ── 챗봇 UI ─────────────────────────────────────────────────
def render_chatbot(itinerary: list, openai_key: str, dm=None):
    if not openai_key or not OPENAI_OK:
        st.warning("💡 챗봇을 사용하려면 OpenAI API 키를 입력하고 연결을 확인해주세요.")
        return

    if "chat_msgs" not in st.session_state:
        st.session_state.chat_msgs = []
    if "_pending_chat" not in st.session_state:
        st.session_state._pending_chat = None
    if "_candidate_list" not in st.session_state:
        st.session_state._candidate_list = None

    # ── 헤더 ──
    col_info, col_clr = st.columns([5, 1])
    with col_info:
        total = len([m for m in st.session_state.chat_msgs if m["role"] == "user"])
        hint  = "  ·  💡 \"N일차 슬롯 바꿔줘\" 또는 \"추천 리스트 줘\"" if itinerary and dm else ""
        st.caption(f"🗨️ 대화 {total}턴 · 최근 10턴 AI 참고 · gpt-4o-mini{hint}")
    with col_clr:
        if st.button("🗑️ 초기화", key="chat_clear_btn", use_container_width=True):
            st.session_state.chat_msgs       = []
            st.session_state._pending_chat   = None
            st.session_state._candidate_list = None
            st.rerun()

    # ── 대화 이력 표시 ──
    for msg in st.session_state.chat_msgs:
        avatar = "🧑" if msg["role"] == "user" else "🤖"
        with st.chat_message(msg["role"], avatar=avatar):
            st.markdown(msg["content"])

    # ── 입력 처리 ──
    user_text = st.chat_input("추천 코스나 제주 여행에 대해 질문해보세요!")
    if st.session_state._pending_chat:
        user_text = st.session_state._pending_chat
        st.session_state._pending_chat = None

    if not user_text:
        return

    st.session_state.chat_msgs.append({"role": "user", "content": user_text})
    with st.chat_message("user", avatar="🧑"):
        st.markdown(user_text)

    client = OpenAI(api_key=openai_key)

    # ── 의도 감지 ──
    intent      = _detect_intent(user_text, itinerary, client) if (itinerary and dm) else {"type": "chat"}
    intent_type = intent.get("type", "chat")
    ulat = st.session_state.get("user_lat", 33.4996213)
    ulng = st.session_state.get("user_lng", 126.5311884)

    # ── 즉시 교체 ──
    if intent_type == "modify":
        reply = _apply_modification(
            intent.get("day", 1), intent.get("slot_key", ""),
            intent.get("keyword", ""), dm, ulat, ulng, client,
        )
        with st.chat_message("assistant", avatar="🤖"):
            st.markdown(reply)
        st.session_state.chat_msgs.append({"role": "assistant", "content": reply})
        st.session_state._candidate_list = None
        st.rerun()
        return

    # ── 후보 리스트 제시 ──
    if intent_type == "recommend_list":
        day      = intent.get("day", 1)
        slot_key = intent.get("slot_key", "")
        keyword  = intent.get("keyword", "")
        candidates = _get_candidates(day, slot_key, keyword, dm, ulat, ulng, n=5)
        if candidates:
            st.session_state._candidate_list = {"day": day, "slot_key": slot_key, "candidates": candidates}
            reply = _format_candidates(day, slot_key, keyword, candidates)
        else:
            reply = "⚠️ 해당 조건에 맞는 장소를 찾지 못했습니다."
            st.session_state._candidate_list = None
        with st.chat_message("assistant", avatar="🤖"):
            st.markdown(reply)
        st.session_state.chat_msgs.append({"role": "assistant", "content": reply})
        return

    # ── 후보 선택 ──
    if intent_type == "select":
        cl = st.session_state.get("_candidate_list")
        if not cl:
            reply = "⚠️ 선택할 후보 목록이 없습니다. 먼저 추천 리스트를 요청해주세요."
        else:
            idx        = intent.get("index", 1) - 1
            candidates = cl["candidates"]
            if 0 <= idx < len(candidates):
                reply = _apply_place(cl["day"], cl["slot_key"], candidates[idx], client)
                st.session_state._candidate_list = None
            else:
                reply = f"⚠️ 1~{len(candidates)} 사이 번호를 입력해주세요."
        with st.chat_message("assistant", avatar="🤖"):
            st.markdown(reply)
        st.session_state.chat_msgs.append({"role": "assistant", "content": reply})
        if st.session_state._candidate_list is None:
            st.rerun()
        return

    # ── 일반 채팅 (스트리밍) ──
    st.session_state._candidate_list = None

    system = f"""당신은 제주 여행 전문 AI 어시스턴트입니다.
아래 추천 코스 정보를 참고하여 사용자 질문에 친절하고 구체적으로 답변하세요.
한국어로 답변하며, 코스 이외 제주 여행 관련 질문도 성실히 답변해주세요.

{_build_context(itinerary)}"""

    recent = st.session_state.chat_msgs[-10:]

    with st.chat_message("assistant", avatar="🤖"):
        placeholder = st.empty()
        parts: list = []
        reply = ""
        try:
            stream = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "system", "content": system}] + recent,
                max_completion_tokens=600,
                stream=True,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    parts.append(delta)
                    placeholder.markdown("".join(parts) + "▌")
            placeholder.markdown("".join(parts))
            reply = "".join(parts).strip()
        except Exception as e:
            reply = f"⚠️ 오류가 발생했습니다: {e}"
            placeholder.markdown(reply)

    st.session_state.chat_msgs.append({"role": "assistant", "content": reply})
