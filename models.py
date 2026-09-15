"""Request, response, and persistence models used by the API."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class GenerateRequest(BaseModel):
    context: dict[str, Any] = Field(description="Context forwarded to Azure OpenAI")


class TranslateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class LanguageSettings(BaseModel):
    native_language: str = Field(default="English", min_length=1, max_length=80)
    learning_language: str = Field(default="Dutch", min_length=1, max_length=80)
    # Returned to the UI; the sanitized value is internal-only.
    assistant_persona: str = Field(default="", max_length=500)
    interests: str = Field(default="", max_length=500)
    sanitized_persona: str = Field(default="", max_length=500, exclude=True)


class FeedbackAnnotation(BaseModel):
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    comment: str = Field(min_length=1, max_length=500)


class GenerateResponse(BaseModel):
    response: str
    feedback: list[FeedbackAnnotation] = Field(default_factory=list)


class TranslateResponse(BaseModel):
    translation: str


class PushSubscription(BaseModel):
    endpoint: str = Field(min_length=1, max_length=2000)
    keys: dict[str, str]


class StoredMessage(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    text: str
    created_at: str
    feedback: list[FeedbackAnnotation] = Field(default_factory=list)


class ResponseDraft(BaseModel):
    response: str = Field(min_length=1)
