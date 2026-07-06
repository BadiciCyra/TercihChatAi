from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict
from sentence_transformers import SentenceTransformer, CrossEncoder, util
from typing import List, Dict, Any, Optional
from contextlib import asynccontextmanager
from fastapi.concurrency import run_in_threadpool
import torch

BI_ENCODER_PATH = "paraphrase-multilingual-MiniLM-L12-v2"
CROSS_ENCODER_PATH = "cross-encoder/ms-marco-MiniLM-L-6-v2"

bi_encoder_model: Optional[SentenceTransformer] = None
cross_encoder_model: Optional[CrossEncoder] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global bi_encoder_model, cross_encoder_model
    print("[RE-RANKER] [INFO] Modeller yükleniyor...")

    # Yükleme CPU/GPU'da zaman alabilir; ama startup'ta olması iyi.
    bi_encoder_model = SentenceTransformer(BI_ENCODER_PATH)
    cross_encoder_model = CrossEncoder(CROSS_ENCODER_PATH, max_length=512)

    print("[RE-RANKER] [OK] Modeller hazır.")
    yield
    print("[RE-RANKER] [INFO] Servis durduruldu.")


app = FastAPI(title="Local Two-Stage Re-Ranking Service", lifespan=lifespan)


class DocumentSnippet(BaseModel):
    url: str
    title: str
    snippet: str

    # retrieve.py'den gelebilecek ekstra alanları kabul et
    model_config = ConfigDict(extra="allow")


class ReRankRequest(BaseModel):
    original_query: str
    documents: List[DocumentSnippet]


def _to_float(x: Any) -> float:
    """torch/numpy/python sayı tiplerini güvenle float'a çevir."""
    try:
        # torch scalar
        if hasattr(x, "item"):
            return float(x.item())
    except Exception:
        pass
    return float(x)


def _stage1_biencoder(query: str, doc_contents: List[str], top_k_fast: int) -> List[int]:
    assert bi_encoder_model is not None

    corpus_embeddings = bi_encoder_model.encode(doc_contents, convert_to_tensor=True)
    query_embedding = bi_encoder_model.encode(query, convert_to_tensor=True)

    fast_scores = util.cos_sim(query_embedding, corpus_embeddings)[0]
    top_k_fast = min(top_k_fast, len(doc_contents))

    # torch.topk -> indices tensor
    top_results = torch.topk(fast_scores, k=top_k_fast)
    return top_results.indices.tolist()


def _stage2_crossencoder(query: str, doc_contents: List[str], top_indices: List[int]) -> List[float]:
    assert cross_encoder_model is not None

    pairs = [[query, doc_contents[i]] for i in top_indices]
    scores = cross_encoder_model.predict(pairs)

    # scores numpy array / list olabilir -> float list'e çevir
    return [_to_float(s) for s in scores]


@app.post("/rerank")
async def rerank_documents_two_stage(request: ReRankRequest) -> Dict[str, Any]:
    if not request.documents:
        return {"ranked_documents": []}

    if bi_encoder_model is None or cross_encoder_model is None:
        # Uygulama startup tamamlanmadan istek gelirse
        return {"ranked_documents": []}

    doc_contents = [f"{doc.title} {doc.snippet}" for doc in request.documents]

    print(f"[RE-RANKER] Aşama 1: {len(doc_contents)} aday Bi-Encoder ile eleniyor...")
    top_indices = await run_in_threadpool(_stage1_biencoder, request.original_query, doc_contents, 5)

    if not top_indices:
        return {"ranked_documents": []}

    print("[RE-RANKER] Aşama 2: Cross-Encoder ile sıralanıyor...")
    cross_scores = await run_in_threadpool(_stage2_crossencoder, request.original_query, doc_contents, top_indices)

    # (score, original_doc_index) eşleştirmesi
    ranked = []
    for score, doc_i in sorted(zip(cross_scores, top_indices), key=lambda x: x[0], reverse=True):
        doc = request.documents[doc_i]
        ranked.append(
            {
                "score": float(score),
                "document": doc.model_dump(),
            }
        )

    return {"ranked_documents": ranked}