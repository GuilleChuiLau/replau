# Replau Logistics Viewer

The Logistics workspace combines customer attention, picking, delivery,
dispatch, driver management, tracking, and delivery payouts.

## Payment fulfillment gates

Logistics consumes `api.v_payment_fulfillments` and treats the payment ledger as
the source of truth:

- Prepaid orders may be offered, assigned, or dispatched only after payment is
  `RELEASED`, `RECONCILED`, or `SETTLED`.
- Cash-on-delivery orders may dispatch while `COD_DUE`, but cannot be completed
  until Logistics records the exact collection as `COD_COLLECTED`.
- COD collection uses the fulfillment version shown on screen, preventing a
  stale page from overwriting a newer cashier/logistics decision.
- Driver offer, direct assignment, dispatch, and delivery completion repeat the
  gate server-side; hiding a button is never the only protection.

## Exception-first operations

The dashboard's `Excepciones` view prioritizes active orders with an unreleased
payment, missing confirmed address, dispatched order without an active driver,
or no operational progress for at least 45 minutes. Payment state badges appear
in the dashboard, Picking Station, and Delivery Station.

The payment-fulfillment migration must be installed before this version is
deployed. The viewer remains bound to localhost; customer tracking continues to
use signed order tokens through the restricted public routes.
