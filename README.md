Most important components of the solution:

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
