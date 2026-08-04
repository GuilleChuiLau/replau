Most important components of the solution:

Recovery after a host rebuild is documented in
[`REPLAU_RECOVERY_RUNBOOK.md`](REPLAU_RECOVERY_RUNBOOK.md). It covers database
restore, services, Cloudflare, Apache/Tailscale access, driver HTTPS, and public
WhatsApp agent isolation without storing secrets.

1. WhatsApp Bridge (conversation engine)
- Handles customer chat flow, order capture, abuse/driver rules, human handoff, and pause/open ordering logic.
- Sends customer-safe tracking links and integrates with logistics/payment state.

2. Postgres + PostgREST API layer
- Single source of truth for orders, items, status, catalog, recipes, and costing.
- Exposes operational endpoints used by dashboards and bridge.

3. Logistics Dashboard (:8790)
- Real-time order operations (kitchen/picking/delivery), conversation visibility, and human handoff controls.
- Customer tracking page (/track/{pedido_num}?token=*** separated from internal ops views.

4. Ops Dashboard (:8793)
- Manager control center: open/paused ordering, business summary KPIs, gateway/health visibility, and operational links.

5. Product Admin (:8794)
- Catalog management, active pricing, image uploads, public menu (/menu, /api/menu), and recipe/cost modules.

6. Payment Proof Review (:8795)
- Back-office verification flow for payment evidence and reconciliation with order processing.

7. Reliability and automation layer
- systemd services/timers, daily backups, stuck monitor, WhatsApp watchdog, health endpoints, and startup baseline QA/smoke checks.

8. Security and hardening baseline
- Auth-protected admin surfaces, least-privilege service users, constrained service permissions, and controlled ingress/session routing.

9. QA and interoperability tests
- Web QA (non-mutate checks) + integration smoke tests to verify end-to-end order flow after changes.

10. Continuity/memory operations
- Daily memory logs + long-term MEMORY.md to preserve decisions, incidents, and recovery context across restarts.

## Recipe consumption and ingredient variance

Ingredient stock is deducted automatically when scanner picking completes. The
posting is transactional and idempotent: a dispatched order can create only one
ingredient-consumption batch, and an ingredient shortage blocks dispatch only
when enforcement is enabled for that ingredient. Products without a complete
active recipe are reported as skipped during gradual rollout.

Install and validate the upgrade with:

```bash
sudo -u postgres psql -v ON_ERROR_STOP=1 -d localapi \
  -f postgrest_local/add_recipe_consumption_variance.sql \
  -f postgrest_local/test_recipe_consumption_variance.sql
```

The Product Admin Ingredient Ledger shows automatic order consumption plus
daily theoretical usage, recorded waste, actual usage, waste rate, and waste
cost. Tests run inside a transaction and roll back all synthetic data.

A safe, inactive five-product starter dataset is documented in
[`PILOT_RECIPE_DATA.md`](PILOT_RECIPE_DATA.md). It adds draft ingredient costs
and portions without creating stock movements or enabling inventory
enforcement.

The generated `BURGER_SINGLE` invoice/portion/opening-stock estimate is in
[`PILOT_BURGER_CALIBRATION.md`](PILOT_BURGER_CALIBRATION.md). Placeholder stock
is deliberately excluded from the append-only live ledger.
