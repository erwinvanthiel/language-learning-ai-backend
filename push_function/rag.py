"""Best-effort indexing of autonomous push messages in the shared AI Search index."""

import hashlib
import logging
import os
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
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
from openai import OpenAI
from openai import AzureOpenAI


@lru_cache(maxsize=1)
def _clients() -> tuple[SearchIndexClient, SearchClient]:
    credential = DefaultAzureCredential()
    endpoint = os.environ["AZURE_SEARCH_ENDPOINT"]
    name = os.environ["AZURE_SEARCH_INDEX_NAME"]
    return SearchIndexClient(endpoint, credential), SearchClient(endpoint, name, credential)


def _ensure_index() -> None:
    if not os.getenv("AZURE_SEARCH_ENDPOINT") or not os.getenv("AZURE_SEARCH_INDEX_NAME"):
        return
    index_client, _ = _clients()
    dimensions = int(os.getenv("AZURE_OPENAI_EMBEDDING_DIMENSIONS", "1536"))
    index_client.create_or_update_index(SearchIndex(
        name=os.environ["AZURE_SEARCH_INDEX_NAME"],
        fields=[
            SimpleField(name="id", type=SearchFieldDataType.String, key=True, filterable=True),
            SearchableField(name="content", type=SearchFieldDataType.String),
            SearchField(name="content_vector", type=SearchFieldDataType.Collection(SearchFieldDataType.Single), searchable=True,
                        vector_search_dimensions=dimensions, vector_search_profile_name="default-vector-profile"),
            SimpleField(name="document_id", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="owner_id", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="source_url", type=SearchFieldDataType.String, filterable=True),
            SearchableField(name="source_title", type=SearchFieldDataType.String),
            SimpleField(name="source_hash", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="chunk_index", type=SearchFieldDataType.Int32, filterable=True),
            SimpleField(name="source_type", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="updated_at", type=SearchFieldDataType.DateTimeOffset, filterable=True, sortable=True),
        ],
        vector_search=VectorSearch(
            algorithms=[HnswAlgorithmConfiguration(name="default-hnsw")],
            profiles=[VectorSearchProfile(name="default-vector-profile", algorithm_configuration_name="default-hnsw")],
        ),
    ))


def index_push_message(text: str, user_id: str, source_url: str, title: str, snippet: str) -> None:
    if not text.strip() or not os.getenv("AZURE_SEARCH_ENDPOINT") or not os.getenv("AZURE_SEARCH_INDEX_NAME"):
        return
    try:
        _ensure_index()
        embedding_deployment = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT")
        endpoint = os.environ["AZURE_OPENAI_ENDPOINT"].rstrip("/")
        token_provider = get_bearer_token_provider(DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default")
        openai = OpenAI(base_url=f"{endpoint}/openai/v1/", api_key=token_provider)
        content = f"{text.strip()}\n\nSource: {title}\n{snippet}\n{source_url}"[:12000]
        source_hash = hashlib.sha256(content.encode()).hexdigest()
        document_id = hashlib.sha256(f"{user_id}:{source_url}".encode()).hexdigest()[:40]
        _, search = _clients()
        existing = next(iter(search.search(search_text="*", filter=f"document_id eq '{document_id}'", select=["source_hash"], top=1)), None)
        if existing and existing.get("source_hash") == source_hash:
            return
        try:
            vector = openai.embeddings.create(model=embedding_deployment, input=[content]).data[0].embedding
        except Exception as error:
            logging.warning("OpenAI-compatible push embedding failed; retrying native endpoint: %s", error)
            native = AzureOpenAI(
                azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
                api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"),
                azure_ad_token_provider=get_bearer_token_provider(
                    DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
                ),
            )
            vector = native.embeddings.create(model=embedding_deployment, input=[content]).data[0].embedding
        search.upload_documents(documents=[{
            "id": f"{document_id}-0", "document_id": document_id, "owner_id": user_id,
            "content": content, "content_vector": vector, "source_url": source_url[:2000],
            "source_title": title[:500], "source_hash": source_hash, "chunk_index": 0,
            "source_type": "push", "updated_at": datetime.now(timezone.utc).isoformat(),
        }])
    except Exception:
        logging.exception("Could not index push message in Azure AI Search")
