# ============================================================
# recrawl.py  |  jeju_crawling_100.csv 데이터 갱신
# ============================================================
# 실행: python recrawl.py
#
# 갱신 항목:
#   - rating      : <span class="num_star"> 값
#   - total_cnt   : 후기 수 + 블로그 수 합산
#   - reviews_text: 리뷰 본문 최대 MAX_REVIEWS개 ( | 구분)
#
# 중단 후 재시작 지원: recrawl_backup.csv 기준으로 이어서 실행
# ============================================================

import pandas as pd
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import WebDriverException
import time
from tqdm import tqdm

INPUT_CSV   = 'jeju_crawling_100.csv'
OUTPUT_CSV  = 'jeju_crawling_100.csv'
BACKUP_CSV  = 'recrawl_backup.csv'
MAX_REVIEWS = 35   # 30~40 사이 목표


# ── 드라이버 생성 ──────────────────────────────────────────
def create_driver():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
    )
    return webdriver.Chrome(
        service=Service(ChromeDriverManager().install()), options=options
    )


# ── 숫자 파싱 헬퍼 ────────────────────────────────────────
def _parse_int(el) -> int:
    """Selenium WebElement의 textContent에서 숫자만 추출."""
    raw = el.get_attribute("textContent").strip()
    digits = "".join(c for c in raw if c.isdigit())
    return int(digits) if digits else 0


# ── 페이지 정보 수집 ──────────────────────────────────────
def get_place_info(driver, url, max_reviews=MAX_REVIEWS):
    driver.get(url)
    time.sleep(3)

    # ── rating / total_cnt: 리뷰 유무와 무관하게 먼저 추출 ──
    # (리뷰가 없거나 페이지가 없는 장소도 별점·카운트는 있을 수 있음)
    rating = None
    try:
        el = driver.find_element(By.CLASS_NAME, "num_star")
        rating = float(el.text.strip())
    except Exception:
        pass

    # ── total_cnt = 후기 수 + 블로그 수 ──────────────────
    total_cnt = 0
    try:
        info_tits = driver.find_elements(By.CLASS_NAME, "info_tit")
        for tit_el in info_tits:
            if tit_el.text.strip() in ("후기", "블로그"):
                # info_tit 바로 다음 형제 info_num
                num_el = tit_el.find_element(
                    By.XPATH, "following-sibling::span[@class='info_num'][1]"
                )
                total_cnt += _parse_int(num_el)
    except Exception:
        pass

    # ── reviews ──────────────────────────────────────────
    collected = []

    while len(collected) < max_reviews:
        # 각 리뷰의 인라인 '더보기' 버튼 펼치기
        while True:
            clicked = False
            for btn in driver.find_elements(By.CLASS_NAME, "btn_more"):
                try:
                    driver.execute_script("arguments[0].scrollIntoView(true);", btn)
                    if btn.text.strip() == "더보기":
                        driver.execute_script("arguments[0].click();", btn)
                        time.sleep(0.5)
                        clicked = True
                        break
                except Exception:
                    continue
            if not clicked:
                break

        # 현재 화면에 펼쳐진 리뷰 수집
        for el in driver.find_elements(By.CLASS_NAME, "desc_review"):
            txt = el.text.strip().replace("\n", " ").replace("접기", "").strip()
            if txt and txt not in collected:
                collected.append(txt)
            if len(collected) >= max_reviews:
                break

        if len(collected) >= max_reviews:
            break

        # '후기 더보기' 버튼 — 다음 배치 로드
        next_loaded = False
        try:
            for btn in driver.find_elements(By.CLASS_NAME, "link_more"):
                if "후기 더보기" in btn.text:
                    driver.execute_script("arguments[0].scrollIntoView(true);", btn)
                    driver.execute_script("arguments[0].click();", btn)
                    time.sleep(2)
                    next_loaded = True
                    break
        except Exception:
            pass

        if not next_loaded:
            try:
                btns = driver.find_elements(
                    By.XPATH, "//*[contains(text(), '후기 더보기')]"
                )
                if btns:
                    driver.execute_script(
                        "arguments[0].scrollIntoView(true);", btns[0]
                    )
                    driver.execute_script("arguments[0].click();", btns[0])
                    time.sleep(2)
                    next_loaded = True
            except Exception:
                pass

        if not next_loaded:
            break

        # 새 리뷰가 실제로 추가됐는지 확인 (무한루프 방지)
        time.sleep(0.5)
        new_els = driver.find_elements(By.CLASS_NAME, "desc_review")
        new_count = sum(
            1 for e in new_els
            if e.text.strip().replace("\n", " ").replace("접기", "").strip()
            and e.text.strip().replace("\n", " ").replace("접기", "").strip()
            not in collected
        )
        if new_count == 0:
            break

    return {
        "rating":    rating,
        "total_cnt": total_cnt if total_cnt > 0 else None,
        "reviews":   collected[:max_reviews],
    }


def get_place_with_retry(driver, url, max_reviews=MAX_REVIEWS, retries=3):
    for attempt in range(retries):
        try:
            return get_place_info(driver, url, max_reviews), driver
        except WebDriverException:
            # 드라이버 크래시 → 죽은 드라이버 버리고 새로 생성
            print(f"\n  [드라이버 재생성 {attempt+1}/{retries}] {url}")
            try:
                driver.quit()
            except Exception:
                pass
            time.sleep(3)
            driver = create_driver()
        except Exception as e:
            if attempt < retries - 1:
                print(f"\n  [재시도 {attempt+1}/{retries}] {url}  |  {e}")
                time.sleep(3)
    print(f"\n  [최종 실패] {url}")
    return {"rating": None, "total_cnt": None, "reviews": []}, driver


# ── 메인 ──────────────────────────────────────────────────
df = pd.read_csv(INPUT_CSV, encoding='cp949')

# reviews_text가 비어있는 행의 원본 인덱스 목록
empty_mask    = df["reviews_text"].isna() | (df["reviews_text"].str.strip() == "")
target_indices = df.index[empty_mask].tolist()
print(f"reviews_text 비어있는 장소: {len(target_indices)}개 / 전체 {len(df)}개")

if not target_indices:
    print("크롤링할 대상이 없습니다.")
    exit(0)

# 중단 후 재시작: backup에서 이미 채워진 행 제외
try:
    backup_df = pd.read_csv(BACKUP_CSV, encoding='utf-8-sig')
    # backup에서 reviews_text가 채워진 행은 이미 완료된 것
    done_indices = set(
        backup_df.index[
            backup_df["reviews_text"].notna() & (backup_df["reviews_text"].str.strip() != "")
            & backup_df.index.isin(target_indices)
        ].tolist()
    )
    # 완료된 행의 데이터를 df에 반영
    for i in done_indices:
        df.loc[i, "rating"]       = backup_df.loc[i, "rating"]
        df.loc[i, "total_cnt"]    = backup_df.loc[i, "total_cnt"]
        df.loc[i, "reviews_text"] = backup_df.loc[i, "reviews_text"]
    remaining = [i for i in target_indices if i not in done_indices]
    print(f"이전 백업 발견: {len(done_indices)}개 완료, {len(remaining)}개 남음")
except Exception:
    remaining = target_indices
    print("처음부터 시작")

driver = create_driver()
fail_indices = []

print(f"{len(remaining)}개 크롤링 시작 (리뷰 최대 {MAX_REVIEWS}개/장소)")

for loop_count, idx in enumerate(tqdm(remaining), start=1):
    row = df.loc[idx]

    # 150개마다 드라이버 재시작 (메모리 누수 방지)
    if loop_count > 1 and loop_count % 150 == 0:
        driver.quit()
        time.sleep(2)
        driver = create_driver()

    data, driver = get_place_with_retry(driver, row["place_url"], max_reviews=MAX_REVIEWS)

    df.loc[idx, "rating"]       = data["rating"]
    df.loc[idx, "total_cnt"]    = data["total_cnt"] if data["total_cnt"] else df.loc[idx, "total_cnt"]
    df.loc[idx, "reviews_text"] = " | ".join(data["reviews"])

    if not data["reviews"]:
        fail_indices.append(idx)

    # 50개마다 백업 저장
    if loop_count % 50 == 0:
        df.loc[:, ~df.columns.str.startswith("Unnamed")].to_csv(
            BACKUP_CSV, index=False, encoding='utf-8-sig'
        )
        print(f"\n  [backup] {loop_count}번째 완료 → {BACKUP_CSV}")

driver.quit()

# ── 최종 저장 ─────────────────────────────────────────────
df = df.loc[:, ~df.columns.str.startswith("Unnamed")]
df.to_csv(OUTPUT_CSV, index=False, encoding='utf-8-sig')
print(f"\n✅ 저장 완료 → {OUTPUT_CSV}")

if fail_indices:
    df.loc[fail_indices].to_csv(
        "recrawl_fail_list.csv", index=False, encoding='utf-8-sig'
    )
    print(f"실패 {len(fail_indices)}개 → recrawl_fail_list.csv")

# ── 결과 요약 ─────────────────────────────────────────────
review_counts = df["reviews_text"].apply(
    lambda x: len(str(x).split(" | ")) if pd.notna(x) and str(x).strip() else 0
)
print(f"\n=== 크롤링 완료 ===")
print(f"총 장소     : {len(df)}개")
print(f"리뷰 0개    : {(review_counts == 0).sum()}개")
print(f"리뷰 1~19개 : {((review_counts > 0) & (review_counts < 20)).sum()}개")
print(f"리뷰 20~34개: {((review_counts >= 20) & (review_counts < 35)).sum()}개")
print(f"리뷰 35개   : {(review_counts >= 35).sum()}개")
print(f"평균 리뷰 수: {review_counts.mean():.1f}개")
