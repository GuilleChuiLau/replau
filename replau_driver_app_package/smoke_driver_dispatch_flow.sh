#!/usr/bin/env bash
set -euo pipefail

APP_BASE_URL="${APP_BASE_URL:-http://127.0.0.1:8797}"
POSTGREST_BASE_URL="${POSTGREST_BASE_URL:-http://127.0.0.1:3000}"
DELIVERY_UI_URL="${DELIVERY_UI_URL:-http://127.0.0.1:8790/ops/delivery}"
DRIVER_AUTH_USERNAME="${DRIVER_AUTH_USERNAME:-driver}"
DRIVER_AUTH_PASSWORD="${DRIVER_AUTH_PASSWORD:-}"
APP_AUTH=()
if [[ -n "${DRIVER_AUTH_PASSWORD}" ]]; then
  APP_AUTH=(-u "${DRIVER_AUTH_USERNAME}:${DRIVER_AUTH_PASSWORD}")
fi

DRIVER_PHONE="${DRIVER_PHONE:-51900001996}"
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
  curl -fsS "${APP_AUTH[@]}" -X POST "${APP_BASE_URL}${path}" "$@"
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
  set +e

  if [[ -n "${assignment_id}" ]]; then
    pg_patch "/delivery_asignaciones?id=eq.${assignment_id}" '{"status":"CANCELLED","notes":"Cancelled by driver dispatch smoke test cleanup"}' >/dev/null
  fi
  if [[ -n "${candidate_id}" ]]; then
    pg_patch "/delivery_offer_candidates?id=eq.${candidate_id}" '{"status":"CANCELLED"}' >/dev/null
  fi
  if [[ -n "${batch_id}" ]]; then
    pg_patch "/delivery_offer_batches?id=eq.${batch_id}" '{"status":"CANCELLED"}' >/dev/null
  fi
  if [[ -n "${pedido_id}" ]]; then
    if [[ -n "${original_mapping_json}" && "${original_mapping_json}" != "null" ]]; then
      local original_pickup_id
      original_pickup_id="$(jq -r '.pickup_point_id' <<<"${original_mapping_json}")"
      pg_post "/rpc/driver_set_order_pickup_point" "{\"p_pedido_id\":${pedido_id},\"p_pickup_point_id\":${original_pickup_id}}" >/dev/null
    else
      pg_delete "/order_pickup_points?pedido_id=eq.${pedido_id}"
    fi
  fi
  if [[ -n "${session_id}" && -n "${driver_account_id}" ]]; then
    curl -fsS "${APP_AUTH[@]}" -X POST "${APP_BASE_URL}/api/driver/${driver_account_id}/offline" >/dev/null
  fi
  if [[ -n "${driver_account_id}" && -n "${original_driver_status}" ]]; then
    pg_patch "/driver_accounts?id=eq.${driver_account_id}" "{\"status\":\"${original_driver_status}\"}" >/dev/null
  fi
  if [[ -n "${repartidor_id}" && -n "${original_repartidor_activo}" ]]; then
    pg_patch "/repartidores?id=eq.${repartidor_id}" "{\"activo\":$(json_bool "${original_repartidor_activo}")}" >/dev/null
  fi
  if [[ -n "${pickup_id}" ]]; then
    if [[ -n "${original_pickup_json}" && "${original_pickup_json}" != "null" ]]; then
      local pickup_payload
      pickup_payload="$(jq '{codigo,nombre,direccion,referencia,telefono,latitude,longitude,activo,service_radius_km}' <<<"${original_pickup_json}")"
      pg_patch "/pickup_points?id=eq.${pickup_id}" "${pickup_payload}" >/dev/null
    else
      pg_delete "/pickup_points?id=eq.${pickup_id}"
    fi
  fi

  if [[ ${rc} -eq 0 ]]; then
    echo "cleanup: restored smoke test state"
  else
    echo "cleanup: restored smoke test state after failure" >&2
  fi
  exit "${rc}"
}
trap cleanup EXIT

need curl
need jq

echo "[1/9] Checking app health"
curl -fsS "${APP_BASE_URL}/health" | jq -e '.ok == true and .postgrest == true and .driver_schema == true' >/dev/null

echo "[2/9] Loading test driver and repartidor"
driver_row="$(pg_get "/driver_accounts?phone=eq.${DRIVER_PHONE}&limit=1" | first_row)"
if [[ -z "${driver_row}" ]]; then
  echo "No driver account found for DRIVER_PHONE=${DRIVER_PHONE}" >&2
  exit 1
fi
driver_account_id="$(jq -r '.id' <<<"${driver_row}")"
repartidor_id="$(jq -r '.repartidor_id // empty' <<<"${driver_row}")"
original_driver_status="$(jq -r '.status' <<<"${driver_row}")"
if [[ -z "${repartidor_id}" ]]; then
  echo "Driver account ${driver_account_id} is not linked to a repartidor" >&2
  exit 1
fi
repartidor_row="$(pg_get "/repartidores?id=eq.${repartidor_id}&limit=1" | first_row)"
original_repartidor_activo="$(jq -r '.activo' <<<"${repartidor_row}")"

echo "[3/9] Preparing pickup ${PICKUP_CODE}"
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

echo "[4/9] Selecting active unassigned order"
order_rows="$(pg_get "/v_pedidos_logistica?select=id,pedido_num,estado&estado=in.(CONFIRMADO,EN_PREPARACION,DESPACHADO)&order=id.desc&limit=25")"
while IFS= read -r order; do
  candidate_pedido_id="$(jq -r '.id' <<<"${order}")"
  active_assignment_count="$(pg_get "/delivery_asignaciones?select=id&pedido_id=eq.${candidate_pedido_id}&status=in.(ACCEPTED,ASSIGNED,COMPLETED)" | jq 'length')"
  if [[ "${active_assignment_count}" == "0" ]]; then
    pedido_id="${candidate_pedido_id}"
    pedido_num="$(jq -r '.pedido_num' <<<"${order}")"
    break
  fi
done < <(jq -c '.[]' <<<"${order_rows}")
if [[ -z "${pedido_id}" ]]; then
  echo "No active unassigned order found for smoke test" >&2
  exit 1
fi
original_mapping_json="$(pg_get "/order_pickup_points?pedido_id=eq.${pedido_id}&limit=1" | first_row || true)"

echo "[5/9] Bringing driver ${driver_account_id} online near pickup"
pg_patch "/repartidores?id=eq.${repartidor_id}" '{"activo":true}' >/dev/null
pg_patch "/driver_accounts?id=eq.${driver_account_id}" '{"status":"APPROVED"}' >/dev/null
session_id="$(app_post_form "/api/driver/${driver_account_id}/online" \
  -F "device_id=smoke-driver-dispatch" \
  -F "app_version=smoke" | jq -r '.session_id')"
app_post_form "/api/driver/${driver_account_id}/location" \
  -F "session_id=${session_id}" \
  -F "latitude=${DRIVER_LATITUDE}" \
  -F "longitude=${DRIVER_LONGITUDE}" \
  -F "accuracy_m=5" | jq -e '.ok == true' >/dev/null

echo "[6/9] Mapping ${pedido_num} to pickup ${PICKUP_CODE}"
pg_post "/rpc/driver_set_order_pickup_point" "{\"p_pedido_id\":${pedido_id},\"p_pickup_point_id\":${pickup_id}}" | jq -e '.ok == true' >/dev/null

echo "[7/9] Creating nearby offer batch"
offer_result="$(pg_post "/rpc/driver_create_nearby_offer_batch" "{\"p_pedido_id\":${pedido_id},\"p_pickup_point_id\":${pickup_id},\"p_radius_km\":${OFFER_RADIUS_KM},\"p_max_candidates\":${MAX_CANDIDATES},\"p_offer_ttl_seconds\":300}")"
jq -e '.ok == true and .candidate_count >= 1' <<<"${offer_result}" >/dev/null
batch_id="$(jq -r '.batch_id' <<<"${offer_result}")"

echo "[8/9] Accepting offer through driver app dashboard"
offers="$(curl -fsS "${APP_AUTH[@]}" "${APP_BASE_URL}/api/driver/${driver_account_id}/offers")"
candidate_id="$(jq -r --argjson pedido_id "${pedido_id}" '.offers[] | select(.pedido_id == $pedido_id) | .id' <<<"${offers}" | head -n1)"
if [[ -z "${candidate_id}" ]]; then
  echo "Driver offers API did not return the created candidate" >&2
  exit 1
fi
curl -fsS "${APP_AUTH[@]}" -X POST "${APP_BASE_URL}/driver/app/${driver_account_id}/offers/${candidate_id}/accept" -o /dev/null
assignment_id="$(
  pg_get "/delivery_offer_candidates?id=eq.${candidate_id}&select=accepted_assignment_id&limit=1" |
    jq -r '.[0].accepted_assignment_id // empty'
)"
if [[ -z "${assignment_id}" ]]; then
  echo "Driver app accept route did not create an assignment" >&2
  exit 1
fi

echo "[9/9] Verifying assignment and delivery station visibility"
assignment_row="$(pg_get "/delivery_asignaciones?id=eq.${assignment_id}&select=id,pedido_id,repartidor_id,status&limit=1" | first_row)"
jq -e --argjson pedido_id "${pedido_id}" --argjson repartidor_id "${repartidor_id}" \
  '.pedido_id == $pedido_id and .repartidor_id == $repartidor_id and .status == "ASSIGNED"' <<<"${assignment_row}" >/dev/null
delivery_html="$(curl -fsS "${DELIVERY_UI_URL}")"
grep -q "${pedido_num}" <<<"${delivery_html}"
grep -q "DRVTST" <<<"${delivery_html}"

echo "PASS driver dispatch flow: pedido=${pedido_num} driver_account=${driver_account_id} candidate=${candidate_id} assignment=${assignment_id}"
