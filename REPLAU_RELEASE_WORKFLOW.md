# Replau Release Workflow

This tree is the source of truth for local Replau service code:

```bash
/home/guill/codex
```

Deployed runtime copies live under `/opt/replau_*`. The release workflow is intentionally local-first until a remote GitHub/GitLab repository is connected.

Hosted CI runs `.github/workflows/replau-ci.yml`. It executes the static part of the gate with:

```bash
python3 replau_ci_cd_gate.py --skip-deploy-drift --skip-http
```

The full release gate still runs locally because it needs live systemd services, `/opt/replau_*` deployed copies, protected dashboard tokens, and local PostgREST/OpenClaw endpoints.

## Normal Change Flow

1. Edit source files under `/home/guill/codex`.
2. Run the fast gate:

   ```bash
   /home/guill/codex/replau_ci_cd_gate.py --web-qa
   ```

3. Deploy or backfill the relevant `/opt/replau_*` runtime copy.
4. Restart/reload the affected service.
5. Run the full gate for workflow-level changes:

   ```bash
   /home/guill/codex/replau_ci_cd_gate.py --web-qa --smoke
   ```

6. Commit the verified source state:

   ```bash
   git -C /home/guill/codex status --short
   git -C /home/guill/codex add <changed-files>
   git -C /home/guill/codex commit -m "Describe the Replau change"
   ```

7. Confirm the release-ready state:

   ```bash
   /home/guill/codex/replau_ci_cd_gate.py --require-clean-git --systemd --web-qa --smoke
   ```

## Rollback Points

Use git history to inspect and restore source states:

```bash
git -C /home/guill/codex log --oneline --decorate -10
git -C /home/guill/codex show --stat HEAD
```

Do not reset deployed services blindly. If a rollback is needed, inspect the target commit, deploy the specific source files back to `/opt/replau_*`, restart affected services, then rerun the gate.

## Current Caveat

`postgrest_local` originally contained an older standalone git repository with only a single local initial commit and no remote. Its metadata is preserved locally as `postgrest_local/.git-legacy-20260523/` so the top-level Replau repo can track the operational source files directly. The `.gitignore` keeps that legacy metadata, generated caches, virtualenvs, logs, and secrets out of new commits.
