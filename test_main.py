import json

from fastapi.testclient import TestClient

import main


class FakeResponses:
    def __init__(self) -> None:
        self.request = None

    def create(self, **kwargs):
        self.request = kwargs
        return type("Response", (), {"output_text": "Hallo!"})()


class FakeOpenAIClient:
    def __init__(self) -> None:
        self.responses = FakeResponses()


def test_read_root() -> None:
    response = TestClient(main.app).get("/")

    assert response.status_code == 200
    assert response.json() == {"message": "Hello, world!"}


def test_generate_relays_context_and_returns_response(monkeypatch) -> None:
    fake_client = FakeOpenAIClient()
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "test-deployment")
    monkeypatch.setattr(main, "get_openai_client", lambda: fake_client)

    response = TestClient(main.app).post(
        "/generate",
        json={"context": {"text": "Help me practise German", "level": "A2"}},
    )

    assert response.status_code == 200
    assert response.json() == {"response": "Hallo!"}
    assert fake_client.responses.request["model"] == "test-deployment"
    assert json.loads(fake_client.responses.request["input"]) == {
        "text": "Help me practise German",
        "level": "A2",
    }


def test_generate_requires_azure_openai_configuration(monkeypatch) -> None:
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_DEPLOYMENT", raising=False)

    response = TestClient(main.app).post(
        "/generate",
        json={"context": {"text": "Hello"}},
    )

    assert response.status_code == 503
