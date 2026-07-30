#!/usr/bin/env bash
set -euo pipefail

DRIVER_ENV_FILE="${DRIVER_ENV_FILE:-/home/guill/.config/replau/driver.env}"
if [[ -f "${DRIVER_ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${DRIVER_ENV_FILE}"
  set +a
fi

APP_BASE_URL="${APP_BASE_URL:-http://127.0.0.1:8797}"
POSTGREST_BASE_URL="${POSTGREST_BASE_URL:-http://127.0.0.1:3000}"
DELIVERY_UI_URL="${DELIVERY_UI_URL:-http://127.0.0.1:8790/ops/delivery}"
DRIVER_AUTH_USERNAME="${DRIVER_AUTH_USERNAME:-driver}"
DRIVER_AUTH_PASSWORD="${DRIVER_AUTH_PASSWORD:-}"
APP_AUTH=()
if [[ -n "${DRIVER_AUTH_PASSWORD}" ]]; then
  APP_AUTH=(-u "${DRIVER_AUTH_USERNAME}:${DRIVER_AUTH_PASSWORD}")
fi

DRIVER_PYTHON="${DRIVER_PYTHON:-/home/guill/.local/share/replau/services/product-venv/bin/python}"
PICKUP_CODE="${PICKUP_CODE:-TEST_SURCO}"
PICKUP_NAME="${PICKUP_NAME:-Test Pickup Surco}"
PICKUP_ADDRESS="${PICKUP_ADDRESS:-Smoke test pickup, Surco}"
PICKUP_LATITUDE="${PICKUP_LATITUDE:--12.11110000}"
PICKUP_LONGITUDE="${PICKUP_LONGITUDE:--77.03000000}"
PICKUP_RADIUS_KM="${PICKUP_RADIUS_KM:-8.05}"
DRIVER_LATITUDE="${DRIVER_LATITUDE:--12.11020000}"
DRIVER_LONGITUDE="${DRIVER_LONGITUDE:--77.02900000}"
OFFER_RADIUS_KM="${OFFER_RADIUS_KM:-8.05}"
MAX_CANDIDATES="${MAX_CANDIDATES:-5}"
TEST_RUN_ID="${TEST_RUN_ID:-$(date -u +%Y%m%d%H%M%S)-$$}"
TEST_CUSTOMER_NUMBER="SMOKE-${TEST_RUN_ID}"
TEST_ORDER_NUMBER="SMOKE-${TEST_RUN_ID}"
TEST_DRIVER_PHONE="9$(date -u +%y%m%d%H%M%S)"
TEST_DRIVER_CODE="SMK${TEST_RUN_ID//[^0-9]/}"

driver_account_id=""
repartidor_id=""
pickup_id=""
pedido_id=""
pedido_num=""
session_id=""
batch_id=""
candidate_id=""
assignment_id=""
original_driver_status=""
original_repartidor_activo=""
original_pickup_json=""
original_mapping_json=""
test_customer_id=""
test_fulfillment_id=""
test_order_created=false
outbox_worker_was_active=false
session_cookie=""

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 2
  fi
}

urlencode() {
  jq -rn --arg v "$1" '$v|@uri'
}

pg_get() {
  curl -fsS "${POSTGREST_BASE_URL}$1"
}

pg_patch() {
  curl -fsS -X PATCH "${POSTGREST_BASE_URL}$1" \
    -H "Content-Type: application/json" \
    -H "Prefer: return=representation" \
    -d "$2"
}

pg_post() {
  curl -fsS -X POST "${POSTGREST_BASE_URL}$1" \
    -H "Content-Type: application/json" \
    -H "Prefer: return=representation" \
    -d "$2"
}

pg_delete() {
  curl -fsS -X DELETE "${POSTGREST_BASE_URL}$1" >/dev/null
}

app_post_form() {
  local path="$1"
  shift
  curl -fsS "${APP_AUTH[@]}" -H "Cookie: replau_driver_session=${session_cookie}" \
    -X POST "${APP_BASE_URL}${path}" "$@"
}

first_row() {
  jq 'if type == "array" then .[0] // empty else . end'
}

json_bool() {
  case "$1" in
    true|t|1) printf 'true' ;;
    *) printf 'false' ;;
  esac
}

cleanup() {
  local rc=$?
  local cleanup_failed=false
  set +e

  if [[ -n "${assignment_id}" ]]; then
    pg_patch "/delivery_asignaciones?id=eq.${assignment_id}" '{"status":"CANCELLED","notes":"Cancelled by synthetic driver dispatch smoke test cleanup"}' >/dev/null || cleanup_failed=true
  fi
  if [[ -n "${candidate_id}" ]]; then
    pg_patch "/delivery_offer_candidates?id=eq.${candidate_id}" '{"status":"CANCELLED"}' >/dev/null || cleanup_failed=true
  fi
  if [[ -n "${batch_id}" ]]; then
    pg_patch "/delivery_offer_batches?id=eq.${batch_id}" '{"status":"CANCELLED"}' >/dev/null || cleanup_failed=true
  fi
  if [[ -n "${session_id}" && -n "${driver_account_id}" ]]; then
    app_post_form "/api/driver/${driver_account_id}/offline" >/dev/null || cleanup_failed=true
  fi
  if [[ -n "${pedido_id}" && "${test_order_created}" == true ]]; then
    pg_delete "/whatsapp_outbox?pedido_id=eq.${pedido_id}" || cleanup_failed=true
    pg_delete "/delivery_asignaciones?pedido_id=eq.${pedido_id}" || cleanup_failed=true
    pg_delete "/order_pickup_points?pedido_id=eq.${pedido_id}" || cleanup_failed=true
    pg_delete "/delivery_offer_batches?pedido_id=eq.${pedido_id}" || cleanup_failed=true
    fulfillment_event_count="$(pg_get "/payment_fulfillment_events?pedido_id=eq.${pedido_id}&select=id" | jq 'length')"
    if [[ "${fulfillment_event_count}" != "0" ]]; then
      echo "cleanup: refusing to delete ${fulfillment_event_count} immutable payment event(s) for synthetic order ${pedido_id}" >&2
      cleanup_failed=true
    elif [[ -n "${test_fulfillment_id}" ]]; then
      pg_delete "/payment_fulfillments?id=eq.${test_fulfillment_id}" || cleanup_failed=true
    fi
    pg_delete "/pedidos?id=eq.${pedido_id}" || cleanup_failed=true
    if [[ "$(pg_get "/pedidos?id=eq.${pedido_id}&select=id" | jq 'length')" != "0" ]]; then
      echo "cleanup: synthetic order ${pedido_id} still exists" >&2
      cleanup_failed=true
    fi
    if [[ "$(pg_get "/whatsapp_outbox?pedido_id=eq.${pedido_id}&select=id" | jq 'length')" != "0" ]]; then
      echo "cleanup: synthetic outbox rows still exist for order ${pedido_id}" >&2
      cleanup_failed=true
    fi
  fi
  if [[ -n "${test_customer_id}" ]]; then
    pg_delete "/clientes_whatsapp?id=eq.${test_customer_id}" || cleanup_failed=true
  fi
  if [[ -n "${driver_account_id}" ]]; then
    pg_delete "/driver_accounts?id=eq.${driver_account_id}" || cleanup_failed=true
  fi
  if [[ -n "${repartidor_id}" ]]; then
    pg_delete "/repartidores?id=eq.${repartidor_id}" || cleanup_failed=true
  fi
  if [[ -n "${pickup_id}" ]]; then
    if [[ -n "${original_pickup_json}" && "${original_pickup_json}" != "null" ]]; then
      local pickup_payload
      pickup_payload="$(jq '{codigo,nombre,direccion,referencia,telefono,latitude,longitude,activo,service_radius_km}' <<<"${original_pickup_json}")"
      pg_patch "/pickup_points?id=eq.${pickup_id}" "${pickup_payload}" >/dev/null || cleanup_failed=true
    else
      pg_delete "/pickup_points?id=eq.${pickup_id}" || cleanup_failed=true
    fi
  fi
  if [[ "${outbox_worker_was_active}" == true ]]; then
    systemctl --user start replau-outbox.service || cleanup_failed=true
  fi

  if [[ "${cleanup_failed}" == true ]]; then
    echo "cleanup: FAILED; inspect synthetic run ${TEST_RUN_ID}" >&2
    rc=1
  elif [[ ${rc} -eq 0 ]]; then
    echo "cleanup: restored smoke test state"
  else
    echo "cleanup: restored smoke test state after failure" >&2
  fi
  exit "${rc}"
}
trap cleanup EXIT

need curl
need jq
need systemctl
if [[ ! -x "${DRIVER_PYTHON}" ]]; then
  echo "Missing driver Python runtime: ${DRIVER_PYTHON}" >&2
  exit 2
fi

if systemctl --user is-active --quiet replau-outbox.service; then
  outbox_worker_was_active=true
  systemctl --user stop replau-outbox.service
fi
if systemctl --user is-active --quiet replau-outbox.service; then
  echo "Could not stop replau-outbox.service; refusing synthetic dispatch test" >&2
  exit 1
fi

echo "[1/10] Checking app health and dry-run guard"
curl -fsS "${APP_BASE_URL}/health" | jq -e '.ok == true and .postgrest == true and .driver_schema == true' >/dev/null
effective_outbox_environment="$(systemctl --user show replau-outbox.service -p Environment --value)"
if [[ "${effective_outbox_environment}" != *"WHATSAPP_DRY_RUN=true"* ]]; then
  echo "WHATSAPP_DRY_RUN=true is not effective; refusing synthetic dispatch test" >&2
  exit 1
fi

echo "[2/10] Creating isolated synthetic driver identity"
repartidor_id="$(
  pg_post "/repartidores" \
    "{\"codigo\":\"${TEST_DRIVER_CODE}\",\"nombre\":\"Synthetic dispatch driver\",\"whatsapp_number\":\"${TEST_DRIVER_PHONE}\",\"activo\":true,\"orden_turno\":999999}" |
    jq -r '.[0].id'
)"
driver_account_id="$(
  pg_post "/driver_accounts" \
    "{\"repartidor_id\":${repartidor_id},\"phone\":\"${TEST_DRIVER_PHONE}\",\"legal_name\":\"Synthetic dispatch driver\",\"status\":\"ACTIVE\",\"trust_tier\":\"TIER_3_FULLY_VERIFIED\",\"risk_score\":0}" |
    jq -r '.[0].id'
)"
pg_post "/driver_auth_credentials" \
  "{\"driver_account_id\":${driver_account_id},\"pin_salt\":\"synthetic\",\"pin_hash\":\"synthetic\",\"credential_version\":1}" >/dev/null
session_cookie="$(
  DRIVER_ACCOUNT_ID="${driver_account_id}" "${DRIVER_PYTHON}" -c \
    'import os, sys; sys.path.insert(0, "replau_driver_app_package"); import replau_driver_app as app; print(app.create_driver_session(int(os.environ["DRIVER_ACCOUNT_ID"]), 1))'
)"
if [[ -z "${session_cookie}" ]]; then
  echo "Could not create isolated synthetic driver session" >&2
  exit 1
fi

echo "[3/10] Preparing pickup ${PICKUP_CODE}"
pickup_code_q="$(urlencode "${PICKUP_CODE}")"
original_pickup_json="$(pg_get "/pickup_points?codigo=eq.${pickup_code_q}&limit=1" | first_row || true)"
pickup_payload="$(jq -n \
  --arg codigo "${PICKUP_CODE}" \
  --arg nombre "${PICKUP_NAME}" \
  --arg direccion "${PICKUP_ADDRESS}" \
  --argjson latitude "${PICKUP_LATITUDE}" \
  --argjson longitude "${PICKUP_LONGITUDE}" \
  --argjson radius "${PICKUP_RADIUS_KM}" \
  '{codigo:$codigo,nombre:$nombre,direccion:$direccion,latitude:$latitude,longitude:$longitude,service_radius_km:$radius,activo:true}')"
if [[ -n "${original_pickup_json}" ]]; then
  pickup_id="$(jq -r '.id' <<<"${original_pickup_json}")"
  pg_patch "/pickup_points?id=eq.${pickup_id}" "${pickup_payload}" >/dev/null
else
  pickup_id="$(pg_post "/pickup_points" "${pickup_payload}" | jq -r '.[0].id')"
fi

echo "[4/10] Creating isolated synthetic customer and order ${TEST_ORDER_NUMBER}"
test_customer_id="$(
  pg_post "/clientes_whatsapp" \
    "{\"whatsapp_number\":\"${TEST_CUSTOMER_NUMBER}\",\"nombre\":\"Synthetic dispatch smoke test\",\"active\":false}" |
    jq -r '.[0].id'
)"
pedido_id="$(
  pg_post "/pedidos" \
    "{\"pedido_num\":\"${TEST_ORDER_NUMBER}\",\"cliente_id\":${test_customer_id},\"canal\":\"MANUAL\",\"estado\":\"DESPACHADO\",\"metodo_pago\":\"CONTRA_ENTREGA\",\"subtotal\":0,\"delivery\":0,\"total\":0,\"order_url\":\"http://127.0.0.1/order/${TEST_ORDER_NUMBER}?token=smoke-${TEST_RUN_ID}\",\"public_token\":\"smoke-${TEST_RUN_ID}\",\"public_token_expires_at\":\"2099-01-01T00:00:00Z\",\"observacion\":\"SYNTHETIC DRIVER DISPATCH SMOKE TEST ${TEST_RUN_ID}\"}" |
    jq -r '.[0].id'
)"
pedido_num="${TEST_ORDER_NUMBER}"
test_order_created=true
fulfillment_rows="$(pg_get "/payment_fulfillments?pedido_id=eq.${pedido_id}&select=id,status")"
if [[ "$(jq 'length' <<<"${fulfillment_rows}")" != "1" ]] || [[ "$(jq -r '.[0].status' <<<"${fulfillment_rows}")" != "COD_DUE" ]]; then
  echo "Synthetic order did not create exactly one expected COD_DUE fulfillment; refusing test" >&2
  exit 1
fi
test_fulfillment_id="$(jq -r '.[0].id' <<<"${fulfillment_rows}")"
if [[ "$(pg_get "/payment_fulfillment_events?pedido_id=eq.${pedido_id}&select=id" | jq 'length')" != "0" ]]; then
  echo "Synthetic fulfillment unexpectedly has immutable audit events; refusing test" >&2
  exit 1
fi

echo "[5/10] Bringing driver ${driver_account_id} online near pickup"
session_id="$(app_post_form "/api/driver/${driver_account_id}/online" \
  -F "device_id=smoke-driver-dispatch" \
  -F "app_version=smoke" | jq -r '.session_id')"
app_post_form "/api/driver/${driver_account_id}/location" \
  -F "session_id=${session_id}" \
  -F "latitude=${DRIVER_LATITUDE}" \
  -F "longitude=${DRIVER_LONGITUDE}" \
  -F "accuracy_m=5" | jq -e '.ok == true' >/dev/null

echo "[6/10] Mapping ${pedido_num} to pickup ${PICKUP_CODE}"
pg_post "/rpc/driver_set_order_pickup_point" "{\"p_pedido_id\":${pedido_id},\"p_pickup_point_id\":${pickup_id}}" | jq -e '.ok == true' >/dev/null

echo "[7/10] Creating nearby offer batch"
offer_result="$(pg_post "/rpc/driver_create_nearby_offer_batch" "{\"p_pedido_id\":${pedido_id},\"p_pickup_point_id\":${pickup_id},\"p_radius_km\":${OFFER_RADIUS_KM},\"p_max_candidates\":${MAX_CANDIDATES},\"p_offer_ttl_seconds\":300}")"
jq -e '.ok == true and .candidate_count >= 1' <<<"${offer_result}" >/dev/null
batch_id="$(jq -r '.batch_id' <<<"${offer_result}")"

echo "[8/10] Accepting offer through authenticated driver app"
offers="$(curl -fsS "${APP_AUTH[@]}" -H "Cookie: replau_driver_session=${session_cookie}" "${APP_BASE_URL}/api/driver/${driver_account_id}/offers")"
candidate_id="$(jq -r --argjson pedido_id "${pedido_id}" '.offers[] | select(.pedido_id == $pedido_id) | .id' <<<"${offers}" | head -n1)"
if [[ -z "${candidate_id}" ]]; then
  echo "Driver offers API did not return the created candidate" >&2
  exit 1
fi
curl -fsS "${APP_AUTH[@]}" -H "Cookie: replau_driver_session=${session_cookie}" \
  -X POST "${APP_BASE_URL}/driver/app/${driver_account_id}/offers/${candidate_id}/accept" -o /dev/null
assignment_id="$(
  pg_get "/delivery_offer_candidates?id=eq.${candidate_id}&select=accepted_assignment_id&limit=1" |
    jq -r '.[0].accepted_assignment_id // empty'
)"
if [[ -z "${assignment_id}" ]]; then
  echo "Driver app accept route did not create an assignment" >&2
  exit 1
fi

echo "[9/10] Verifying assignment and delivery station visibility"
assignment_row="$(pg_get "/delivery_asignaciones?id=eq.${assignment_id}&select=id,pedido_id,repartidor_id,status&limit=1" | first_row)"
jq -e --argjson pedido_id "${pedido_id}" --argjson repartidor_id "${repartidor_id}" \
  '.pedido_id == $pedido_id and .repartidor_id == $repartidor_id and .status == "ASSIGNED"' <<<"${assignment_row}" >/dev/null
delivery_html="$(curl -fsS "${DELIVERY_UI_URL}")"
if ! grep -q "${pedido_num}" <<<"${delivery_html}"; then
  echo "Delivery Station did not display synthetic order ${pedido_num}" >&2
  exit 1
fi
if ! grep -q "${TEST_DRIVER_CODE}" <<<"${delivery_html}"; then
  echo "Delivery Station did not display synthetic driver ${TEST_DRIVER_CODE}" >&2
  exit 1
fi

echo "[10/10] Verifying notification isolation"
synthetic_outbox_count="$(pg_get "/whatsapp_outbox?pedido_id=eq.${pedido_id}&select=id" | jq 'length')"
if [[ "${synthetic_outbox_count}" != "1" ]]; then
  echo "Expected exactly one isolated synthetic outbox row, found ${synthetic_outbox_count}" >&2
  exit 1
fi

echo "PASS driver dispatch flow: pedido=${pedido_num} driver_account=${driver_account_id} candidate=${candidate_id} assignment=${assignment_id}"
