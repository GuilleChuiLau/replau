# Replau CI/CD Gate

Script:

```bash
/home/guill/codex/replau_ci_cd_gate.py
```

Purpose: make the local release process repeatable before and after changes.

Default checks:

- Compile every Replau Python source file.
- Verify source files in `/home/guill/codex` match deployed runtime files in `/opt/replau_*`.
- Probe local health endpoints for PostgREST, WhatsApp bridge, Logistics, Kitchen, send adapter, Ops, Product Admin, Payment Proof Review, and OpenClaw gateway.
- Resolve protected local dashboard tokens from live process environments when needed.

Optional checks:

- `--systemd` requires key system services and the OpenClaw gateway user service to be active.
- `--require-clean-git` requires `/home/guill/codex` to have no uncommitted changes.

Recommended release commands:

```bash
# Fast non-mutating gate before deploy or after a narrow code change
/home/guill/codex/replau_ci_cd_gate.py --web-qa

# Full interoperability gate after larger workflow changes
/home/guill/codex/replau_ci_cd_gate.py --web-qa --smoke

# Release-ready gate after committing source changes
/home/guill/codex/replau_ci_cd_gate.py --require-clean-git --systemd --web-qa --smoke

# Static-only check when services are intentionally stopped
/home/guill/codex/replau_ci_cd_gate.py --skip-http
```

Operational notes:

- `--web-qa` runs `/home/guill/codex/replau_web_qa.py --no-mutate`.
- `--smoke` runs `/home/guill/codex/replau_integration_smoke_test.py` and creates a safe smoke order.
- `--require-clean-git` should be used after committing, not while actively editing.
- A failed source/deployed drift check means either source needs to be backfilled from `/opt` or deployment needs to be repeated from source.
- A failed Ops health check should be treated as a release blocker unless the reported critical item is intentionally in progress.
