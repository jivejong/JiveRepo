# k8s-data-platform — handoff

Companion repository to `batcave-ids`. This is where Terraform and Kubernetes live, as the subject
rather than as accessories to a data pipeline.

**Premise:** provision a Kubernetes cluster with Terraform, deploy Airflow with the
KubernetesExecutor via Helm, and run the `batcave-ids` pipeline on it as a real workload. The two
repositories have a genuine relationship rather than an invented one.

---

## Repository metadata

**Description:**
> Kubernetes data platform: Terraform-provisioned cluster, Airflow on KubernetesExecutor via Helm,
> RBAC, persistent storage, and a documented troubleshooting log from real failure modes.

**Topics:**
`kubernetes` `terraform` `helm` `airflow` `infrastructure-as-code` `devops` `kubernetes-operators`
`rbac` `data-platform` `gitops` `digitalocean` `kustomize`

Note: GitHub Linguist reports `.tf` as **HCL** and manifests as YAML. The words Terraform and
Kubernetes appear only in the description and topics.

---

## Why this is a separate repository

Terraform and Kubernetes were removed from `batcave-ids` because nothing there needed them: a single
stateless service and a local broker do not justify a cluster, and provisioning infrastructure whose
only purpose is hosting a demo is not a demonstration of infrastructure skill.

Here they are load-bearing. Airflow with the KubernetesExecutor genuinely requires RBAC, persistent
volumes, a StatefulSet, secrets, and correct resource limits. That is where the learning is.

Say this in both READMEs. Removing a technology because it did not earn its place, then giving it a
home where it does, is a stronger signal than scattering both across every project.

---

## Target stack

| Layer | Technology |
|---|---|
| Local cluster | kind, 3-node config |
| Cloud cluster | DigitalOcean Kubernetes (free control plane, nodes from ~$12/mo) |
| Provisioning | Terraform (`digitalocean` provider, `kubernetes` provider, `helm` provider) |
| Orchestrator | Airflow via the official Helm chart, KubernetesExecutor |
| Metadata DB | PostgreSQL StatefulSet with a PVC, or a managed database |
| Ingress | ingress-nginx, cert-manager for TLS |
| Config | Kustomize base + overlays (`kind`, `doks`) |
| Observability | kube-prometheus-stack (optional, Phase 5) |

DigitalOcean, Vultr, and Linode all provide the control plane free and charge only for nodes. EKS
charges $0.10/hour per cluster with no free tier, roughly $73/month, which is why it is not the
default here. If AWS specifically matters for your audience, see the note at the end.

---

## Phases

### Phase 0 — kind cluster, by hand  (4–6h)
Before any Terraform. Create a 3-node kind cluster, deploy a trivial service, expose it, break it.

**Checkpoint:** you can explain, without looking anything up, what a Service does that a Deployment
does not, and why a pod might sit in `Pending`.

### Phase 1 — Airflow on kind via Helm  (8–12h)
Official Helm chart, KubernetesExecutor, values file under version control. PostgreSQL StatefulSet
with a PVC. RBAC for the scheduler to create task pods. Secrets for connections.

**Checkpoint:** a DAG runs, each task creates its own pod, and you can retrieve logs from a
completed task pod. Confirm the PVC survives a `helm upgrade`.

### Phase 2 — Deliberate breakage  (6–8h)
Cause each of these, diagnose it, and write up the diagnosis path in `docs/troubleshooting.md`:

`CrashLoopBackOff` · `ImagePullBackOff` · pod `Pending` on insufficient resources · `OOMKilled` from
a low memory limit · failing readiness probe removing a pod from its Service · cross-namespace DNS
resolution · a `NetworkPolicy` blocking more than intended · a PVC that will not bind · an RBAC
denial · a `helm upgrade` that orphans resources

**This document is the most valuable artifact in the repository.** Almost nobody writes one, and it
reads as operational experience rather than tutorial completion. Include the actual error text and
the actual commands used to diagnose each.

### Phase 3 — Terraform  (10–14h)
```
terraform/
  modules/
    cluster/        # DOKS cluster, node pool, VPC
    platform/       # namespaces, RBAC, storage classes
    airflow/        # helm_release with values
    ingress/        # ingress-nginx, cert-manager, DNS records
  envs/
    kind/           # kubernetes + helm providers against local kind
    doks/           # digitalocean provider, remote state
```

Every module gets `main.tf`, `variables.tf`, `outputs.tf`, `versions.tf`, and a `README.md`.
Use `terraform-docs` to generate the module READMEs.

**Checkpoint:** `terraform apply` against kind reproduces the Phase 1 environment from scratch.
Destroy and re-apply twice to confirm idempotence.

### Phase 4 — Real cluster  (6–10h)
Apply to DOKS. Real LoadBalancer, real DNS, real TLS via cert-manager. Run for one bounded month.

**Checkpoint:** Airflow reachable over HTTPS at a real domain. Drain a node and confirm pods
reschedule. Then `terraform destroy` and verify nothing remains billing.

### Phase 5 — Integration and presentation  (6–10h)
- `batcave-ids` DAGs running on this platform against a containerized version of the pipeline
- Architecture diagram
- `docs/troubleshooting.md` complete
- CI: `terraform validate`, `tflint`, `checkov` or `tfsec`, `kubeval` or `kubeconform` on manifests
- Cost writeup: what a month actually cost, itemized
- Honest limitations: single environment, no multi-region, no production SLO

Estimated total: **40–60 focused hours.**

---

## Free CI wins

All run without a cloud account and all produce visible green checks:

- `terraform validate` + `tflint`
- `checkov` or `tfsec` for IaC security scanning
- `terraform-docs` for module documentation
- `kubeconform` for manifest schema validation
- `helm template` + `helm lint`

Committed CI showing IaC passing security scanning is uncommon in portfolio Terraform and takes
about an hour to set up.

---

## The AWS question

Your internal audience runs AWS. Under the current plan, no repository in your portfolio touches it.
Databricks Free Edition runs on Databricks-managed infrastructure, so it does not fill that gap
either.

Three options, in order of cost:

1. **Provision AWS resources without EKS.** Point this repository's Terraform at S3, IAM, ECR, and a
   VPC while the cluster runs on DOKS. This puts real AWS Terraform in the portfolio for a few
   dollars, and a hybrid setup is defensible as a cost-conscious architecture decision.
2. **One bounded month of EKS.** About $73 plus nodes. Deploy, capture evidence, destroy. Adds the
   literal words "EKS" and "AWS" to the description.
3. **Skip it** and rely on your résumé and LinkedIn to carry AWS, on the argument that the Kubernetes
   and Terraform skills transfer directly and a reviewer will understand that.

Option 1 is the best value. Decide deliberately rather than discovering the gap later.
