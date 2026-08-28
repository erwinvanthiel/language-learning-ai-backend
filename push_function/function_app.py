import json
import logging
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import azure.functions as func
from azure.data.tables import TableServiceClient, UpdateMode
from azure.identity import DefaultAzureCredential
from azure.servicebus import ServiceBusClient, ServiceBusMessage
from pywebpush import WebPushException, webpush


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
                payload = {"user_id": user["RowKey"], "subscription": json.loads(subscription), "body": REMINDER}
                message_time = now.strftime("%Y%m%dT%H%M%S.%fZ")
                service.get_table_client("Messages").create_entity(
                    {
                        "PartitionKey": user["RowKey"],
                        "RowKey": f"{message_time}_{uuid4().hex}",
                        "Role": "assistant",
                        "Text": REMINDER,
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
