#!/bin/bash
# Shared helpers for the guest startup scripts.
# The database password is fetched at run time. It is not written here.

set -euo pipefail

report_failed() {
  local line="$1"
  curl -sf -X PUT -H "Metadata-Flavor: Google" \
    --data "FAILED at line ${line}" \
    "http://metadata.google.internal/computeMetadata/v1/instance/guest-attributes/twotier/ready" \
    || true
}

trap 'report_failed "${BASH_LINENO[0]}"' ERR

report_ready() {
  local value="$1"
  curl -sf -X PUT -H "Metadata-Flavor: Google" \
    --data "${value}" \
    "http://metadata.google.internal/computeMetadata/v1/instance/guest-attributes/twotier/ready"
}

fetch_secret() {
  local token payload
  token="$(curl -sf -H "Metadata-Flavor: Google" \
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')"
  payload="$(curl -sf -H "Authorization: Bearer ${token}" \
    "https://secretmanager.googleapis.com/v1/projects/__PROJECT_ID__/secrets/__SECRET_NAME__/versions/latest:access")"
  python3 -c 'import json,sys,base64; print(base64.b64decode(json.load(sys.stdin)["payload"]["data"]).decode(), end="")' \
    <<<"${payload}"
}
