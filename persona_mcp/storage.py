"""Persona-namespaced vector storage via Qdrant (hosted, persists across
restarts/redeploys - unlike the earlier local-Chroma MVP). Embeddings are
computed locally via fastembed (ONNX, no API key, no per-call cost), same
zero-cost-embeddings property the original Chroma-based design had.
"""

import os
import uuid
from dataclasses import dataclass

from fastembed import TextEmbedding
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from persona_mcp.chunking import Chunk

EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"

# Cosine-similarity score above which a newly-written live memory is treated
# as a near-duplicate of something already stored and skipped. Heuristic, not
# exact - catches "said the same thing twice" without blocking genuinely new
# but related facts.
DUPLICATE_SCORE_THRESHOLD = 0.95

_client: QdrantClient | None = None
_embedder: TextEmbedding | None = None


def _get_client() -> QdrantClient:
    global _client
    if _client is None:
        _client = QdrantClient(url=os.environ["QDRANT_URL"], api_key=os.environ["QDRANT_API_KEY"])
    return _client


def _get_embedder() -> TextEmbedding:
    global _embedder
    if _embedder is None:
        _embedder = TextEmbedding(model_name=EMBEDDING_MODEL)
    return _embedder


def _embed(texts: list[str]) -> list[list[float]]:
    return [vec.tolist() for vec in _get_embedder().embed(texts)]


def _collection_name(persona_id: str) -> str:
    return f"persona_{persona_id}"


@dataclass
class PersonaCollection:
    """A handle to one persona's Qdrant collection - passed around the way a
    Chroma Collection object used to be, so call sites elsewhere don't change."""

    persona_id: str
    collection_name: str


def get_collection(persona_id: str) -> PersonaCollection:
    client = _get_client()
    name = _collection_name(persona_id)
    if not client.collection_exists(name):
        dim = len(_embed(["dimension probe"])[0])
        client.create_collection(collection_name=name, vectors_config=VectorParams(size=dim, distance=Distance.COSINE))
    return PersonaCollection(persona_id=persona_id, collection_name=name)


def reset_collection(persona_id: str) -> PersonaCollection:
    client = _get_client()
    name = _collection_name(persona_id)
    if client.collection_exists(name):
        client.delete_collection(name)
    return get_collection(persona_id)


def add_chunks(collection: PersonaCollection, chunks: list[Chunk]) -> None:
    if not chunks:
        return
    client = _get_client()
    vectors = _embed([c.text for c in chunks])
    points = []
    for i, (chunk, vector) in enumerate(zip(chunks, vectors)):
        raw_id = f"{chunk.metadata.get('source_file', 'unknown')}:{chunk.metadata.get('chunk_index', i)}"
        point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, raw_id))
        points.append(PointStruct(id=point_id, vector=vector, payload={"text": chunk.text, **chunk.metadata}))
    client.upsert(collection_name=collection.collection_name, points=points)


def query_memories(collection: PersonaCollection, query: str, k: int = 5) -> list[dict]:
    client = _get_client()
    count = client.count(collection.collection_name).count
    if count == 0:
        return []
    k = min(k, count)
    query_vector = _embed([query])[0]
    results = client.query_points(collection_name=collection.collection_name, query=query_vector, limit=k)

    flattened = []
    for point in results.points:
        payload = dict(point.payload or {})
        text = payload.pop("text", "")
        flattened.append({"text": text, "metadata": payload, "score": point.score})
    return flattened


def is_near_duplicate(collection: PersonaCollection, text: str) -> bool:
    nearest = query_memories(collection, text, k=1)
    return bool(nearest) and nearest[0]["score"] > DUPLICATE_SCORE_THRESHOLD


def add_memory(collection: PersonaCollection, text: str, metadata: dict) -> str:
    """Write a single live-session memory (as opposed to bulk ingestion via add_chunks)."""
    client = _get_client()
    vector = _embed([text])[0]
    memory_id = str(uuid.uuid4())
    client.upsert(
        collection_name=collection.collection_name,
        points=[PointStruct(id=memory_id, vector=vector, payload={"text": text, **metadata})],
    )
    return memory_id
