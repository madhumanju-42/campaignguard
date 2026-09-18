"""TF-IDF policy retrieval (scikit-learn). Small, transparent, and reproducible."""

from __future__ import annotations

from functools import lru_cache

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from .data import policies


@lru_cache
def _index():
    pols = list(policies().values())
    docs = [f"{p['title']}. {p['text']}" for p in pols]
    vec = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), sublinear_tf=True)
    matrix = vec.fit_transform(docs)
    return pols, vec, matrix


def search_policies(query: str, channel: str | None = None, top_k: int = 3) -> list[dict]:
    pols, vec, matrix = _index()
    top_k = max(1, min(int(top_k), 10))
    scores = cosine_similarity(vec.transform([query]), matrix).ravel()
    ranked = sorted(range(len(pols)), key=lambda i: scores[i], reverse=True)
    out = []
    for i in ranked:
        p = pols[i]
        if channel and channel not in p["channels"]:
            continue
        if scores[i] <= 0:
            continue
        out.append(
            {
                "policy_id": p["policy_id"],
                "title": p["title"],
                "text": p["text"],
                "channels": p["channels"],
                "score": round(float(scores[i]), 4),
            }
        )
        if len(out) >= top_k:
            break
    return out
