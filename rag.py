"""Azure AI Search knowledge ingestion and hybrid retrieval helpers.

All operations are best-effort at request time: a Search outage must not take
down chat or message persistence. Index documents are scoped to either ``global``
web knowledge or the authenticated user's private conversation knowledge.
"""

import hashlib
import html
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from html.parser import HTMLParser
from typing import Any

import httpx
from openai import AzureOpenAI
from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SearchableField,
    SimpleField,
    VectorSearch,
    VectorSearchProfile,
)
from azure.search.documents.models import VectorizedQuery


EMBEDDING_DIMENSIONS = int(os.getenv("AZURE_OPENAI_EMBEDDING_DIMENSIONS", "1536"))


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg", "canvas", "template"}:
            self._ignored += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg", "canvas", "template"} and self._ignored:
            self._ignored -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored:
            self.parts.append(data)


def normalize_text(value: str) -> str:
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def extract_page_text(markup: str) -> str:
    parser = _TextExtractor()
    parser.feed(markup[:1_000_000])
    return normalize_text(" ".join(parser.parts))


def chunk_text(text: str, size: int = 1800, overlap: int = 200) -> list[str]:
    text = normalize_text(text)
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            boundary = text.rfind(" ", start + size // 2, end)
            if boundary > start:
                end = boundary
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def _configured() -> bool:
    return bool(os.getenv("AZURE_SEARCH_ENDPOINT") and os.getenv("AZURE_SEARCH_INDEX_NAME"))


@lru_cache(maxsize=1)
def search_index_client() -> SearchIndexClient:
    return SearchIndexClient(os.environ["AZURE_SEARCH_ENDPOINT"], DefaultAzureCredential())


@lru_cache(maxsize=1)
def search_client() -> SearchClient:
    return SearchClient(
        os.environ["AZURE_SEARCH_ENDPOINT"],
        os.environ["AZURE_SEARCH_INDEX_NAME"],
        DefaultAzureCredential(),
    )


@lru_cache(maxsize=1)
def ensure_index() -> None:
    if not _configured():
        return
    fields = [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True, filterable=True),
        SearchableField(name="content", type=SearchFieldDataType.String),
        SearchField(
            name="content_vector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            hidden=False,
            vector_search_dimensions=EMBEDDING_DIMENSIONS,
            vector_search_profile_name="default-vector-profile",
        ),
        SimpleField(name="document_id", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="owner_id", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="source_url", type=SearchFieldDataType.String, filterable=True),
        SearchableField(name="source_title", type=SearchFieldDataType.String),
        SimpleField(name="source_hash", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="chunk_index", type=SearchFieldDataType.Int32, filterable=True),
        SimpleField(name="source_type", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="updated_at", type=SearchFieldDataType.DateTimeOffset, filterable=True, sortable=True),
    ]
    index = SearchIndex(
        name=os.environ["AZURE_SEARCH_INDEX_NAME"],
        fields=fields,
        vector_search=VectorSearch(
            algorithms=[HnswAlgorithmConfiguration(name="default-hnsw")],
            profiles=[VectorSearchProfile(name="default-vector-profile", algorithm_configuration_name="default-hnsw")],
        ),
    )
    search_index_client().create_or_update_index(index)


def _embed(client: Any, texts: list[str]) -> list[list[float]]:
    deployment = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT")
    if not deployment or not texts:
        return []
    try:
        result = client.embeddings.create(model=deployment, input=texts)
    except Exception as error:
        logging.warning("OpenAI-compatible embedding call failed; retrying native Azure endpoint: %s", error)
        from azure.identity import get_bearer_token_provider

        native_client = AzureOpenAI(
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"),
            azure_ad_token_provider=get_bearer_token_provider(
                DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
            ),
        )
        result = native_client.embeddings.create(model=deployment, input=texts)
    return [item.embedding for item in sorted(result.data, key=lambda item: item.index)]


def _filter(owner_id: str) -> str:
    safe = owner_id.replace("'", "''")
    freshness_days = max(1, int(os.getenv("RAG_FRESHNESS_DAYS", "7")))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=freshness_days)).isoformat().replace("+00:00", "Z")
    scope = f"(owner_id eq 'global' or owner_id eq '{safe}')"
    # Conversation and push documents remain useful indefinitely; web pages
    # are revalidated after the configured freshness window.
    return f"{scope} and (source_type ne 'web' or updated_at ge {cutoff})"


def _redupe(results: list[dict[str, Any]], budget: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    used = 0
    for result in results:
        content = normalize_text(str(result.get("content", "")))
        if not content:
            continue
        words = set(content.lower().split())
        if any(len(words & set(str(item.get("content", "")).lower().split())) / max(1, len(words)) > 0.8 for item in selected):
            continue
        if used + len(content) > budget:
            break
        selected.append({
            "content": content,
            "source_url": str(result.get("source_url", "")),
            "source_title": str(result.get("source_title", "")),
            "source_type": str(result.get("source_type", "")),
        })
        used += len(content)
    return selected


def retrieve(query: str, owner_id: str, openai_client: Any) -> list[dict[str, Any]]:
    if not _configured() or not query.strip():
        return []
    try:
        ensure_index()
        vectors = _embed(openai_client, [query])
        vector_query = VectorizedQuery(
            vector=vectors[0], k_nearest_neighbors=int(os.getenv("RAG_TOP_K", "8")), fields="content_vector"
        ) if vectors else None
        kwargs: dict[str, Any] = {
            "search_text": query[:1000],
            "filter": _filter(owner_id),
            "top": int(os.getenv("RAG_TOP_K", "8")),
            "select": ["content", "source_url", "source_title", "source_type", "updated_at"],
        }
        if vector_query:
            kwargs["vector_queries"] = [vector_query]
        return _redupe(list(search_client().search(**kwargs)), int(os.getenv("RAG_EVIDENCE_BUDGET_CHARS", "12000")))
    except Exception:
        logging.exception("Azure AI Search retrieval failed")
        return []


def _existing_hash(source_url: str, owner_id: str) -> str | None:
    if not _configured():
        return None
    safe = source_url.replace("'", "''")
    try:
        owner = owner_id.replace("'", "''")
        rows = search_client().search(
            search_text="*",
            filter=f"source_url eq '{safe}' and owner_id eq '{owner}'",
            select=["source_hash"],
            top=1,
        )
        row = next(iter(rows), None)
        return str(row.get("source_hash")) if row and row.get("source_hash") else None
    except Exception:
        return None


def _source_is_fresh(source_url: str, owner_id: str) -> bool:
    """Avoid downloading a page again while its indexed copy is fresh."""
    if not _configured():
        return False
    safe_url = source_url.replace("'", "''")
    safe_owner = owner_id.replace("'", "''")
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, int(os.getenv("RAG_FRESHNESS_DAYS", "7"))))).isoformat().replace("+00:00", "Z")
    try:
        rows = search_client().search(
            search_text="*",
            filter=f"source_url eq '{safe_url}' and owner_id eq '{safe_owner}' and source_type eq 'web' and updated_at ge {cutoff}",
            select=["id"],
            top=1,
        )
        return next(iter(rows), None) is not None
    except Exception:
        return False


def index_document(
    content: str,
    owner_id: str,
    source_url: str,
    source_title: str,
    source_type: str,
    openai_client: Any,
) -> list[dict[str, str]]:
    if not _configured() or not content.strip():
        return []
    normalized = normalize_text(content)
    source_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    if _existing_hash(source_url, owner_id) == source_hash:
        return []
    try:
        ensure_index()
        chunks = chunk_text(normalized)
        embeddings = _embed(openai_client, chunks)
        if len(embeddings) != len(chunks):
            logging.warning("Skipping index update because embeddings were unavailable")
            return []
        document_id = hashlib.sha256(f"{owner_id}:{source_url}".encode()).hexdigest()[:40]
        old = list(search_client().search(search_text="*", filter=f"document_id eq '{document_id}'", select=["id"], top=1000))
        if old:
            search_client().delete_documents(documents=[{"id": row["id"]} for row in old])
        now = datetime.now(timezone.utc).isoformat()
        documents = [
            {
                "id": f"{document_id}-{index}",
                "document_id": document_id,
                "owner_id": owner_id,
                "content": chunk,
                "content_vector": vector,
                "source_url": source_url[:2000],
                "source_title": source_title[:500],
                "source_hash": source_hash,
                "chunk_index": index,
                "source_type": source_type,
                "updated_at": now,
            }
            for index, (chunk, vector) in enumerate(zip(chunks, embeddings))
        ]
        if documents:
            search_client().upload_documents(documents=documents)
        return [{"content": chunk, "source_url": source_url, "source_title": source_title, "source_type": source_type} for chunk in chunks]
    except Exception:
        logging.exception("Azure AI Search indexing failed")
        return []


def fetch_and_index(result: dict[str, str], owner_id: str, openai_client: Any) -> list[dict[str, str]]:
    url = result.get("url", "")
    if not url.startswith(("https://", "http://")):
        return []
    if _source_is_fresh(url, owner_id):
        return []
    try:
        response = httpx.get(url, follow_redirects=True, timeout=8, headers={"User-Agent": "language-learning-ai/1.0"})
        response.raise_for_status()
        text = extract_page_text(response.text)
        return index_document(text, owner_id, str(response.url), result.get("title", ""), "web", openai_client)
    except (httpx.HTTPError, ValueError):
        logging.exception("Web page retrieval failed for %s", url)
        return []


def index_message(text: str, owner_id: str, openai_client: Any, source_url: str = "") -> None:
    title = "Conversation message"
    index_document(text, owner_id, source_url or f"conversation://{owner_id}", title, "push" if source_url else "conversation", openai_client)


def delete_owner_documents(owner_id: str) -> int:
    """Delete all private conversation and push evidence for one user."""
    if not _configured():
        return 0
    try:
        ensure_index()
        safe_owner = owner_id.replace("'", "''")
        client = search_client()
        ids = [row["id"] for row in client.search(
            search_text="*",
            filter=f"owner_id eq '{safe_owner}'",
            select=["id"],
            top=100000,
        ) if row.get("id")]
        deleted = 0
        for start in range(0, len(ids), 1000):
            result = client.delete_documents(documents=[{"id": value} for value in ids[start:start + 1000]])
            for item in result:
                succeeded = getattr(item, "succeeded", None)
                if succeeded is None and isinstance(item, dict):
                    succeeded = item.get("succeeded", False)
                deleted += int(bool(succeeded))
        return deleted
    except Exception:
        logging.exception("Azure AI Search history deletion failed")
        return 0
