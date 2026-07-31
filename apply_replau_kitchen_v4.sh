#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

sudo -u postgres psql -v ON_ERROR_STOP=1 -d localapi \
  < "$repo_dir/replau_kitchen_ui/add_kitchen_v4.sql"

systemctl --user restart postgrest-localapi.service
systemctl --user restart replau-kitchen.service

for _ in {1..20}; do
  if curl --fail --silent --show-error http://127.0.0.1:8791/health >/dev/null; then
    break
  fi
  sleep 1
done

curl --fail --silent --show-error http://127.0.0.1:8791/health
printf '\nKITCHEN_V4_OK: open https://memopc.tail52e16e.ts.net/kitchen/\n'
