# Kubernetes Deployment

This folder provides a production-ready starting point for the FTTH API, worker,
PostgreSQL, Redis, and RabbitMQ.

## Redis policy

Redis is intentionally configured with bounded memory and TTL-friendly eviction:

- `--maxmemory 512mb`
- `--maxmemory-policy volatile-lru`
- `--maxmemory-samples 10`

The application cache writes entries with TTL through `SETEX`, and job progress
hashes are refreshed with a 24-hour expiry. `volatile-lru` therefore evicts only
keys that are safe to expire.

## Before applying

1. Create `ftth-secrets` from your secret manager, or copy
   `k8s/secret.example.yaml` to a private manifest and replace every placeholder.
2. Create `ftth-postgres-secret`, or copy `k8s/postgres-secret.example.yaml` to a
   private manifest and make the password match the `DATABASE_URL` password.
3. Replace `ghcr.io/your-org/your-repo/ftth-api:latest` in `api.yaml` and
   `worker.yaml` with the image built by your CI/CD pipeline.
4. Set `FTTH_CORS_ORIGINS` in `configmap.yaml` to the deployed frontend/API
   origin.
5. Do not add real secret manifests to git.

## Apply

```bash
kubectl apply -f k8s/secret.example.yaml
kubectl apply -f k8s/postgres-secret.example.yaml
kubectl apply -k k8s
```

Use the two example secret commands only for local cluster smoke tests after
replacing the placeholder values. In production, create those secrets from your
cluster secret manager instead.

For public traffic, copy `ingress.example.yaml`, update the host/TLS secret, and
apply it after the API service is healthy.
