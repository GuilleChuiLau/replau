# Replau Driver App Manual

This manual covers the Phase 3 driver app, dispatch admin page, and smoke-test verification flow.

## Quick Links

```text
Public driver app:
http://localhost/driver

Local driver app:
http://127.0.0.1:8797/driver

Local dispatch admin:
http://127.0.0.1:8797/ops/driver-dispatch

Delivery station:
http://127.0.0.1:8790/ops/delivery
```

The dispatch admin route is token-protected when `REQUIRE_ADMIN_TOKEN=true`.
Use the token from `/etc/replau-driver-app.env`.

## Driver Workflow

### 1. Open Driver App

Go to:

```text
http://localhost/driver
```

Use the **Driver app** form and enter the driver's phone number.

Current test driver:

```text
51900001996
```

This opens:

```text
/driver/app/{driver_account_id}
```

For the current test driver:

```text
http://127.0.0.1:8797/driver/app/1
```

### 2. Check Driver Readiness

The dashboard shows:

- account approval status
- online/offline status
- open offer count
- active assignment count
- linked driver code

The driver can only go online when:

- account status is `APPROVED` or `ACTIVE`
- the account is linked to a `repartidores` row
- that repartidor is active

### 3. Go Online

In **Availability**, enter optional starting coordinates and press **Go online**.

Example coordinates near `TEST_SURCO`:

```text
Latitude: -12.1102
Longitude: -77.0290
```

Going online creates a `driver_online_sessions` row and changes the driver account to `ACTIVE`.

### 4. Update Location

While online, the dashboard shows a location update form.

Enter:

```text
Latitude
Longitude
```

Then press **Update location**.

This creates a `driver_locations` row and refreshes the online session's `last_seen_at`.

### 5. Receive Offers

When dispatch creates a nearby offer, the driver dashboard shows it in **Offers**.

Each offer includes:

- order number
- pickup code and address
- distance
- ETA seconds
- offer status
- expiry timestamp

### 6. Accept Or Decline

Use:

- **Accept** to create a delivery assignment
- **Decline** to reject the offer

Accepting an offer:

- creates a `delivery_asignaciones` row
- marks the selected candidate `ACCEPTED`
- marks competing candidates `LOST`
- marks the batch `ASSIGNED`
- shows the assignment in **Current assignment**

Declining an offer:

- marks the candidate `DECLINED`
- may expire the batch if no other candidates remain

### 7. Go Offline

Press **Go offline** when the driver is done.

This closes active online sessions and returns the account from `ACTIVE` to `APPROVED`.

## Dispatch Workflow

### 1. Open Dispatch Admin

Go to:

```text
http://127.0.0.1:8797/ops/driver-dispatch
```

If token auth is enabled, include the admin token as a query parameter:

```text
http://127.0.0.1:8797/ops/driver-dispatch?token=ADMIN_TOKEN_HERE
```

### 2. Read The KPIs

The top of the page shows:

- **Active pickups**
- **Mapped orders**
- **Open offers**
- **Online drivers**

These help confirm whether dispatch is ready before offering orders.

### 3. Configure Pickup Point

In **Pickup points**, create or update a pickup.

Required fields:

- code
- name
- address
- latitude
- longitude
- service radius km

Current test pickup:

```text
Code: TEST_SURCO
Name: Test Pickup Surco
Latitude: -12.11110000
Longitude: -77.03000000
Radius: 8.05
```

### 4. Map Order To Pickup

In **Order pickup mapping**:

1. Find the active order.
2. Choose the pickup point.
3. Press **Set**.

Mapped orders show a `MAPPED` badge with the pickup code.

### 5. Create Nearby Offer

For the mapped order:

1. Optionally enter radius km.
2. Optionally enter max candidates.
3. Press **Offer**.

The app creates an offer batch and candidate rows for approved, online, nearby drivers.

### 6. Watch Candidate State

In **Nearby offer candidates**, watch:

- batch id
- pickup
- driver
- distance
- candidate status
- batch status
- offered timestamp

Useful statuses:

```text
OFFERED   Driver can accept or decline
VIEWED    Driver viewed the offer
ACCEPTED  Driver accepted
DECLINED  Driver declined
LOST      Another driver accepted
CANCELLED Cancelled by cleanup or dispatch
EXPIRED   Offer expired
```

## Verification

Run the full smoke test after installs or changes:

```bash
cd /home/guill/codex/replau_driver_app_package
./smoke_driver_dispatch_flow.sh
```

The smoke test:

1. checks app health
2. loads the test driver
3. prepares `TEST_SURCO`
4. selects an active unassigned order
5. brings driver account `1` online
6. posts fresh location
7. maps the order to pickup
8. creates a nearby offer batch
9. accepts through the driver dashboard route
10. verifies assignment and delivery station visibility
11. cleans up all temporary operational state

Expected success:

```text
PASS driver dispatch flow: pedido=... driver_account=1 candidate=... assignment=...
cleanup: restored smoke test state
```

## Health Checks

Driver app health:

```bash
curl -fsS http://127.0.0.1:8797/health
```

Driver API health:

```bash
curl -fsS -u driver:DRIVER_PASSWORD http://127.0.0.1:8797/api/driver/health
```

Service status:

```bash
systemctl status replau-driver-app --no-pager
```

## Install Or Upgrade

From the package folder:

```bash
cd /home/guill/codex/replau_driver_app_package
sudo ./install_driver_app_phase2_root.sh
```

Then verify:

```bash
./smoke_driver_dispatch_flow.sh
```

## Troubleshooting

### Admin Page Returns 401

Token auth is enabled.

Check:

```bash
sudo grep '^ADMIN_TOKEN=' /etc/replau-driver-app.env
```

Then open:

```text
http://127.0.0.1:8797/ops/driver-dispatch?token=ADMIN_TOKEN_HERE
```

### Driver Cannot Go Online

Check:

- account status is `APPROVED`
- `repartidor_id` is present
- linked repartidor is active
- no missing Phase 1 tables/views

Useful readback:

```bash
curl -fsS 'http://127.0.0.1:3000/v_driver_accounts?id=eq.1&limit=1' | jq
curl -fsS 'http://127.0.0.1:3000/repartidores?id=eq.7&limit=1' | jq
```

### Offer Creates Zero Candidates

Check:

- driver is online
- driver location is fresh
- driver is inside pickup radius
- repartidor is active
- driver has no active assignment

Useful readback:

```bash
curl -fsS 'http://127.0.0.1:3000/driver_online_sessions?driver_account_id=eq.1&order=started_at.desc&limit=3' | jq
curl -fsS 'http://127.0.0.1:3000/driver_locations?driver_account_id=eq.1&order=captured_at.desc&limit=3' | jq
curl -fsS 'http://127.0.0.1:3000/v_delivery_offer_candidates?driver_account_id=eq.1&order=offered_at.desc&limit=5' | jq
```

### Smoke Test Fails Midway

The smoke test has cleanup traps, but if manual cleanup is needed, check:

```bash
curl -fsS 'http://127.0.0.1:3000/delivery_asignaciones?status=in.(ACCEPTED,ASSIGNED)&order=id.desc&limit=10' | jq
curl -fsS 'http://127.0.0.1:3000/delivery_offer_candidates?status=in.(OFFERED,VIEWED,ACCEPTED)&order=id.desc&limit=10' | jq
curl -fsS 'http://127.0.0.1:3000/order_pickup_points?order=pedido_id.desc&limit=10' | jq
```

Prefer cancelling/restoring through known IDs instead of broad deletes.
