# Boero infrastructure

This repository owns shared Compose configuration, Nginx examples, environment templates and deployment/rollback scripts. Application Dockerfiles, local development and image publication belong to their respective repositories.

- Consult [staging operations](docs/STAGING.md) or [production preparation](docs/PRODUCTION.md) only for the relevant task. Verify the target host's containers, effective environment, Nginx configuration and health before claiming operational success; provisioning notes and Compose files are not live evidence.
- Read the relevant application's `.github/workflows/` when assessing deployment triggers; documentation about prepared changes may not match the executing branch. Retain environment configuration and CI/image publication. Do not provision, deploy or change deployment policy unless requested.
- Preserve persistent volumes, private environment files and unrelated worktree/index changes. Commit and push only when requested; do not add or run tests on initiative.
- Follow the user's scope and existing authorization; an audit does not authorize implementation. Complete the requested outcome and authorized verification without stopping after a first draft or repeatedly asking for the same approval. Stop for an unresolved scope, data-loss or access decision, and report blockers or unverified runtime behavior explicitly.
