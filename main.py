import json
import os
from datetime import datetime, timezone
from functools import lru_cache
from typing import Annotated, Any, Literal
from uuid import uuid4

from azure.core.exceptions import HttpResponseError
from azure.data.tables import TableServiceClient, UpdateMode
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


class LanguageSettings(BaseModel):
    native_language: str = Field(default="English", min_length=1, max_length=80)
    learning_language: str = Field(default="Dutch", min_length=1, max_length=80)


class FeedbackAnnotation(BaseModel):
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    comment: str = Field(min_length=1, max_length=500)


class GenerateResponse(BaseModel):
    response: str
    feedback: list[FeedbackAnnotation] = Field(default_factory=list)


class StoredMessage(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    text: str
    created_at: str
    feedback: list[FeedbackAnnotation] = Field(default_factory=list)


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
        {"PartitionKey": "google", "RowKey": user_id}, mode=UpdateMode.MERGE
    )


def get_language_settings(user_id: str) -> LanguageSettings:
    try:
        entity = get_table_service_client().get_table_client("Users").get_entity(
            partition_key="google", row_key=user_id
        )
    except Exception as error:
        if getattr(error, "status_code", None) == 404:
            return LanguageSettings()
        if isinstance(error, (HttpResponseError, KeyError)):
            raise HTTPException(status_code=503, detail="User settings are unavailable.") from error
        raise
    return LanguageSettings(
        native_language=entity.get("NativeLanguage", "English"),
        learning_language=entity.get("LearningLanguage", "Dutch"),
    )


def save_language_settings(user_id: str, settings: LanguageSettings) -> None:
    try:
        get_table_service_client().get_table_client("Users").upsert_entity(
            {
                "PartitionKey": "google",
                "RowKey": user_id,
                "NativeLanguage": settings.native_language,
                "LearningLanguage": settings.learning_language,
            }
        )
    except (HttpResponseError, KeyError) as error:
        raise HTTPException(status_code=503, detail="User settings are unavailable.") from error


def store_message(
    user_id: str,
    text: str,
    role: Literal["user", "assistant"] = "user",
    feedback: list[FeedbackAnnotation] | None = None,
) -> None:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    get_table_service_client().get_table_client("Messages").create_entity(
        {
            "PartitionKey": user_id,
            "RowKey": f"{timestamp}_{uuid4().hex}",
            "Role": role,
            "Text": text,
            **({"Feedback": json.dumps([item.model_dump() for item in feedback])} if feedback else {}),
        }
    )


def register_user_and_message(
    user_id: str,
    text: str | None = None,
    role: Literal["user", "assistant"] = "user",
    feedback: list[FeedbackAnnotation] | None = None,
) -> None:
    try:
        store_user(user_id)
        if text is not None:
            store_message(user_id, text, role, feedback)
    except (HttpResponseError, KeyError) as error:
        raise HTTPException(status_code=503, detail="Message storage is unavailable.") from error


app = FastAPI(title="Language Learning AI API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "https://yellow-coast-0325af203.7.azurestaticapps.net",
        "https://yellow-coast-0325af203-dev.westeurope.7.azurestaticapps.net",
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

    messages = []
    for entity in sorted(entities, key=lambda item: item["RowKey"])[-100:]:
        feedback = []
        if entity.get("Feedback"):
            try:
                feedback = [FeedbackAnnotation.model_validate(item) for item in json.loads(entity["Feedback"])]
            except (TypeError, ValueError):
                feedback = []
        messages.append(
            StoredMessage(
                id=entity["RowKey"],
                role=entity.get("Role", "user"),
                text=entity["Text"],
                created_at=entity["RowKey"].split("_", 1)[0],
                feedback=feedback,
            )
        )
    return messages


def parse_generation(output: str, message_text: str) -> tuple[str, list[FeedbackAnnotation]]:
    """Parse structured teacher feedback, falling back safely for plain model output."""
    try:
        candidate = output.strip()
        if candidate.startswith("```"):
            candidate = candidate.strip("`").removeprefix("json").strip()
        payload = json.loads(candidate)
        response = payload.get("response") or payload.get("reply")
        raw_feedback = payload.get("feedback", [])
        if not isinstance(response, str) or not isinstance(raw_feedback, list):
            raise ValueError("Invalid generation payload")
        feedback = []
        for item in raw_feedback:
            annotation = FeedbackAnnotation.model_validate(item)
            if annotation.end <= len(message_text) and annotation.start < annotation.end:
                feedback.append(annotation)
        return response, sorted(feedback, key=lambda item: (item.start, item.end))
    except (TypeError, ValueError, json.JSONDecodeError):
        return output, []


@app.get("/settings", response_model=LanguageSettings)
def read_settings(user_id: Annotated[str, Depends(get_current_user)]) -> LanguageSettings:
    register_user_and_message(user_id)
    return get_language_settings(user_id)


@app.put("/settings", response_model=LanguageSettings)
def update_settings(
    settings: LanguageSettings,
    user_id: Annotated[str, Depends(get_current_user)],
) -> LanguageSettings:
    save_language_settings(user_id, settings)
    return settings


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
    register_user_and_message(user_id, message_text, "user")
    settings = get_language_settings(user_id)

    try:
        result = get_openai_client().responses.create(
            model=deployment,
            instructions=(
                "You are a helpful language-learning assistant. Use the supplied "
                "context to give a clear and concise response. Return ONLY valid JSON "
                "with keys `response` (your answer) and `feedback` (an array of objects "
                "with integer `start`, integer `end`, and `comment`). The feedback "
                "spans must identify only genuine mistakes in the user's message, using "
                "character offsets. If there are no mistakes, return an empty feedback "
                "array. Ignore words or passages written in languages other than the "
                "learning language. Write feedback comments in the user's native language. "
                "The user's native "
                f"language is {settings.native_language}; respond in {settings.learning_language} "
                "unless the user explicitly asks for another language."
            ),
            input=json.dumps(request.context, ensure_ascii=False),
            max_output_tokens=1000,
        )
    except OpenAIError as error:
        raise HTTPException(
            status_code=502,
            detail="Azure OpenAI could not generate a response.",
        ) from error

    response_text, feedback = parse_generation(result.output_text, message_text)
    register_user_and_message(user_id, response_text, "assistant", feedback)
    return GenerateResponse(response=response_text, feedback=feedback)
