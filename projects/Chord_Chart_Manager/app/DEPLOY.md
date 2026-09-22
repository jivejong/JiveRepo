# Deployment status

The former GCP `e2-micro` instructions in this file are retired. They described
an earlier deployment direction and no longer match the project's active
infrastructure plan.

The verified baseline is local Docker Compose:

```bash
cp .env.example .env
docker compose up --build -d
docker compose ps
curl http://localhost:3000/healthz
```

This runs PostgreSQL and one application container. The application container
serves both the built React client and the Express API on port 3000.

Cloud work has not started. The proposed Kubernetes/Terraform direction is in
[../infra/INFRA_HANDOFF.md](../infra/INFRA_HANDOFF.md), with resource details
in `MANIFESTS.md` and `TERRAFORM.md` beside it.

Before implementing that plan, explicitly decide whether to retain the current
combined application image or adopt the plan's separate nginx web and Express
API images. Do not treat the split-image topology as already implemented.

Any tablet deployment must keep the client and relative `/api` routes on one
trusted HTTPS origin so the service worker can control the application. If
GetSongBPM-enriched data is used, add the service's required visible backlink
to the React UI before release.
