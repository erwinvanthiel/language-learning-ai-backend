import json

from fastapi.testclient import TestClient
import pytest

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


@pytest.fixture(autouse=True)
def authenticated_user():
    main.app.dependency_overrides[main.get_current_user] = lambda: "google-user-123"
    yield
    main.app.dependency_overrides.clear()


def test_read_root() -> None:
    response = TestClient(main.app).get("/")

    assert response.status_code == 200
    assert response.json() == {"message": "Hello, world!"}


def test_generate_relays_context_and_returns_response(monkeypatch) -> None:
    fake_client = FakeOpenAIClient()
    stored_messages = []
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "test-deployment")
    monkeypatch.setattr(main, "get_openai_client", lambda: fake_client)
    monkeypatch.setattr(
        main,
        "register_user_and_message",
        lambda user_id, text=None: stored_messages.append((user_id, text)),
    )

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
    assert stored_messages == [("google-user-123", "Help me practise German")]


def test_generate_requires_azure_openai_configuration(monkeypatch) -> None:
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_DEPLOYMENT", raising=False)

    response = TestClient(main.app).post(
        "/generate",
        json={"context": {"text": "Hello"}},
    )

    assert response.status_code == 503


def test_generate_requires_authentication(monkeypatch) -> None:
    main.app.dependency_overrides.clear()
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "google-client-id")

    response = TestClient(main.app).post(
        "/generate",
        json={"context": {"text": "Hello"}},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required."}


def test_get_current_user_verifies_google_token(monkeypatch) -> None:
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "google-client-id")
    monkeypatch.setattr(
        main.id_token,
        "verify_oauth2_token",
        lambda token, request, audience: {
            "sub": "verified-google-user",
            "aud": audience,
        },
    )

    assert main.get_current_user("Bearer signed-token") == "verified-google-user"


def test_read_messages_returns_only_authenticated_users_messages(monkeypatch) -> None:
    class FakeMessagesTable:
        def query_entities(self, query):
            assert query == "PartitionKey eq 'google-user-123'"
            return [
                {
                    "PartitionKey": "google-user-123",
                    "RowKey": "20260825T120000.000000Z_first",
                    "Text": "Goedemorgen",
                }
            ]

    class FakeTableService:
        def get_table_client(self, table_name):
            assert table_name == "Messages"
            return FakeMessagesTable()

    monkeypatch.setattr(main, "register_user_and_message", lambda *args: None)
    monkeypatch.setattr(main, "get_table_service_client", lambda: FakeTableService())

    response = TestClient(main.app).get("/messages")

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": "20260825T120000.000000Z_first",
            "text": "Goedemorgen",
            "created_at": "20260825T120000.000000Z",
        }
    ]
