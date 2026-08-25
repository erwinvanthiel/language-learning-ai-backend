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

## Continuous deployment to Azure App Service

Pull requests targeting `main` run a compile check and start the API for a smoke
test. A merge to `main` repeats those checks, packages the application and its
Python dependencies, and deploys it to a Linux Azure App Service.

The deployment uses GitHub's OpenID Connect integration, so it does not require a
long-lived Azure publish profile. Before merging the deployment workflow, create:

- A GitHub environment named `production`.
- A repository variable named `AZURE_WEBAPP_NAME` containing the App Service name.
- Repository or `production` environment secrets named `AZURE_CLIENT_ID`,
  `AZURE_TENANT_ID`, and `AZURE_SUBSCRIPTION_ID`.
- A federated credential in Microsoft Entra ID with this subject:
  `repo:<github-owner>/<github-repository>:environment:production`.
- A role assignment granting that identity `Website Contributor` on the target
  App Service.

The target must be a Linux App Service configured for Python 3.12. Optionally add
required reviewers to the `production` environment to put an approval gate before
deployment. See [Microsoft's App Service deployment documentation](https://learn.microsoft.com/azure/app-service/deploy-github-actions)
for the Azure-side OIDC setup.
