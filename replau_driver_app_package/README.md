# Replau Driver App Package

Driver onboarding, verification, and nearby dispatch app for Replau.

This package does not replace the existing dispatch module. It sits in front of it:

- Applicants live in `api.driver_accounts`.
- Evidence lives in `api.driver_documents`, `api.driver_vehicles`, and `api.driver_verification_checks`.
- Existing operational drivers stay in `api.repartidores`.
- Approval links a driver account to a `repartidores` row through `api.driver_approve_to_repartidor(...)`.

## Files

```text
add_driver_app_phase1.sql
add_driver_app_phase2.sql
replau_driver_app.py
requirements.txt
.env.example
replau-driver-app.service
install_driver_app_root.sh
install_driver_app_phase2_root.sh
harden_driver_app_public_edge_root.sh
smoke_driver_dispatch_flow.sh
```

## Local dry run

From this package folder:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
POSTGREST_BASE_URL=http://127.0.0.1:3000 APP_PORT=8796 ./replau_driver_app.py
```

Open:

```text
http://127.0.0.1:8796/driver
http://127.0.0.1:8796/ops/drivers
```

## Phase 1 database migration

Review first:

```bash
less add_driver_app_phase1.sql
```

Apply when ready:

```bash
sudo -u postgres psql -d localapi -f add_driver_app_phase1.sql
```

Then reload/check PostgREST:

```bash
curl -sS http://127.0.0.1:3000/v_driver_accounts?limit=1
curl -sS http://127.0.0.1:8796/health
```

## Phase 1 install

Preferred helper:

```bash
sudo ./install_driver_app_root.sh
```

Manual equivalent:

```bash
sudo mkdir -p /opt/replau_driver_app
sudo cp replau_driver_app.py requirements.txt /opt/replau_driver_app/
sudo cp .env.example /etc/replau-driver-app.env
sudo chown -R guill:guill /opt/replau_driver_app
sudo -u guill python3 -m venv /opt/replau_driver_app/.venv
sudo -u guill /opt/replau_driver_app/.venv/bin/pip install -r /opt/replau_driver_app/requirements.txt
sudo cp replau-driver-app.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now replau-driver-app
```

## Phase 2 dispatch upgrade

Phase 2 adds pickup-point mapping, nearby offer batches, driver offer accept/decline APIs, and the local dispatch admin page.

Preferred helper:

```bash
sudo ./install_driver_app_phase2_root.sh
```

It:

- applies `add_driver_app_phase2.sql`
- installs the updated `replau_driver_app.py`
- restarts `replau-driver-app`
- reloads the PostgREST schema cache
- verifies `/health` and `/api/driver/health`

Open locally:

```text
http://127.0.0.1:8796/ops/driver-dispatch
http://localhost/driver
```

## Dispatch smoke test

Run after Phase 2 install:

```bash
./smoke_driver_dispatch_flow.sh
```

The smoke test uses the app and PostgREST APIs to:

- find the test driver account, default phone `51900001996`
- activate `DRVTST`
- prepare pickup `TEST_SURCO`
- choose an active unassigned order
- put the driver online and post a fresh location
- map the order to the pickup
- create a nearby offer batch
- accept the offer through `/api/driver/{account_id}/offers/{candidate_id}/accept`
- verify the assignment and delivery-station visibility
- clean up by cancelling the assignment/offer and restoring original fixture state

Useful overrides:

```bash
DRIVER_PHONE=51900001996 PICKUP_CODE=TEST_SURCO ./smoke_driver_dispatch_flow.sh
APP_BASE_URL=http://127.0.0.1:8796 POSTGREST_BASE_URL=http://127.0.0.1:3000 ./smoke_driver_dispatch_flow.sh
```

## Public edge sketch

Preferred hardening helper:

```bash
sudo ./harden_driver_app_public_edge_root.sh
```

It:

- sets `REQUIRE_ADMIN_TOKEN=true`
- generates/preserves `ADMIN_TOKEN`
- exposes only `/driver` and `/api/driver` through nginx
- keeps `/ops/drivers` off the public edge
- backs up the nginx site before editing
- runs `nginx -t`, restarts the app, reloads nginx, and prints the token

Manual nginx equivalent:

```nginx
upstream replau_driver_app_public {
    server 127.0.0.1:8796 max_fails=3 fail_timeout=10s;
    keepalive 16;
}

location ^~ /driver {
    limit_req zone=replau_public burst=30 nodelay;
    proxy_pass http://replau_driver_app_public;
}

location ^~ /api/driver {
    limit_req zone=replau_public burst=60 nodelay;
    proxy_pass http://replau_driver_app_public;
}
```

Keep `/ops/drivers` local/admin-only until token auth is required and tested.
