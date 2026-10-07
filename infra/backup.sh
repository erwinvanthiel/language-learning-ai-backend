#!/usr/bin/env bash
set -euo pipefail

: "${RESOURCE_GROUP:=language-learning-ai-rg}"
: "${STORAGE_ACCOUNT:=languageaistorageevth}"
BACKUP_DIR="${1:-backups/$(date -u +%Y%m%dT%H%M%SZ)}"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

# Resource export is a starting point, not a guaranteed redeployment template.
az group export --resource-group "$RESOURCE_GROUP" \
  --include-parameter-default-value > "$BACKUP_DIR/resource-template.json" || true

az resource list --resource-group "$RESOURCE_GROUP" -o json > "$BACKUP_DIR/resources.json"
for app in $(az webapp list --resource-group "$RESOURCE_GROUP" --query '[].name' -o tsv); do
  az webapp config appsettings list --resource-group "$RESOURCE_GROUP" --name "$app" \
    -o json > "$BACKUP_DIR/appsettings-$app.json"
done

# Requires Storage Table Data Reader/Contributor permission. These exports retain
# user data and should be encrypted or moved to private storage after creation.
for table in Users Messages; do
  az storage entity query --account-name "$STORAGE_ACCOUNT" --table-name "$table" \
    --auth-mode login -o json > "$BACKUP_DIR/table-$table.json" || {
      echo "Could not export $table; grant Storage Table Data Reader and retry." >&2
    }
done

echo "Backup written to $BACKUP_DIR. Protect it: it contains application settings and user data."
