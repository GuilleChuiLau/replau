# Generated simple-burger calibration estimate

This is a complete synthetic calibration pack for `BURGER_SINGLE`. It lets us
plan and validate the workflow without pretending that generated values came
from real invoices, kitchen scales, or a physical stock count.

Machine-readable source:
[`postgrest_local/pilot_burger_calibration_estimate.csv`](postgrest_local/pilot_burger_calibration_estimate.csv)

## Generated assumptions

| Ingredient | Package estimate | Cost/kg | Portion | Opening stock estimate | Placeholder expiration |
|---|---:|---:|---:|---:|---:|
| Burger bun | 24 × 75 g for S/22.50 | S/12.50 | 75 g | 1.500 kg | 2026-08-06 |
| Ground beef | 5 kg for S/150.00 | S/30.00 | 100 g | 5.000 kg | 2026-08-05 |
| Lettuce | 2.5 kg for S/20.00 | S/8.00 | 15 g | 1.000 kg | 2026-08-05 |
| Tomato | 5 kg for S/30.00 | S/6.00 | 25 g | 2.000 kg | 2026-08-07 |
| Onion | 5 kg for S/25.00 | S/5.00 | 10 g | 2.000 kg | 2026-08-17 |
| House sauce | 2 kg for S/36.00 | S/18.00 | 20 g | 1.000 kg | 2026-08-10 |
| Salt | 1 kg for S/2.00 | S/2.00 | 1 g | 1.000 kg | 2028-08-03 |

## Derived results

- Estimated ingredient cost per burger: **S/4.6195**.
- Current selling price: **S/12.00**.
- Estimated gross contribution before labor, packaging, utilities, tax, waste,
  and overhead: **S/7.3805**, or **61.5%**.
- Generated opening stock value: **S/218.75**.
- The limiting generated opening quantity is the bun: **20 burgers**.

## Safety decision

The package prices, lot codes, stock quantities, and expiration dates are
generated placeholders. They are intentionally marked
`GENERATED_ESTIMATE_NOT_INVOICE` and `apply_to_live=false`.

They must not be posted to the append-only live ingredient ledger. Doing so
would create fictional inventory that can only be corrected by compensating
movements. The already deployed costs and recipe grams may remain as inactive
planning estimates until physical measurements replace them.

## Rollback-only end-to-end simulation

The generated data can exercise the real scanner flow without becoming live
inventory:

```bash
cd /home/guill/.openclaw/workspace/replau
sudo -u postgres psql -v ON_ERROR_STOP=1 -d localapi \
  -f postgrest_local/test_pilot_burger_rollback_simulation.sql
```

The contract temporarily activates the recipe and enforcement, posts the seven
generated opening lots, scans two burgers, completes picking, validates 0.492 kg
and S/9.2390 of ingredient consumption, checks idempotency and 18 remaining
burger units, then rolls back. Post-rollback assertions reject any retained
synthetic order, customer, lot, movement, activation, or enforcement state.
