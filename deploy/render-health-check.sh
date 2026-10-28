#!/bin/sh
set -eu

: "${QA_HEALTH_CHECK_URL:?Set QA_HEALTH_CHECK_URL to the deployed backend health URL}"
status=$(curl --fail --silent --show-error --location --max-time 90 \
    --output /dev/null --write-out '%{http_code}' "$QA_HEALTH_CHECK_URL")
case "$status" in
    2??) ;;
    *)
        printf 'Health check returned HTTP %s\n' "$status" >&2
        exit 1
        ;;
esac
