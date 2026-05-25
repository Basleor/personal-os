"""
Personal OS — Layer 01: 混合向量检索 (Hybrid Semantic Search)
External drive: /Volumes/13384923891/hermes-agent/
v1.0 — Pure Python TF-IDF + Cosine Similarity, zero external deps.

Design:
  - Character n-gram tokenizer for Chinese (bigram + trigram)
  - TF-IDF weighting, L2 normalization
  - Vocabulary stored in vector_vocab table (token_str → numeric ID)
  - Sparse vectors in vector_index table (JSON blobs of {token_id: weight})
  - Cosine similarity ranking
  - Rebuildable index from ideas + session_tasks tables
  - Keyword fallback via SQLite LIKE
"""
import json
import math
import time
from collections import Counter
from core.db import get_connection, init_db, init_vector_index


# ═══════════════════════════════════════════════════════════════
#  Tokenizer — character n-grams (bigram + trigram)
# ═══════════════════════════════════════════════════════════════

def tokenize(text: str) -> list[str]:
    """Tokenize text into character bigrams and trigrams.
    
    Character n-grams capture semantic proximity for Chinese
    without dictionary-based segmentation.
    """
    if not text:
        return []
    tokens = []
    n = len(text)
    for i in range(n - 1):
        tokens.append(text[i:i+2])
    for i in range(n - 2):
        tokens.append(text[i:i+3])
    return tokens if tokens else [text]  # Fallback for single char


# ═══════════════════════════════════════════════════════════════
#  Vocabulary management — token string ↔ numeric ID
# ═══════════════════════════════════════════════════════════════

def _ensure_vocab_table():
    """Create the vocabulary table (idempotent)."""
    conn = get_connection()
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS vector_vocab (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT UNIQUE NOT NULL,
            doc_count INTEGER DEFAULT 1
        )
    ''')
    conn.commit()
    conn.close()


def _load_vocabulary() -> dict[str, tuple[int, int]]:
    """Load vocabulary: token -> (token_id, doc_count)."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT token, id, doc_count FROM vector_vocab")
    vocab = {}
    for row in c.fetchall():
        vocab[row[0]] = (row[1], row[2])
    conn.close()
    return vocab


def _store_vocabulary(token_df: dict[str, int]):
    """Store or update vocabulary with document frequencies."""
    conn = get_connection()
    c = conn.cursor()
    for token, count in token_df.items():
        c.execute(
            "INSERT INTO vector_vocab (token, doc_count) VALUES (?, ?) "
            "ON CONFLICT(token) DO UPDATE SET doc_count=?",
            (token, count, count)
        )
    conn.commit()
    conn.close()


def _compute_idf(N: int, df: int) -> float:
    """Smooth IDF."""
    return math.log((N + 1) / (df + 1)) + 1


# ═══════════════════════════════════════════════════════════════
#  Vectorization
# ═══════════════════════════════════════════════════════════════

def _sparse_tfidf(tokens: list[str], vocab: dict[str, tuple[int, int]],
                  N: int) -> dict[int, float]:
    """Compute L2-normalized sparse TF-IDF vector.
    
    Returns {token_id: weight} for non-zero entries.
    """
    if not tokens:
        return {}
    tf = Counter(tokens)
    vec = {}
    for token, count in tf.items():
        entry = vocab.get(token)
        if entry is None:
            continue
        tid, df = entry
        tf_val = 1 + math.log(count)
        idf_val = _compute_idf(N, df)
        vec[tid] = tf_val * idf_val
    # L2 normalize
    norm = math.sqrt(sum(v * v for v in vec.values()))
    if norm > 0:
        vec = {k: v / norm for k, v in vec.items()}
    return vec


def _query_vector(query_tokens: list[str], vocab: dict[str, tuple[int, int]],
                  N: int) -> dict[int, float]:
    """Build and normalize a query vector. No IDF weighting (query is short)."""
    if not query_tokens:
        return {}
    tf = Counter(query_tokens)
    vec = {}
    for token, count in tf.items():
        entry = vocab.get(token)
        if entry is None:
            continue
        tid, df = entry
        idf_val = _compute_idf(N, df)
        vec[tid] = (1 + math.log(count)) * idf_val
    norm = math.sqrt(sum(v * v for v in vec.values()))
    if norm > 0:
        vec = {k: v / norm for k, v in vec.items()}
    return vec


def cosine_similarity(a: dict[int, float], b: dict[int, float]) -> float:
    """Cosine similarity between two L2-normalized sparse vectors."""
    if not a or not b:
        return 0.0
    # Iterate over the smaller vector
    if len(a) > len(b):
        a, b = b, a
    score = 0.0
    for tid, w in a.items():
        w2 = b.get(tid)
        if w2 is not None:
            score += w * w2
    return score


# ═══════════════════════════════════════════════════════════════
#  Index builder
# ═══════════════════════════════════════════════════════════════

def _gather_documents() -> list[dict]:
    """Collect all indexable documents from ideas and session_tasks."""
    conn = get_connection()
    c = conn.cursor()
    docs = []
    c.execute("SELECT id, raw_idea, current_task_type, COALESCE(project,'') FROM ideas")
    for row in c.fetchall():
        text = row[1] or ""
        if text.strip():
            docs.append({
                "source_table": "ideas",
                "source_id": row[0],
                "text": text,
                "meta": {"task_type": row[2], "project": row[3]},
            })
    c.execute("SELECT id, description, task_type, COALESCE(project,'') FROM session_tasks")
    for row in c.fetchall():
        text = row[1] or ""
        if text.strip():
            docs.append({
                "source_table": "session_tasks",
                "source_id": row[0],
                "text": text,
                "meta": {"task_type": row[2], "project": row[3]},
            })
    conn.close()
    return docs


def rebuild_index() -> tuple[int, int]:
    """Rebuild the full vector index. Returns (vocab_size, docs_indexed)."""
    init_db()
    init_vector_index()
    _ensure_vocab_table()
    docs = _gather_documents()
    if not docs:
        return 0, 0

    # Build vocabulary: compute document frequencies for each token
    token_df = Counter()
    doc_tokens = []
    for doc in docs:
        tokens = set(tokenize(doc["text"]))
        for t in tokens:
            token_df[t] += 1
        doc_tokens.append(tokens)

    _store_vocabulary(dict(token_df))
    vocab = _load_vocabulary()
    N = len(docs)

    # Clear old index
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM vector_index")
    now_ts = time.strftime("%Y-%m-%d %H:%M:%S")
    indexed = 0

    for i, doc in enumerate(docs):
        tokens = list(doc_tokens[i])
        if not tokens:
            continue
        vec = _sparse_tfidf(tokens, vocab, N)
        if not vec:
            continue
        # Store as JSON (keys are strings for SQLite compatibility)
        blob = json.dumps({
            "v": {str(k): round(v, 6) for k, v in vec.items()},
            "m": doc["meta"],
            "t": doc["text"][:300],
        }, ensure_ascii=False)
        c.execute(
            "INSERT OR REPLACE INTO vector_index (source_table, source_id, vector_blob, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (doc["source_table"], doc["source_id"], blob, now_ts)
        )
        indexed += 1

    conn.commit()
    conn.close()
    return len(token_df), indexed


# ═══════════════════════════════════════════════════════════════
#  Search
# ═══════════════════════════════════════════════════════════════

def _load_indexed_docs() -> list[dict]:
    """Load all vector_index entries with decoded vectors."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT source_table, source_id, vector_blob FROM vector_index")
    docs = []
    for row in c.fetchall():
        blob = json.loads(row[2])
        docs.append({
            "source_table": row[0],
            "source_id": row[1],
            "vec": {int(k): v for k, v in blob["v"].items()},
            "meta": blob.get("m", {}),
            "text": blob.get("t", ""),
        })
    conn.close()
    return docs


def search(query: str, limit: int = 10) -> list[dict]:
    """Semantic (TF-IDF + cosine) search over indexed documents.
    
    Falls back to keyword search if index is empty.
    """
    _ensure_vocab_table()
    vocab = _load_vocabulary()
    if not vocab:
        # No index built — fallback to keyword
        return search_keyword(query, limit)

    docs = _load_indexed_docs()
    if not docs:
        return []

    q_tokens = tokenize(query)
    N = len(docs)
    q_vec = _query_vector(q_tokens, vocab, N)
    if not q_vec:
        return search_keyword(query, limit)

    results = []
    for doc in docs:
        score = cosine_similarity(q_vec, doc["vec"])
        if score > 0.001:  # Minimal threshold
            results.append({
                "score": round(score, 4),
                "source_table": doc["source_table"],
                "source_id": doc["source_id"],
                "text": doc["text"],
                "meta": doc["meta"],
            })

    results.sort(key=lambda r: r["score"], reverse=True)
    if not results:
        return search_keyword(query, limit)
    return results[:limit]


def search_keyword(query: str, limit: int = 10) -> list[dict]:
    """Fallback keyword search using SQLite LIKE."""
    conn = get_connection()
    c = conn.cursor()
    results = []
    like_pat = f"%{query}%"

    c.execute(
        "SELECT id, raw_idea, current_task_type, COALESCE(project,'') FROM ideas "
        "WHERE raw_idea LIKE ? ORDER BY id DESC LIMIT ?",
        (like_pat, limit)
    )
    for row in c.fetchall():
        results.append({
            "score": 1.0,
            "source_table": "ideas",
            "source_id": row[0],
            "text": (row[1] or "")[:200],
            "meta": {"task_type": row[2], "project": row[3]},
        })

    c.execute(
        "SELECT id, description, task_type, COALESCE(project,'') FROM session_tasks "
        "WHERE description LIKE ? ORDER BY id DESC LIMIT ?",
        (like_pat, limit)
    )
    for row in c.fetchall():
        results.append({
            "score": 1.0,
            "source_table": "session_tasks",
            "source_id": row[0],
            "text": (row[1] or "")[:200],
            "meta": {"task_type": row[2], "project": row[3]},
        })

    conn.close()
    return results[:limit]


def search_all(query: str, limit: int = 10) -> list[dict]:
    """Combined search: vector first, keyword fallback."""
    return search(query, limit=limit)
