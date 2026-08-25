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
