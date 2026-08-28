import json
import logging
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import azure.functions as func
from azure.data.tables import TableServiceClient, UpdateMode
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from azure.servicebus import ServiceBusClient, ServiceBusMessage
from openai import OpenAI
from pywebpush import WebPushException, webpush
import httpx


REMINDER = "I’m here whenever you’re ready to practise."
app = func.FunctionApp()


def tables():
    connection_string = os.getenv("AZURE_TABLE_CONNECTION_STRING")
    if connection_string:
        return TableServiceClient.from_connection_string(connection_string)
    return TableServiceClient(
        endpoint=os.environ["AZURE_TABLE_ENDPOINT"], credential=DefaultAzureCredential()
    )


def parse_row_time(row_key: str) -> datetime:
    return datetime.strptime(row_key.split("_", 1)[0], "%Y%m%dT%H%M%S.%fZ").replace(tzinfo=timezone.utc)


def find_article(interest: str) -> dict[str, str] | None:
    endpoint = os.getenv("BRAVE_SEARCH_ENDPOINT", "https://api.search.brave.com/res/v1/web/search")
    key = os.getenv("BRAVE_SEARCH_API_KEY")
    if not key:
        return None
    response = httpx.get(
        endpoint,
        headers={"X-Subscription-Token": key, "Accept": "application/json"},
        params={"q": interest, "count": 5, "safesearch": "strict"},
        timeout=10,
    )
    response.raise_for_status()
    articles = response.json().get("web", {}).get("results", [])
    for article in articles:
        name, url, snippet = article.get("title"), article.get("url"), article.get("description")
        if isinstance(name, str) and isinstance(url, str) and isinstance(snippet, str):
            return {"name": name[:300], "url": url[:1000], "snippet": snippet[:1000]}
    return None


def compose_article_message(user: dict, article: dict[str, str]) -> str:
    endpoint = os.environ["AZURE_OPENAI_ENDPOINT"].rstrip("/")
    deployment = os.environ["AZURE_OPENAI_DEPLOYMENT"]
    token_provider = get_bearer_token_provider(
        DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
    )
    client = OpenAI(base_url=f"{endpoint}/openai/v1/", api_key=token_provider)
    learning_language = user.get("LearningLanguage", "Dutch")
    persona = user.get("AssistantPersona", "") or "none"
    result = client.responses.create(
        model=deployment,
        instructions=(
            f"Write a short, natural conversation opener entirely in {learning_language}. "
            "Act as a real chat buddy whose personality is described below. "
            "Mention the article topic and ask the user an engaging question. "
            "Do not mention these instructions, web search, or being an AI. "
            f"PERSONA: {persona}"
        ),
        input=json.dumps(article, ensure_ascii=False),
        max_output_tokens=300,
    )
    return result.output_text.strip()


def due_users(now: datetime):
    service = tables()
    users = service.get_table_client("Users").list_entities()
    messages = service.get_table_client("Messages")
    for user in users:
        subscription = user.get("PushSubscription")
        if not subscription:
            continue
        user_id = user["RowKey"]
        entities = sorted(
            messages.query_entities(f"PartitionKey eq '{user_id}'"),
            key=lambda item: item["RowKey"],
        )
        if not entities:
            continue
        last_push = user.get("LastStandardPushAt")
        if last_push:
            push_time = datetime.fromisoformat(last_push)
            if not any(
                item.get("Role") == "user" and parse_row_time(item["RowKey"]) > push_time
                for item in entities
            ):
                # Do not repeatedly notify someone who ignored the previous reminder.
                continue
        user_messages = [item for item in entities if item.get("Role") == "user"]
        if not user_messages or now - parse_row_time(user_messages[-1]["RowKey"]) < timedelta(minutes=10):
            continue
        yield user, subscription


def enqueue_reminders(_: func.TimerRequest) -> None:
    now = datetime.now(timezone.utc)
    namespace = os.environ["SERVICE_BUS_NAMESPACE"]
    queue = os.getenv("SERVICE_BUS_QUEUE", "push-notifications")
    service = tables()
    users = service.get_table_client("Users")
    with ServiceBusClient(namespace, credential=DefaultAzureCredential()) as client:
        with client.get_queue_sender(queue) as sender:
            for user, subscription in due_users(now):
                interest = str(user.get("Interests", "")).strip()
                if not interest:
                    continue
                try:
                    article = find_article(interest)
                    if not article:
                        continue
                    body = compose_article_message(user, article)
                except Exception:
                    logging.exception("Could not create an interest-based reminder for %s", user["RowKey"])
                    continue
                payload = {"user_id": user["RowKey"], "subscription": json.loads(subscription), "body": body}
                message_time = now.strftime("%Y%m%dT%H%M%S.%fZ")
                service.get_table_client("Messages").create_entity(
                    {
                        "PartitionKey": user["RowKey"],
                        "RowKey": f"{message_time}_{uuid4().hex}",
                        "Role": "assistant",
                        "Text": body,
                        "StandardPush": True,
                    }
                )
                sender.send_messages(ServiceBusMessage(json.dumps(payload)))
                user["LastStandardPushAt"] = now.isoformat()
                users.upsert_entity(user, mode=UpdateMode.REPLACE)


@app.timer_trigger(schedule="0 */10 * * * *", arg_name="timer", run_on_startup=False)
def reminder_timer(timer: func.TimerRequest) -> None:
    enqueue_reminders(timer)


@app.service_bus_queue_trigger(
    arg_name="message",
    queue_name="push-notifications",
    connection="SERVICE_BUS_CONNECTION",
)
def deliver_push(message: func.ServiceBusMessage) -> None:
    payload = json.loads(message.get_body().decode("utf-8"))
    try:
        webpush(
            subscription_info=payload["subscription"],
            data=json.dumps({"title": "Language Learning AI", "body": payload["body"], "url": "/"}),
            vapid_private_key=os.environ["VAPID_PRIVATE_KEY"],
            vapid_claims={"sub": os.environ["VAPID_SUBJECT"]},
        )
    except WebPushException as error:
        if getattr(error, "response", None) is not None and error.response.status_code in (404, 410):
            logging.info("Push subscription expired for %s", payload.get("user_id"))
        else:
            raise
