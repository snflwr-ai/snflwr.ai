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

## Additional Resources

- [Kubernetes Documentation](https://kubernetes.io/docs/)
- [Horizontal Pod Autoscaling](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/)
- [Ingress NGINX](https://kubernetes.github.io/ingress-nginx/)
- [Cert-Manager](https://cert-manager.io/docs/)
