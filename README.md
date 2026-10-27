# Two-tier network on Google Cloud

**Status:** in progress. Phase 1 is the plan and the name checks. Nothing in this repository has been created in Google Cloud yet.

## At a glance

- **What it will prove:** a database virtual machine with no public address accepts the database port from the web tier only. A third machine in the same network is blocked.
- **Where it stands:** `python -m src.deploy --plan` prints that plan and calls nothing. The live deploy is a later phase.
- **Project id:** comes from `.env` when you build. The id on the architecture picture, `twotier-poc-zeon01`, is a placeholder. Pick a fresh one. A deleted project id cannot be reused for up to 30 days.

Part of [Cloud Projects](https://github.com/zsociety47/cloud-projects).

## Run this as yourself

The names committed here are this proof. A later operator replaces only the owner slot.

| File | What to change |
|---|---|
| [`.env.example`](.env.example) | `yourname01` in `GCP_PROJECT_ID`, then copy the file to `.env` and set the billing account id. `.env` is not committed. |

## Print the plan

From this folder, with no `.env` required:

```bash
python3 -m src.deploy --plan
```

```bash
python3 -m unittest discover -s tests
```

The tests check names, labels, and that the plan does not print a billing account id. They do not call Google Cloud.

The design picture is [docs/architecture/architecture.png](docs/architecture/architecture.png). The print version is [docs/architecture/architecture.pdf](docs/architecture/architecture.pdf).
