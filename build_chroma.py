# ============================================================
# build_chroma.py  |  Chroma 리뷰 DB 적재 (최초 1회 실행)
# ============================================================
# 실행: python build_chroma.py
# ============================================================

import os
import shutil
import uuid
import math
import pandas as pd

from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from langchain_chroma import Chroma
from config import OPENAI_API_KEY

CHROMA_DIR = "./chroma_jeju_reviews"
COLLECTION  = "jeju_reviews"

# ---------------------------
# 0. 기존 DB 삭제 (손상 인덱스 초기화)
# ---------------------------
if os.path.isdir(CHROMA_DIR):
    shutil.rmtree(CHROMA_DIR)
    print(f"기존 Chroma DB 삭제 완료: {CHROMA_DIR}")
os.makedirs(CHROMA_DIR, exist_ok=True)

# ---------------------------
# 1. CSV 로드 (인코딩 자동 감지)
# ---------------------------
for enc in ["utf-8", "utf-8-sig", "cp949", "euc-kr"]:
    try:
        df = pd.read_csv("jeju_crawling_100.csv", encoding=enc)
        print(f"CSV 로드 완료 (encoding={enc})")
        break
    except UnicodeDecodeError:
        continue
else:
    raise RuntimeError("CSV 인코딩을 감지하지 못했습니다.")

# ---------------------------
# 2. reviews_text를 | 기준으로 분리
#    리뷰 1개 = Document 1개
# ---------------------------

docs = []
ids  = []

for row_idx, row in df.iterrows():
    place_name   = row.get("place_name", "")
    category     = row.get("category_group_name", "")
    address      = row.get("address_name", "")
    reviews_text = row.get("reviews_text", "")

    if pd.isna(reviews_text) or str(reviews_text).strip() == "":
        continue

    split_reviews = [r.strip() for r in str(reviews_text).split("|") if r.strip()]

    for review_idx, review in enumerate(split_reviews):
        docs.append(
            Document(
                page_content=review,
                metadata={
                    "place_name":          place_name,
                    "category_group_name": category,
                    "address_name":        address,
                    "row_idx":             int(row_idx),
                    "review_idx":          int(review_idx),
                },
            )
        )
        ids.append(str(uuid.uuid4()))

print(f"총 리뷰 문서 수: {len(docs)}")

# ---------------------------
# 3. Embedding / Chroma
# ---------------------------
embeddings = OpenAIEmbeddings(
    model="text-embedding-3-small",
    api_key=OPENAI_API_KEY,
)

vectorstore = Chroma(
    collection_name=COLLECTION,
    embedding_function=embeddings,
    persist_directory=CHROMA_DIR,
)

# ---------------------------
# 4. max_batch_size 확인
# ---------------------------
client = vectorstore._client
max_batch_size = getattr(client, "max_batch_size", 5000)
print("max_batch_size =", max_batch_size)

# ---------------------------
# 5. 배치로 나눠서 적재
# ---------------------------
total_docs  = len(docs)
num_batches = math.ceil(total_docs / max_batch_size)

for batch_idx in range(num_batches):
    start = batch_idx * max_batch_size
    end   = min(start + max_batch_size, total_docs)

    vectorstore.add_documents(documents=docs[start:end], ids=ids[start:end])
    print(f"[{batch_idx + 1}/{num_batches}] {start} ~ {end} 적재 완료")

print("Chroma 적재 완료 →", CHROMA_DIR)
