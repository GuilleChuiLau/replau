# Pilot recipe data

This dataset prepares five draft recipes for the first controlled ingredient-
inventory rollout:

- `BURGER_SINGLE`
- `BURGER_SINGLE_CHEESE`
- `FRIES_SMALL`
- `CHICKEN_STRIPS_3`
- `WINGS_6`

The seed adds 14 reusable ingredients and 26 recipe lines. Costs and portions
are conservative planning estimates inferred from the current menu; they are
not assertions about the restaurant's actual suppliers or kitchen process.

## Safety

- All five recipes are created with `active = false`.
- All ingredients are created with `inventory_enforced = false`.
- No opening balances, lots, receipts, or movements are posted.
- Rerunning the seed does not overwrite corrected ingredient costs or recipe
  quantities.

## Apply

```bash
cd /home/guill/.openclaw/workspace/replau
sudo -u postgres psql -v ON_ERROR_STOP=1 -d localapi \
  -f postgrest_local/seed_pilot_recipe_data.sql
```

The final query prints estimated unit food cost and gross margin against the
current selling price.

## Before activation

1. Replace estimated cost/kg with the latest supplier invoice cost, normalized
   to kilograms.
2. Weigh each portion after ordinary kitchen preparation and correct grams.
3. Record opening stock through an ingredient receipt/count with the real lot
   and expiration date; do not edit ledger totals directly.
4. Activate one recipe and run one controlled scanner-picked order.
5. Compare theoretical use with a physical count before activating the other
   recipes or enabling enforcement.
