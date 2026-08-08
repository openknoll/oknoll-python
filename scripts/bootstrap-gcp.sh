#!/usr/bin/env bash
# bootstrap-gcp.sh — one-time GCP prerequisites (implementation plan §5.4, design §16.2).
# Creates the dev/prod projects and versioned Terraform state buckets.
# Run manually, once, by a human with org/billing permissions. Everything else is Terraform.
set -euo pipefail

: "${BILLING_ACCOUNT:?Set BILLING_ACCOUNT to your GCP billing account ID (e.g. 000000-AAAAAA-000000)}"

for ENV in dev prod; do
  PROJECT="oknoll-${ENV}"
  echo "==> ${PROJECT}"
  gcloud projects create "${PROJECT}" || echo "project ${PROJECT} already exists"
  gcloud billing projects link "${PROJECT}" --billing-account="${BILLING_ACCOUNT}"
  gcloud storage buckets create "gs://${PROJECT}-tfstate" \
    --project="${PROJECT}" --location=us-central1 \
    --uniform-bucket-level-access || echo "bucket ${PROJECT}-tfstate already exists"
  gcloud storage buckets update "gs://${PROJECT}-tfstate" --versioning
done

# Protect prod state against accidental deletion.
gcloud storage buckets update gs://oknoll-prod-tfstate --retention-period=7d || true

echo "Done. Leave GCP alone until Phase 7 (Terraform envs/dev)."
