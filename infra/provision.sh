#!/usr/bin/env bash
set -euo pipefail

: "${RESOURCE_GROUP:=language-learning-ai-rg}"
: "${LOCATION:=westeurope}"
: "${PARAMETERS_FILE:=infra/parameters.example.json}"

az group create --name "$RESOURCE_GROUP" --location "$LOCATION" >/dev/null
az deployment group create \
  --resource-group "$RESOURCE_GROUP" \
  --template-file infra/main.bicep \
  --parameters "@$PARAMETERS_FILE" \
  --parameters location="$LOCATION"

echo "Resources created. Configure model deployments, app settings, identities, and GitHub secrets before deploying application code."
