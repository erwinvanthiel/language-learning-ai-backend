# Language Learning AI Backend

Minimal FastAPI backend for the language-learning assistant.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload
```

Open <http://127.0.0.1:8000/> to receive:

```json
{"message": "Hello, world!"}
```

Interactive API documentation is available at <http://127.0.0.1:8000/docs>.

## Authentication and message storage

`GET /messages` and `POST /generate` require a Google ID token in the
`Authorization: Bearer <token>` header. The API validates the token signature,
expiry, and audience against `GOOGLE_CLIENT_ID` and uses Google's stable `sub`
claim as the user ID. It does not store names, email addresses, or OAuth tokens.

The API registers verified user IDs in the `Users` Azure Table and stores their
prompts in the `Messages` table. Configure `AZURE_TABLE_ENDPOINT` and grant the
App Service managed identity `Storage Table Data Contributor` on the storage
account. `GET /messages` returns up to the authenticated user's 100 most recent
prompts.

## Generate a language-learning response

`POST /generate` accepts a JSON context dictionary, stores its message for the
authenticated user, and forwards it to the configured Azure OpenAI model deployment:

```json
{
  "context": {
    "text": "Help me practise ordering coffee in Dutch"
  }
}
```

The response is returned as `{"response": "..."}`. The server uses Microsoft Entra
authentication through `DefaultAzureCredential`; configure `AZURE_OPENAI_ENDPOINT`
and `AZURE_OPENAI_DEPLOYMENT` in the environment. In Azure, give the App Service's
managed identity the `Cognitive Services OpenAI User` role on the Azure OpenAI
resource.

## Continuous deployment to Azure App Service

Pull requests targeting `dev` or `main` run a compile check and start the API for
a smoke test. A merge to either branch repeats those checks and packages the
application and its Python dependencies. The `dev` branch deploys to
`language-learning-ai-api-dev-evth`; `main` deploys to the production App Service.

The deployment uses GitHub's OpenID Connect integration, so it does not require a
long-lived Azure publish profile. Before merging the deployment workflow, create:

- GitHub environments named `staging` and `production`.
- A repository variable named `AZURE_WEBAPP_NAME` containing the App Service name.
- A repository variable named `AZURE_DEV_WEBAPP_NAME` containing the staging Web
  App name.
- Repository or environment secrets named `AZURE_CLIENT_ID`,
  `AZURE_TENANT_ID`, and `AZURE_SUBSCRIPTION_ID`.
- A federated credential in Microsoft Entra ID with this subject:
  `repo:<github-owner>/<github-repository>:environment:production`.
- A second federated credential with the subject:
  `repo:<github-owner>/<github-repository>:environment:staging`.
- A role assignment granting that identity `Website Contributor` on the target
  App Service.

The target must be a Linux App Service configured for Python 3.12. Optionally add
required reviewers to the `production` environment to put an approval gate before
deployment. See [Microsoft's App Service deployment documentation](https://learn.microsoft.com/azure/app-service/deploy-github-actions)
for the Azure-side OIDC setup.

## Agentic response flow

The response pipeline is intentionally split into bounded stages. `POST /generate`
first loads the user's persisted conversation/article references and queries the
Azure AI Search knowledge base with hybrid keyword + vector retrieval. Retrieved
chunks are deduplicated and capped by the evidence budget. A planning step then
decides whether that evidence is sufficient. For factual or current questions it
can invoke the registered `internet_search` skill (Brave), which returns at most
one candidate page. The page is fetched, cleaned of markup, normalized, chunked,
embedded with the configured Azure OpenAI embedding deployment, and indexed with
its URL and provenance. The request performs a fresh retrieval so the response
agent receives the newly indexed evidence.

The conversational stage runs through LangChain Deep Agents with the Azure OpenAI
chat model and the web-search skill. Its draft is evaluated against a response
quality check and revised at most once. Language corrections are a separate pass:
they annotate only genuine mistakes in the learning language and do not change the
natural response. User and assistant messages are persisted in Azure Table Storage
and indexed best-effort in the same AI Search index, so a Search outage does not
prevent normal chat storage.

Autonomous reminders follow a similar flow in the timer-triggered Function App:
the function selects an eligible user, searches Brave for one interest-related
article, asks Azure OpenAI to start a conversation about it, stores the resulting
assistant message and source URL, indexes that message in AI Search, and queues the
push notification through Azure Service Bus. Because the push text and source are
indexed, later conversations can retrieve and discuss what was previously sent.

### RAG configuration

Set `AZURE_SEARCH_ENDPOINT`, `AZURE_SEARCH_INDEX_NAME`, and
`AZURE_OPENAI_EMBEDDING_DEPLOYMENT` (plus the optional
`AZURE_OPENAI_EMBEDDING_DIMENSIONS`, `RAG_TOP_K`, `RAG_EVIDENCE_BUDGET_CHARS`, and
`RAG_FRESHNESS_DAYS`). The App Service and Function App managed identities need
`Search Index Data Contributor` on the search service. The index is created or
updated lazily on the first request that uses the RAG layer.
