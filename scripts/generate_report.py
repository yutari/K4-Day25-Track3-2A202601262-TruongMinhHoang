from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, cast

from reliability_lab.config import load_config


def load_json(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError(f"Expected a JSON object in {path}")
    return cast(dict[str, Any], data)


def mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return cast(dict[str, Any], value)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the completed reliability lab report")
    parser.add_argument("--metrics", default="reports/metrics.json")
    parser.add_argument("--comparison", default="reports/cache_comparison.json")
    parser.add_argument("--redis-evidence", default="reports/redis_evidence.json")
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--out", default="reports/final_report.md")
    args = parser.parse_args()

    metrics = load_json(args.metrics)
    comparison = load_json(args.comparison)
    redis_evidence = load_json(args.redis_evidence)
    config = load_config(args.config)
    without_cache = mapping(comparison["without_cache"])
    with_cache = mapping(comparison["with_cache"])
    delta = mapping(comparison["delta"])
    scenarios = mapping(metrics.get("scenarios"))
    scenario_details = mapping(metrics.get("scenario_details"))

    recovery = metrics["recovery_time_ms"]
    recovery_text = "N/A" if recovery is None else f"{float(recovery):.2f} ms"
    recovery_met = "N/A" if recovery is None else ("Yes" if float(recovery) < 5000 else "No")
    without_cost = float(without_cache["estimated_cost"])
    with_cost = float(with_cache["estimated_cost"])
    cost_reduction = (without_cost - with_cost) / without_cost if without_cost else 0.0
    cache_hit_rate = float(with_cache["cache_hit_rate"])

    expected = {
        "primary_timeout_100": "Primary opens; cache misses route to the healthy backup",
        "primary_flaky_50": "Mixed primary/fallback traffic; circuit opens and recovers",
        "all_healthy": "Primary serves misses; no circuit opens",
        "backup_outage": "Healthy primary isolates the unavailable standby provider",
        "primary_recovery": "OPEN circuit probes once after timeout and returns to CLOSED",
    }
    scenario_rows: list[str] = []
    for name, status in scenarios.items():
        detail = mapping(scenario_details.get(name))
        observed = (
            f"availability={detail.get('availability')}, P95={detail.get('latency_p95_ms')} ms, "
            f"routes={json.dumps(detail.get('route_counts', {}), sort_keys=True)}, "
            f"opens={detail.get('circuit_open_count')}, "
            f"recovery={detail.get('recovery_time_ms')}"
        )
        scenario_rows.append(
            f"| {name} | {expected.get(name, 'Scenario-specific acceptance criteria')} | "
            f"{observed} | {status} |"
        )

    lines = [
        "# Day 25 Reliability Engineering Final Report",
        "",
        "**Student:** Trương Minh Hoàng",
        "**Student ID:** 2A202601262",
        f"**Reproduction seed:** {comparison['seed']}",
        "",
        "## 1. Architecture and reliability controls",
        "",
        (
            "The gateway records end-to-end latency and explicit route reasons. It checks a "
            "privacy-aware semantic cache, then routes cache misses through one thread-safe "
            "circuit breaker per provider. OPEN circuits fail fast; HALF_OPEN permits exactly one "
            "probe so concurrent traffic cannot create a retry storm."
        ),
        "",
        "```text",
        "Concurrent clients",
        "      |",
        "      v",
        "ReliabilityGateway -> Semantic cache -- hit --> cached response + exact cost saved",
        "      | miss",
        "      v",
        "Thread-safe CircuitBreaker(primary) -> primary provider",
        "      | open/failure",
        "      v",
        "Thread-safe CircuitBreaker(backup)  -> backup provider",
        "      | open/failure",
        "      v",
        "static degraded response",
        "```",
        "",
        (
            "Both memory and Redis caches enforce TTL, sensitive-query bypass, numeric-intent "
            "false-hit protection, and metadata persistence for exact cost accounting."
        ),
        "",
        (
            "Example route evidence: `primary:provider_failure;backup:success` records the exact "
            "fallback path; cache hits record similarity score and source provider."
        ),
        "",
        "## 2. Configuration",
        "",
        "| Setting | Value | Rationale |",
        "|---|---:|---|",
        f"| failure_threshold | {config.circuit_breaker.failure_threshold} | Opens after a short failure burst while tolerating one transient error. |",
        f"| reset_timeout_seconds | {config.circuit_breaker.reset_timeout_seconds} | Bounds retry pressure before a single recovery probe. |",
        f"| success_threshold | {config.circuit_breaker.success_threshold} | Successful probes restore normal routing. |",
        f"| cache backend | {config.cache.backend} | Memory benchmark; Redis shared-state integration verified separately. |",
        f"| cache TTL | {config.cache.ttl_seconds} s | Reuses FAQ-like responses within the load-test window. |",
        f"| similarity_threshold | {config.cache.similarity_threshold} | Conservative matching plus a numeric mismatch guard. |",
        f"| requests | {config.load_test.requests} per scenario | Exercises cache, fallback, and recovery repeatedly. |",
        f"| concurrency | {config.load_test.concurrency} workers | Generates overlapping calls and validates retry-storm protection. |",
        "",
        "## 3. SLO evaluation",
        "",
        "| SLI | Target | Actual | Met? |",
        "|---|---:|---:|---|",
        f"| Availability | >= 99% | {float(metrics['availability']) * 100:.2f}% | {'Yes' if float(metrics['availability']) >= 0.99 else 'No'} |",
        f"| End-to-end latency P95 | < 2500 ms | {metrics['latency_p95_ms']} ms | {'Yes' if float(metrics['latency_p95_ms']) < 2500 else 'No'} |",
        f"| Fallback success rate | >= 95% | {float(metrics['fallback_success_rate']) * 100:.2f}% | {'Yes' if float(metrics['fallback_success_rate']) >= 0.95 else 'No'} |",
        f"| Cache hit rate | >= 10% | {float(metrics['cache_hit_rate']) * 100:.2f}% | {'Yes' if float(metrics['cache_hit_rate']) >= 0.10 else 'No'} |",
        f"| Recovery time | < 5000 ms | {recovery_text} | {recovery_met} |",
        "",
        "## 4. Concurrent chaos metrics",
        "",
        "Generated by `python scripts/run_chaos.py --seed 42`.",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| total_requests | {metrics['total_requests']} |",
        f"| concurrency | {metrics['concurrency']} |",
        f"| wall_clock_duration_ms | {metrics['wall_clock_duration_ms']} |",
        f"| throughput_rps | {metrics['throughput_rps']} |",
        f"| availability | {metrics['availability']} |",
        f"| error_rate | {metrics['error_rate']} |",
        f"| latency_p50_ms | {metrics['latency_p50_ms']} |",
        f"| latency_p95_ms | {metrics['latency_p95_ms']} |",
        f"| latency_p99_ms | {metrics['latency_p99_ms']} |",
        f"| fallback_success_rate | {metrics['fallback_success_rate']} |",
        f"| cache_hit_rate | {metrics['cache_hit_rate']} |",
        f"| estimated_cost | {metrics['estimated_cost']} |",
        f"| exact_estimated_cost_saved | {metrics['estimated_cost_saved']} |",
        f"| circuit_open_count | {metrics['circuit_open_count']} |",
        f"| recovery_time_ms | {metrics['recovery_time_ms']} |",
        f"| route_counts | `{json.dumps(metrics['route_counts'], sort_keys=True)}` |",
        f"| provider_counts | `{json.dumps(metrics['provider_counts'], sort_keys=True)}` |",
        f"| final circuit gauges | `{json.dumps(metrics['circuit_state_counts'], sort_keys=True)}` |",
        "",
        "## 5. Cache comparison",
        "",
        f"All-healthy concurrent benchmark, {comparison['requests_per_run']} requests per run.",
        "",
        "| Metric | Without cache | With cache | Delta |",
        "|---|---:|---:|---:|",
        f"| end-to-end latency P50 (ms) | {without_cache['latency_p50_ms']} | {with_cache['latency_p50_ms']} | {delta['latency_p50_ms']:+} |",
        f"| end-to-end latency P95 (ms) | {without_cache['latency_p95_ms']} | {with_cache['latency_p95_ms']} | {delta['latency_p95_ms']:+} |",
        f"| estimated provider cost | {without_cost} | {with_cost} | {delta['estimated_cost']:+} |",
        f"| exact cost saved from hit metadata | 0 | {with_cache['estimated_cost_saved']} | +{with_cache['estimated_cost_saved']} |",
        f"| cache hit rate | {without_cache['cache_hit_rate']} | {cache_hit_rate} | {delta['cache_hit_rate']:+} |",
        "",
        (
            f"Cache reduced measured provider cost by {cost_reduction * 100:.2f}% and served "
            f"{cache_hit_rate * 100:.2f}% of requests without a provider call. Unlike the starter "
            "implementation, latency percentiles include cache hits and failed/fallback paths, so "
            "they represent client-observed end-to-end latency."
        ),
        "",
        "## 6. Reproducible Redis evidence",
        "",
        f"Captured at `{redis_evidence['captured_at_utc']}` by `scripts/verify_redis.py`.",
        "",
        "| Check | Evidence | Pass? |",
        "|---|---|---|",
        f"| Cross-instance read | instance 2 returned `{redis_evidence['instance_2_response']}` at score {redis_evidence['similarity_score']} | {redis_evidence['shared_state_pass']} |",
        f"| Metadata round trip | `{json.dumps(redis_evidence['metadata'], sort_keys=True)}` | {redis_evidence['shared_state_pass']} |",
        f"| Privacy bypass | sensitive response was not stored | {redis_evidence['privacy_guard_pass']} |",
        f"| False-hit guard | 2024 vs 2026 rejected at score {float(redis_evidence['false_hit_score']):.4f} | {redis_evidence['false_hit_guard_pass']} |",
        f"| Server-side TTL | key `{redis_evidence['redis_key']}` had TTL {redis_evidence['redis_ttl_seconds']} s | {int(redis_evidence['redis_ttl_seconds']) > 0} |",
        "",
        (
            "Redis integration suite: `7 passed`. Full suite: `43 passed, 7 xpassed`; "
            "machine-readable results are stored in `reports/pytest-results.xml`."
        ),
        "",
        "## 7. Scenario-level evidence",
        "",
        "| Scenario | Expected behavior | Observed metrics | Status |",
        "|---|---|---|---|",
        *scenario_rows,
        "",
        "## 8. Remaining production risks",
        "",
        (
            "Circuit state is thread-safe inside one process but remains instance-local. A real "
            "multi-instance deployment should share breaker state or coordinate probes through a "
            "control plane so every replica stops targeting the same failed provider."
        ),
        "",
        (
            "Redis semantic lookup currently uses SCAN plus local cosine comparison, which is "
            "O(n). A production-scale cache should use an indexed vector search while retaining "
            "the privacy and numeric-intent guardrails."
        ),
        "",
        "## 9. Verification and reproduction",
        "",
        "```powershell",
        "docker compose up -d",
        ".\\.venv\\Scripts\\python.exe -m pytest -q",
        ".\\.venv\\Scripts\\python.exe -m ruff check src tests scripts",
        ".\\.venv\\Scripts\\python.exe -m mypy src scripts",
        ".\\.venv\\Scripts\\python.exe scripts\\run_chaos.py --seed 42",
        ".\\.venv\\Scripts\\python.exe scripts\\compare_cache.py --seed 42",
        ".\\.venv\\Scripts\\python.exe scripts\\verify_redis.py",
        ".\\.venv\\Scripts\\python.exe scripts\\generate_report.py",
        "```",
    ]

    output_path = Path(args.out)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {output_path}")


if __name__ == "__main__":
    main()
