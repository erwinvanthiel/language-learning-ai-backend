import json
import os
from functools import lru_cache
from typing import Any

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI, OpenAIError
from pydantic import BaseModel, Field


class GenerateRequest(BaseModel):
    context: dict[str, Any] = Field(description="Context forwarded to Azure OpenAI")


class GenerateResponse(BaseModel):
    response: str


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


@app.post("/generate", response_model=GenerateResponse)
def generate(request: GenerateRequest) -> GenerateResponse:
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
    if not deployment or not os.getenv("AZURE_OPENAI_ENDPOINT"):
        raise HTTPException(status_code=503, detail="Azure OpenAI is not configured.")

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
