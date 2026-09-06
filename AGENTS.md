# Development workflow

- Treat `main` as the production branch and `dev` as the staging/integration branch.
- Before creating a feature branch, fetch both branches and fast-forward `dev` from `main` (for example, `git fetch origin main dev && git switch dev && git pull --ff-only origin dev && git merge --ff-only origin/main`). Resolve any synchronization conflicts on `dev` before branching.
- Never develop a feature directly on `main` or `dev`.
- Start each feature or fix from the latest `dev` branch and use a short-lived branch such as `feat/<name>` or `fix/<name>`.
- Open the feature branch against `dev`, ensure its automated checks pass, and merge it into `dev` first.
- After the change has been merged into and validated on `dev`, open a pull request from `dev` to `main` for production promotion.
- Do not merge or push a feature branch directly to `main`.
- Keep `dev` synchronized with `main` after any approved emergency production fix.
- Do not bypass required checks on either promotion step.
