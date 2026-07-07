# Kubernetes Deployment Guide
**snflwr.ai Production Deployment on Kubernetes**

## Prerequisites

- Kubernetes cluster (1.25+)
- kubectl CLI configured
- Docker registry access
- Domain name and DNS configuration
- SSL certificate (via cert-manager)

## Quick Start

### 1. Build and Push Docker Images

```bash
# Build API image
docker build -f docker/Dockerfile -t your-registry/snflwr-ai/api:latest .

# Push to registry
docker push your-registry/snflwr-ai/api:latest
```

### 2. Create Namespace

```bash
kubectl apply -f enterprise/k8s/namespace.yaml
```

### 3. Create Secrets

**IMPORTANT:** Replace placeholder values in `enterprise/k8s/secrets.yaml` or create from `.env.production`:

```bash
# Option 1: Create from environment file
kubectl create secret generic snflwr-secrets \
  --from-env-file=.env.production \
  --namespace=snflwr-ai

# Option 2: Create manually
kubectl apply -f enterprise/k8s/secrets.yaml
```

### 4. Deploy Components

```bash
# Apply in order
kubectl apply -f enterprise/k8s/configmap.yaml
kubectl apply -f enterprise/k8s/postgres-deployment.yaml
kubectl apply -f enterprise/k8s/redis-deployment.yaml

# Wait for databases to be ready
kubectl wait --for=condition=ready pod -l app=postgres -n snflwr-ai --timeout=120s
kubectl wait --for=condition=ready pod -l app=redis -n snflwr-ai --timeout=120s

# Deploy application
kubectl apply -f enterprise/k8s/api-deployment.yaml
kubectl apply -f enterprise/k8s/celery-deployment.yaml

# Deploy ingress (requires nginx-ingress controller)
kubectl apply -f enterprise/k8s/ingress.yaml
```

### 5. Verify Deployment

```bash
# Check pods
kubectl get pods -n snflwr-ai

# Check services
kubectl get svc -n snflwr-ai

# Check ingress
kubectl get ingress -n snflwr-ai

# View logs
kubectl logs -f deployment/snflwr-api -n snflwr-ai
```

## Scaling

### Manual Scaling

```bash
# Scale API servers
kubectl scale deployment snflwr-api --replicas=5 -n snflwr-ai

# Scale Celery workers
kubectl scale deployment celery-worker --replicas=4 -n snflwr-ai
```

### Auto-Scaling (HPA)

HPA is automatically configured for the API deployment:

```bash
# View HPA status
kubectl get hpa -n snflwr-ai

# Adjust HPA settings
kubectl edit hpa snflwr-api-hpa -n snflwr-ai
```

## Database Migration

### Initialize Database Schema

```bash
# Run migration job
kubectl run db-init \
  --image=your-registry/snflwr-ai/api:latest \
  --restart=Never \
  --namespace=snflwr-ai \
  --env-from=configmap/snflwr-config \
  --env-from=secret/snflwr-secrets \
  --command -- python database/init_db.py

# Check job status
kubectl logs db-init -n snflwr-ai

# Delete job after completion
kubectl delete pod db-init -n snflwr-ai
```

### Add Performance Indexes

```bash
kubectl run db-indexes \
  --image=your-registry/snflwr-ai/api:latest \
  --restart=Never \
  --namespace=snflwr-ai \
  --env-from=configmap/snflwr-config \
  --env-from=secret/snflwr-secrets \
  --command -- python database/add_performance_indexes.py
```

## Monitoring

### View Pod Status

```bash
# All pods
kubectl get pods -n snflwr-ai -o wide

# API pods
kubectl get pods -l app=snflwr-api -n snflwr-ai

# Celery workers
kubectl get pods -l app=celery-worker -n snflwr-ai
```

### View Logs

```bash
# API logs
kubectl logs -f deployment/snflwr-api -n snflwr-ai

# Celery worker logs
kubectl logs -f deployment/celery-worker -n snflwr-ai

# PostgreSQL logs
kubectl logs -f deployment/postgres -n snflwr-ai

# All logs from a pod
kubectl logs -f <pod-name> -n snflwr-ai
```

### Exec into Pod

```bash
# Shell into API pod
kubectl exec -it deployment/snflwr-api -n snflwr-ai -- /bin/bash

# Run psql in PostgreSQL pod
kubectl exec -it deployment/postgres -n snflwr-ai -- psql -U snflwr -d snflwr_db
```

## Backup & Restore

### Database Backup

```bash
# Create backup job
kubectl run db-backup \
  --image=your-registry/snflwr-ai/api:latest \
  --restart=Never \
  --namespace=snflwr-ai \
  --env-from=configmap/snflwr-config \
  --env-from=secret/snflwr-secrets \
  --command -- python scripts/backup_database.py backup

# Download backup
kubectl cp snflwr-ai/db-backup:/app/backups ./backups
```

### Database Restore

```bash
# Upload backup to pod
kubectl cp ./backups/backup.sql snflwr-ai/postgres:/tmp/backup.sql

# Restore
kubectl exec -it deployment/postgres -n snflwr-ai -- \
  psql -U snflwr -d snflwr_db -f /tmp/backup.sql
```

## Troubleshooting

### Pod Won't Start

```bash
# Describe pod
kubectl describe pod <pod-name> -n snflwr-ai

# Check events
kubectl get events -n snflwr-ai --sort-by='.lastTimestamp'

# Check logs
kubectl logs <pod-name> -n snflwr-ai --previous
```

### Database Connection Issues

```bash
# Test connection from API pod
kubectl exec -it deployment/snflwr-api -n snflwr-ai -- \
  python -c "from storage.database import db_manager; print(db_manager.execute_read('SELECT 1'))"

# Check PostgreSQL service
kubectl get svc postgres-service -n snflwr-ai

# Test DNS resolution
kubectl run test-dns --image=busybox --rm -it --restart=Never -n snflwr-ai -- \
  nslookup postgres-service
```

### High CPU/Memory Usage

```bash
# View resource usage
kubectl top pods -n snflwr-ai

# View node usage
kubectl top nodes

# Check HPA status
kubectl get hpa -n snflwr-ai
```

## Rolling Updates

### Update Application

```bash
# Update image
kubectl set image deployment/snflwr-api \
  snflwr-api=your-registry/snflwr-ai/api:v2.0.0 \
  -n snflwr-ai

# Watch rollout
kubectl rollout status deployment/snflwr-api -n snflwr-ai

# Rollback if needed
kubectl rollout undo deployment/snflwr-api -n snflwr-ai
```

## Cleanup

### Delete Specific Components

```bash
# Delete deployments
kubectl delete deployment snflwr-api -n snflwr-ai
kubectl delete deployment celery-worker -n snflwr-ai

# Delete services
kubectl delete svc snflwr-api-service -n snflwr-ai
```

### Delete Everything

```bash
# WARNING: This deletes all resources including data
kubectl delete namespace snflwr-ai
```

## Production Checklist

- [ ] Replace all placeholder secrets with secure values
- [ ] Configure DNS for ingress host
- [ ] Install and configure cert-manager for SSL
- [ ] Set up monitoring (Prometheus + Grafana)
- [ ] Configure backup cron jobs
- [ ] Set resource limits and requests appropriately
- [ ] Enable network policies for security
- [ ] Set up log aggregation (ELK stack)
- [ ] Configure HPA based on load testing
- [ ] Test disaster recovery procedures

## Known limitations (before a large production rollout)

Recently hardened (✅): the Ollama model store is now a PersistentVolumeClaim (no
~10–20GB re-pull on pod restart — see `ollama-deployment.yaml`); scheduled
in-cluster backups via `backup-cronjob.yaml` (daily, off-host-capable,
fail-closed); the production Ollama LB (`docker/compose/ollama/nginx.conf`) now
has passive ejection + active failover.

Still open — decide before scaling beyond a pilot:

- **Stateful tier is single-replica by default.** `postgres-deployment.yaml` and
  `redis-deployment.yaml` are `replicas: 1`. See the opt-in HA paths below —
  Postgres via CloudNativePG and Redis via Sentinel; a PodDisruptionBudget only
  helps once these run >1 replica.

### Postgres HA (opt-in)

`postgres-deployment.yaml` is a single replica (fine for dev / low-stakes installs).
For high availability + point-in-time recovery, use CloudNativePG instead:

1. Install the operator (once per cluster, pinned):

       kubectl apply --server-side -f \
         https://raw.githubusercontent.com/cloudnative-pg/cloudnative-pg/release-1.25/releases/cnpg-1.25.0.yaml

2. Set the `snflwr-pg-app` password in `postgres-cnpg.yaml`, then apply it
   (do NOT also apply postgres-deployment.yaml):

       kubectl apply -f postgres-cnpg.yaml

   **Important:** the `snflwr-pg-app` Secret password (the `snflwr` role) MUST match `POSTGRES_PASSWORD` in `snflwr-secrets` (which the API uses to connect) — keep the two in sync or the app will fail to authenticate.

3. In the ConfigMap, comment out `POSTGRES_HOST: "postgres-service"` and uncomment
   `POSTGRES_HOST: "snflwr-pg-rw"` (CNPG's primary service), then redeploy the API.

CNPG runs 1 primary + 2 streaming standbys with automatic failover; `-rw` always
routes to the current primary. Local WAL storage is provisioned by default.

**Enable off-cluster PITR (opt-in — requires an object store):** the `backup:`
block and `ScheduledBackup` in `postgres-cnpg.yaml` are commented out on purpose,
because a placeholder bucket would make CNPG *silently* fail WAL archiving. To turn
on continuous off-cluster archiving + point-in-time recovery:
1. Create the bucket, then the credentials Secret:

       kubectl create secret generic snflwr-pg-backup-creds -n snflwr-ai \
         --from-literal=ACCESS_KEY_ID=<id> --from-literal=SECRET_ACCESS_KEY=<key>

2. Uncomment the `backup:` block AND the `ScheduledBackup` in `postgres-cnpg.yaml`,
   replacing the `<FILL-bucket>` / `<FILL-s3-endpoint>` values, then re-apply.

**Cutover from an existing single-instance Postgres:**
1. `python scripts/backup_database.py backup` (final pg_dump of the old DB).
2. Install the operator + apply `postgres-cnpg.yaml`; wait for the cluster to be ready.
3. Restore the dump into the new cluster (`psql`/`pg_restore` against `snflwr-pg-rw`).
4. Repoint `POSTGRES_HOST` (step 3 above) and redeploy.
5. Retire `postgres-deployment.yaml`.

### Redis HA (opt-in)

`redis-deployment.yaml` is a single replica (fine for dev / low-stakes installs).
For high availability, apply `redis-sentinel.yaml` INSTEAD (1 master + 2 replicas +
3 Sentinels) and set in the ConfigMap:

    REDIS_SENTINEL_ENABLED: "true"
    REDIS_SENTINEL_MASTER:  "mymaster"
    REDIS_SENTINEL_HOSTS:   "redis-sentinel:26379"

    kubectl apply -f redis-sentinel.yaml   # do NOT also apply redis-deployment.yaml

The app and Celery connect to Sentinel (`redis-sentinel:26379`) and follow the
promoted master automatically.
- **Multi-GPU Ollama needs a StatefulSet.** The Deployment + RWO PVC is correct
  for one GPU; per-GPU model caches require a StatefulSet with
  `volumeClaimTemplates` (an RWO PVC can't be shared across nodes).
- **GPU-utilization autoscaling is documented, not bundled.** The DCGM-exporter +
  Prometheus Adapter recipe in `ollama-deployment.yaml` must be installed
  separately; Ollama scaling is otherwise manual (`kubectl scale`).
- **Image version drift.** Manifests pin `snflwr-ai/api:v0.1.0` while the home
  build uses a rolling tag — keep them in sync from one source of truth.

## Open WebUI (Student Chat UI)

As of this release the k8s deployment is no longer API-only. Open WebUI
(OWUI v0.9.x) runs as a first-class workload and serves as the student-facing
chat interface, accessible at `https://chat.snflwr.ai` (operator-set default).

**Safety-network guarantee.** All student LLM traffic is routed through the
`snflwr-api` safety proxy. OWUI cannot reach Ollama (port 11434) directly —
this is enforced at the network layer by `open-webui-netpol.yaml`, not only
at the application layer.

**Data model.** Student accounts and chat history (child PII) are stored in the
CNPG-backed `openwebui` Postgres database (HA + PITR, isolated from the app's
`snflwr_db`). The RWO PVC (`open-webui-data`, 2 Gi) holds only non-PII
vector/scratch data written by OWUI at runtime.

### Prerequisites

Before deploying OWUI, confirm the following:

- **CNPG operator ≥ 1.24** is installed (`Database` CRD requires ≥ 1.24;
  `spec.managed.roles` in `postgres-cnpg.yaml` requires ≥ 1.20). Check the
  installed version with:

      kubectl -n cnpg-system get deploy cnpg-controller-manager \
        -o jsonpath='{.spec.template.spec.containers[0].image}'

- **DNS** for the student host (`chat.snflwr.ai` by default) resolves to the
  ingress controller's external IP or load-balancer hostname.

- **cert-manager** is present with a `letsencrypt-prod` `ClusterIssuer`.

- **Secrets exist** — see "Create required secrets" below.

- **Student host is set** in `open-webui-ingress.yaml` (the `host` field and
  TLS entry both default to `chat.snflwr.ai`).

- **ConfigMap** has `OPEN_WEBUI_URL` set to the public student URL and
  `OPEN_WEBUI_INTERNAL_URL` set to `http://open-webui-service:8080` (the
  address the API uses to reach OWUI inside the cluster).

#### Create required secrets

Two secrets must exist **before** any OWUI manifest is applied:

```bash
# 1. OWUI Postgres role password.
#    Type MUST be kubernetes.io/basic-auth so CNPG can read the username field.
#    This same secret is mounted into the OWUI Deployment and the config-seed
#    Job for DATABASE_URL.
#
#    PASSWORD MUST BE URL-SAFE: the value is interpolated directly into a
#    Postgres connection URL (postgresql://openwebui:<password>@...).
#    Characters such as @ : / # ? will break URL parsing.  Use only
#    alphanumeric + hyphens/underscores, or generate with:
#      python -c 'import secrets; print(secrets.token_urlsafe(32))'
kubectl create secret generic openwebui-db-app \
  --type=kubernetes.io/basic-auth \
  --from-literal=username=openwebui \
  --from-literal=password=<your-secure-password> \
  -n snflwr-ai

# 2. Add OWUI keys to snflwr-secrets (or include when first creating it).
#
#    INTERNAL_API_KEY  — the bearer token the proxy validates; the value here
#                        MUST match INTERNAL_API_KEY in the api's environment.
#    WEBUI_SECRET_KEY  — OWUI session-signing key (generate separately).
#
#    Include all other snflwr-secrets keys in the same command or patch
#    the existing secret:
kubectl create secret generic snflwr-secrets \
  --from-literal=INTERNAL_API_KEY='<proxy-bearer-key>' \
  --from-literal=WEBUI_SECRET_KEY='<webui-signing-key>' \
  ... \
  -n snflwr-ai
```

### Rollout order

Order matters: OWUI's Alembic migrations run at container startup and create
the `config` table that the config-seed Job writes into. Apply in this sequence:

```bash
# 1. Confirm both required secrets exist.
kubectl get secret openwebui-db-app snflwr-secrets -n snflwr-ai

# 2. Declare the openwebui Postgres role (managed.roles) and database.
#    managed.roles in postgres-cnpg.yaml provisions the role declaratively —
#    there is no separate superuser init Job.
kubectl apply -f enterprise/k8s/postgres-cnpg.yaml
kubectl apply -f enterprise/k8s/open-webui-db.yaml

# 3. Apply the PVC and Deployment; wait for OWUI to boot and run migrations.
#    IMPORTANT — do this BEFORE the network policy (step 4). At startup OWUI
#    downloads its all-MiniLM embedding model from HuggingFace; the egress
#    NetworkPolicy blocks the internet, so OWUI must have internet on this first
#    boot to fetch + cache the model into the PVC (/app/backend/data/cache).
#    Thereafter the Deployment's HF_HUB_OFFLINE=1 / TRANSFORMERS_OFFLINE=1 make
#    OWUI load the cached model without phoning home, so it boots fine under the
#    policy. If the PVC is ever lost, re-cache by temporarily removing the egress
#    policy, restarting OWUI once, then re-applying the policy.
kubectl apply -f enterprise/k8s/open-webui-pvc.yaml
kubectl apply -f enterprise/k8s/open-webui-deployment.yaml
kubectl rollout status deployment/open-webui -n snflwr-ai

# 4. Apply network policies (only after OWUI's first boot has cached the model).
kubectl apply -f enterprise/k8s/open-webui-netpol.yaml

# 5 + 6. Seed the proxy bearer into OWUI's config AND restart OWUI so it reloads
#    it. OWUI caches config at boot, so the restart is mandatory — without it the
#    student model list is empty. Use the wrapper so the restart can't be
#    forgotten (it runs the config-seed Job, waits, then does the rollout-restart):
#
#    PREREQUISITE — api image must be rebuilt from this branch first:
#    The config-seed Job runs the snflwr-ai/api image and calls
#    scripts/owui_connect.py, added to the image via a Dockerfile change. Without
#    a rebuilt+pushed image the Job pod fails with "No such file or directory".
#      docker build -f docker/Dockerfile -t snflwr-ai/api:v0.1.0 .
#      docker push snflwr-ai/api:v0.1.0
scripts/owui_k8s_seed_and_restart.sh snflwr-ai
#
#    (Equivalent manual steps, if you prefer:
#      kubectl apply -f enterprise/k8s/open-webui-config-seed-job.yaml
#      kubectl wait --for=condition=complete job/open-webui-config-seed -n snflwr-ai --timeout=300s
#      kubectl rollout restart deployment/open-webui -n snflwr-ai
#      kubectl rollout status deployment/open-webui -n snflwr-ai )

# 7. Apply the ingress (TLS, cert-manager letsencrypt-prod).
kubectl apply -f enterprise/k8s/open-webui-ingress.yaml
kubectl get ingress open-webui-ingress -n snflwr-ai
```

### Safety-network guarantee

The egress NetworkPolicy (`open-webui-egress`) selects all pods labeled
`app: open-webui` — including the config-seed Job pod — and permits only:

| Destination | Port | Purpose |
|---|---|---|
| `snflwr-api` pods | TCP 8000 | Safety proxy (only allowed LLM backend) |
| `snflwr-pg` CNPG pods | TCP 5432 | OWUI Postgres database |
| Any namespace | UDP + TCP 53 | kube-dns service resolution |

Ollama (port 11434) is intentionally absent from the allowlist. Because a
NetworkPolicy selects OWUI's egress, Kubernetes enforces default-deny for
everything not listed — omitting Ollama is what enforces the child-safety
invariant at the network layer, independent of application configuration.

The ingress NetworkPolicy (`open-webui-ingress`) restricts inbound connections
to the ingress-nginx controller (student traffic) and `snflwr-api` pods (admin
callbacks and family-portal redirects).

> **Kubelet probe caveat.** If OWUI pods become stuck in `NotReady` after
> applying `open-webui-netpol.yaml`, your CNI may enforce ingress NetworkPolicy
> for kubelet probes. Add an `ipBlock` rule for your node subnet to the ingress
> spec (see the header comment in `open-webui-netpol.yaml` for the exact
> snippet to add).

### Post-deploy smoke test

```bash
# 1. Browse https://chat.snflwr.ai (or your configured student host).
#    Sign in as a provisioned student account.

# 2. Open the model dropdown — "snflwr.ai" must be listed.
#    Send a message and confirm the tutor returns an answer.

# 3. Send a blocked prompt (e.g. an age-inappropriate question).
#    Expect the safe-redirect response, not a raw model reply.

# 4. Confirm the NetworkPolicy blocks direct Ollama access:
kubectl exec deploy/open-webui -n snflwr-ai -- \
  sh -c 'wget -T3 -qO- http://ollama-service:11434/ || echo BLOCKED_AS_EXPECTED'
# Expected: BLOCKED_AS_EXPECTED

# 5. (Optional) Confirm admin can provision a family via the API:
kubectl exec deploy/snflwr-api -n snflwr-ai -- \
  curl -s http://localhost:8000/api/admin/families \
  -H "Authorization: Bearer $INTERNAL_API_KEY"
```

## Additional Resources

- [Kubernetes Documentation](https://kubernetes.io/docs/)
- [Horizontal Pod Autoscaling](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/)
- [Ingress NGINX](https://kubernetes.github.io/ingress-nginx/)
- [Cert-Manager](https://cert-manager.io/docs/)
