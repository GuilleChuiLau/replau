# Replau WhatsApp templates

`templates.es_PE.json` contains three transactional utility-template drafts for
Meta review: order confirmed, order ready, and order dispatched.

Validation is local and non-mutating by default:

```bash
python3 provision_templates.py
```

Creating templates is intentionally guarded. It requires `--apply`,
`META_TEMPLATE_APPLY_CONFIRM=YES`, `META_WABA_ID`, and `META_ACCESS_TOKEN`.
Do not apply while the WhatsApp account has an unresolved restriction. Template
creation also does not activate the Meta transport or Replau outbound policy.
