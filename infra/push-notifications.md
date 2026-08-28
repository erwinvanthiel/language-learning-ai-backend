# Push reminder infrastructure

The reminder function requires an Azure Function App, a Service Bus queue, and
VAPID keys. These resources are intentionally not created by CI. Create them
once per environment, grant the Function App managed identity `Azure Service
Bus Data Sender` and `Azure Service Bus Data Receiver`, and configure these
settings on the Function App:

`AZURE_TABLE_ENDPOINT`, `SERVICE_BUS_NAMESPACE`, `SERVICE_BUS_QUEUE`,
`SERVICE_BUS_CONNECTION`, `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, and
`VAPID_SUBJECT` (for example `mailto:admin@example.com`). For interest-based
article reminders, also configure `BING_SEARCH_ENDPOINT` (optional),
`BING_SEARCH_KEY`, `AZURE_OPENAI_ENDPOINT`, and `AZURE_OPENAI_DEPLOYMENT`.

Configure `VAPID_PUBLIC_KEY` on each FastAPI App Service as well. Generate a
key pair with a Web Push/VAPID key generator and store the private key only in
Azure application settings or GitHub environment secrets.

The timer runs every ten minutes. It enqueues one standard reminder for users
whose most recent user message is at least ten minutes old. A user is skipped
until they send a new message after their previous standard reminder.
