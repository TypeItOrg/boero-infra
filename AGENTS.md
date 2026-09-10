# Boero infrastructure

This repository owns shared Compose configuration, Nginx examples, environment templates and deployment/rollback scripts. Application Dockerfiles, local development and image publication belong to their respective repositories.

- Staging and production configurations are retained, but neither currently has a provisioned VPS. Configuration is not evidence of an active environment. Consult [staging preparation](docs/STAGING.md) or [production preparation](docs/PRODUCTION.md) only for the relevant task.
- The prepared application changes disable automatic staging deployment while preserving CI and image publication. Production deployment remains manual pending provisioning. Do not provision or deploy unless requested.
- Preserve persistent volumes, private environment files and unrelated worktree/index changes. Commit and push only when requested; do not add or run tests on initiative.
- Follow the user's scope and existing authorization. Complete authorized work without repeated approval questions; an audit does not authorize implementation.
