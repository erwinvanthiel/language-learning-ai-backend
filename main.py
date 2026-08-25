import json
import os
from datetime import datetime, timezone
from functools import lru_cache
from typing import Annotated, Any
from uuid import uuid4

from azure.core.exceptions import HttpResponseError
from azure.data.tables import TableServiceClient
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from fastapi import Depends
from fastapi import FastAPI
from fastapi import Header
from fastapi import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from google.auth.exceptions import GoogleAuthError
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from openai import OpenAI, OpenAIError
from pydantic import BaseModel, Field


class GenerateRequest(BaseModel):
    context: dict[str, Any] = Field(description="Context forwarded to Azure OpenAI")


class GenerateResponse(BaseModel):
    response: str


class StoredMessage(BaseModel):
    id: str
    text: str
    created_at: str


@lru_cache
def get_openai_client() -> OpenAI:
    endpoint = os.environ["AZURE_OPENAI_ENDPOINT"].rstrip("/")
    token_provider = get_bearer_token_provider(
        DefaultAzureCredential(),
        "https://cognitiveservices.azure.com/.default",
    )
    return OpenAI(
        base_url=f"{endpoint}/openai/v1/",
        api_key=token_provider,
    )


@lru_cache
def get_table_service_client() -> TableServiceClient:
    return TableServiceClient(
        endpoint=os.environ["AZURE_TABLE_ENDPOINT"].rstrip("/"),
        credential=DefaultAzureCredential(),
    )


def get_current_user(
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    if not client_id:
        raise HTTPException(status_code=503, detail="Google authentication is not configured.")

    scheme, _, credential = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not credential:
        raise HTTPException(status_code=401, detail="Authentication required.")

    try:
        claims = id_token.verify_oauth2_token(
            credential,
            google_requests.Request(),
            client_id,
        )
    except (ValueError, GoogleAuthError) as error:
        raise HTTPException(status_code=401, detail="Invalid Google ID token.") from error

    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Google token has no user ID.")
    return str(user_id)


def store_user(user_id: str) -> None:
    get_table_service_client().get_table_client("Users").upsert_entity(
        {"PartitionKey": "google", "RowKey": user_id}
    )


def store_message(user_id: str, text: str) -> None:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    get_table_service_client().get_table_client("Messages").create_entity(
        {
            "PartitionKey": user_id,
            "RowKey": f"{timestamp}_{uuid4().hex}",
            "Text": text,
        }
    )


def register_user_and_message(user_id: str, text: str | None = None) -> None:
    try:
        store_user(user_id)
        if text is not None:
            store_message(user_id, text)
    except (HttpResponseError, KeyError) as error:
        raise HTTPException(status_code=503, detail="Message storage is unavailable.") from error


app = FastAPI(title="Language Learning AI API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "https://yellow-coast-0325af203.7.azurestaticapps.net",
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/")
def read_root() -> dict[str, str]:
    return {"message": "Hello, world!"}


@app.get("/messages", response_model=list[StoredMessage])
def read_messages(user_id: Annotated[str, Depends(get_current_user)]) -> list[StoredMessage]:
    register_user_and_message(user_id)
    try:
        entities = list(
            get_table_service_client()
            .get_table_client("Messages")
            .query_entities(f"PartitionKey eq '{user_id}'")
        )
    except (HttpResponseError, KeyError) as error:
        raise HTTPException(status_code=503, detail="Message storage is unavailable.") from error

    return [
        StoredMessage(
            id=entity["RowKey"],
            text=entity["Text"],
            created_at=entity["RowKey"].split("_", 1)[0],
        )
        for entity in sorted(entities, key=lambda item: item["RowKey"])[-100:]
    ]


@app.post("/generate", response_model=GenerateResponse)
def generate(
    request: GenerateRequest,
    user_id: Annotated[str, Depends(get_current_user)],
) -> GenerateResponse:
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
    if not deployment or not os.getenv("AZURE_OPENAI_ENDPOINT"):
        raise HTTPException(status_code=503, detail="Azure OpenAI is not configured.")

    message_text = request.context.get("text")
    if not isinstance(message_text, str):
        message_text = json.dumps(request.context, ensure_ascii=False)
    register_user_and_message(user_id, message_text)

    try:
        result = get_openai_client().responses.create(
            model=deployment,
            instructions=(
                "You are a helpful language-learning assistant. Use the supplied "
                "context to give a clear and concise response."
            ),
            input=json.dumps(request.context, ensure_ascii=False),
            max_output_tokens=1000,
        )
    except OpenAIError as error:
        raise HTTPException(
            status_code=502,
            detail="Azure OpenAI could not generate a response.",
        ) from error

    return GenerateResponse(response=result.output_text)
