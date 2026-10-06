# Azure infrastructure lifecycle

`main.bicep` provisions the resource shells used by the application: App
Service plans and apps, Function Apps, Storage Tables, Service Bus, Azure OpenAI,
Azure AI Search, and Static Web Apps. `provision.sh` creates or updates them.

Use `backup.sh` before deleting a resource group:

```bash
bash infra/backup.sh backups/before-delete
```

The backup includes an ARM export, resource inventory, App Service settings, and
the `Users` and `Messages` tables. Treat it as sensitive because settings may
contain secrets and the tables contain user data. Azure AI Search documents are
rebuildable web knowledge; retain their source URLs or reindex them after
recreation. Model deployments, OAuth credentials, GitHub secrets, VAPID keys,
and API keys must be restored separately.

Recreate the resource group with:

```bash
RESOURCE_GROUP=language-learning-ai-rg bash infra/provision.sh
```

After provisioning, restore app settings and secrets, deploy the application,
create Azure OpenAI model deployments, grant managed-identity roles, configure
GitHub Actions variables, and reindex web knowledge. The exported ARM template
is a snapshot and should be reviewed; it is not a substitute for the maintained
Bicep template.
