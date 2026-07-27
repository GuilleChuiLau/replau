# WhatsApp outbound policy

Replau treats WhatsApp as a customer-initiated transactional channel. The
policy layer is designed to reduce account-quality risk; it does not bypass or
weaken WhatsApp enforcement.

## Safe defaults

- Database policy starts `PAUSED`.
- The installed worker must remain `WHATSAPP_DRY_RUN=true` while the current
  restriction is unresolved.
- No outbound row is delivered without a policy `ALLOW`.
- An explicit `STOP`, `BAJA`, `SALIR`, `CANCELAR SUSCRIPCION`, or
  `NO MENSAJES` permanently cancels queued delivery to that recipient until an
  explicit opt-in keyword is received.
- A customer-initiated inbound message opens a 24-hour service window.
- Default limits are 6 messages per recipient/hour, 40/account/hour, and
  150/account/day.
- Three consecutive adapter delivery failures trip the circuit.
- Superseded delivery-progress messages for the same order and recipient are
  coalesced so only the newest state remains.

## Activation

Do not activate while an account restriction is present. After the account is
healthy, verify dry-run policy outcomes first. Activation is an explicit
database action with an operator and reason:

```sql
select api.set_whatsapp_outbound_policy_state(
  'ACTIVE',
  'Restriction cleared; dry-run audit approved',
  'operator-name'
);
```

Changing the database state does not override `WHATSAPP_DRY_RUN=true`. Live
delivery requires a separate, deliberate systemd configuration change after
reviewing volumes, opt-outs, sessions, and Meta account quality.

## Official API

Messages outside the customer-service window remain blocked. If Replau later
migrates to Meta's official WhatsApp Business Platform, approved template
classification should be added as a separate policy input rather than
weakening the session rule for linked-device delivery.
