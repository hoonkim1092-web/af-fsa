# Internal Repository Roles

This is an internal developer note.
Do not surface this in user-facing docs or runtime behavior.

## Repository Split

- Development repository: `agent-factory`
  - URL: <https://github.com/hoonkim1092-web/agent-factory>
  - Main development source of truth for Agent Factory.
  - Core features, architecture changes, tests, docs, experiments, and runtime changes belong here.
- Deployment repository: `af-fsa`
  - URL: <https://github.com/hoonkim1092-web/af-fsa>
  - Deployment and release-facing repository.
  - Deployment packages, release snapshots, installer/distribution sync, and shipping-oriented changes belong here.

## Commit Rule

- If the commit is about building or evolving Agent Factory itself, commit to `agent-factory`.
- If the commit is about shipping, packaging, or deployment distribution, commit to `af-fsa`.
- If unsure, default to `agent-factory` and move the deployment-facing result into `af-fsa` at release time.

## Current Workspace

- The current workspace `C:\Project\agent-factory` should be treated as the development repository.
