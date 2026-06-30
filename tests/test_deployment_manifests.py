"""Deployment configuration regression checks."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_docker_redis_uses_bounded_ttl_aware_lru_policy():
    compose = _read("docker-compose.yml")

    assert "--maxmemory 512mb" in compose
    assert "--maxmemory-policy volatile-lru" in compose
    assert "--maxmemory-samples 10" in compose
    assert '["CMD", "redis-cli", "ping"]' in compose


def test_kubernetes_redis_uses_bounded_ttl_aware_lru_policy():
    manifest = _read("k8s/redis.yaml")

    assert "- --maxmemory" in manifest
    assert "- 512mb" in manifest
    assert "- --maxmemory-policy" in manifest
    assert "- volatile-lru" in manifest
    assert "- --maxmemory-samples" in manifest
    assert '- "10"' in manifest
    assert "readinessProbe:" in manifest
    assert "livenessProbe:" in manifest


def test_kubernetes_config_exposes_cache_and_dependency_requirements():
    config = _read("k8s/configmap.yaml")

    assert 'LRU_CACHE_MAX_SIZE: "10000"' in config
    assert 'LRU_CACHE_TTL: "2592000"' in config
    assert 'FTTH_REQUIRE_REDIS: "1"' in config
    assert 'FTTH_REQUIRE_RABBITMQ: "1"' in config
    assert "REDIS_URL: redis://ftth-redis:6379/0" in config
    assert "RABBITMQ_URL:" not in config


def test_kubernetes_secrets_keep_credentials_out_of_configmap():
    secret = _read("k8s/secret.example.yaml")
    rabbitmq = _read("k8s/rabbitmq.yaml")

    assert "DATABASE_URL:" in secret
    assert "JWT_SECRET_KEY:" in secret
    assert "RABBITMQ_URL:" in secret
    assert "RABBITMQ_DEFAULT_PASS:" in secret
    assert "secretKeyRef:" in rabbitmq
    assert "key: RABBITMQ_DEFAULT_PASS" in rabbitmq


def test_kustomization_includes_core_runtime_manifests_but_not_example_secrets():
    kustomization = _read("k8s/kustomization.yaml")

    for manifest in (
        "namespace.yaml",
        "configmap.yaml",
        "postgres.yaml",
        "redis.yaml",
        "rabbitmq.yaml",
        "api.yaml",
        "worker.yaml",
    ):
        assert f"- {manifest}" in kustomization

    assert "secret.example.yaml" not in kustomization
    assert "postgres-secret.example.yaml" not in kustomization


def test_env_example_documents_cache_and_required_dependency_flags():
    env = _read(".env.example")

    assert "LRU_CACHE_MAX_SIZE=10000" in env
    assert "LRU_CACHE_TTL=2592000" in env
    assert "FTTH_REQUIRE_REDIS=0" in env
    assert "FTTH_REQUIRE_RABBITMQ=0" in env
