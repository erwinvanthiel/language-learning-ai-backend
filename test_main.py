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
        "get_language_settings",
        lambda user_id: main.LanguageSettings(
            native_language="English", learning_language="German"
        ),
    )
    monkeypatch.setattr(
        main,
        "register_user_and_message",
        lambda user_id, text=None, role="user": stored_messages.append(
            (user_id, text, role)
        ),
    )

    response = TestClient(main.app).post(
        "/generate",
        json={"context": {"text": "Help me practise German", "level": "A2"}},
    )

    assert response.status_code == 200
    assert response.json() == {"response": "Hallo!"}
    assert fake_client.responses.request["model"] == "test-deployment"
    assert "respond in German" in fake_client.responses.request["instructions"]
    assert json.loads(fake_client.responses.request["input"]) == {
        "text": "Help me practise German",
        "level": "A2",
    }
    assert stored_messages == [
        ("google-user-123", "Help me practise German", "user"),
        ("google-user-123", "Hallo!", "assistant"),
    ]


def test_read_and_update_language_settings(monkeypatch) -> None:
    entities = {}

    class FakeUsersTable:
        def get_entity(self, partition_key, row_key):
            assert partition_key == "google"
            if row_key not in entities:
                error = RuntimeError("not found")
                error.status_code = 404
                raise error
            return entities[row_key]

        def upsert_entity(self, entity):
            entities[entity["RowKey"]] = entity

    class FakeTableService:
        def get_table_client(self, table_name):
            assert table_name == "Users"
            return FakeUsersTable()

    monkeypatch.setattr(main, "get_table_service_client", lambda: FakeTableService())
    client = TestClient(main.app)

    response = client.get("/settings")
    assert response.status_code == 200
    assert response.json() == {"native_language": "English", "learning_language": "Dutch"}

    response = client.put(
        "/settings",
        json={"native_language": "English", "learning_language": "German"},
    )
    assert response.status_code == 200
    assert response.json()["learning_language"] == "German"
    assert client.get("/settings").json()["learning_language"] == "German"


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
                    "Role": "user",
                },
                {
                    "PartitionKey": "google-user-123",
                    "RowKey": "20260825T120001.000000Z_second",
                    "Text": "Good morning!",
                    "Role": "assistant",
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
            "role": "user",
            "text": "Goedemorgen",
            "created_at": "20260825T120000.000000Z",
        },
        {
            "id": "20260825T120001.000000Z_second",
            "role": "assistant",
            "text": "Good morning!",
            "created_at": "20260825T120001.000000Z",
        }
    ]
