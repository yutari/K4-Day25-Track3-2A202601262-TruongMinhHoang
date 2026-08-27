from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from reliability_lab.cache import ResponseCache
from reliability_lab.chaos import run_scenario, run_simulation
from reliability_lab.circuit_breaker import CircuitBreaker, CircuitState
from reliability_lab.config import LabConfig, ProviderConfig, ScenarioConfig
from reliability_lab.gateway import ReliabilityGateway
from reliability_lab.providers import FakeLLMProvider


def build_config(*, requests: int = 12, concurrency: int = 4) -> LabConfig:
    return LabConfig.model_validate(
        {
            "providers": [
                {
                    "name": "primary",
                    "fail_rate": 0.0,
                    "base_latency_ms": 30,
                    "cost_per_1k_tokens": 0.01,
                }
            ],
            "circuit_breaker": {
                "failure_threshold": 2,
                "reset_timeout_seconds": 0.05,
                "success_threshold": 1,
            },
            "cache": {
                "enabled": False,
                "backend": "memory",
                "ttl_seconds": 60,
                "similarity_threshold": 0.9,
            },
            "load_test": {"requests": requests, "concurrency": concurrency},
            "scenarios": [],
        }
    )


def test_half_open_allows_only_one_concurrent_probe() -> None:
    breaker = CircuitBreaker("primary", failure_threshold=1, reset_timeout_seconds=0.01)
    breaker.record_failure()
    breaker.opened_at = time.monotonic() - 1

    with ThreadPoolExecutor(max_workers=12) as executor:
        allowed = list(executor.map(lambda _index: breaker.allow_request(), range(12)))

    assert sum(allowed) == 1
    assert breaker.state == CircuitState.HALF_OPEN


def test_late_failure_does_not_extend_open_timeout() -> None:
    breaker = CircuitBreaker("primary", failure_threshold=1, reset_timeout_seconds=10)
    breaker.record_failure()
    opened_at = breaker.opened_at
    breaker.record_failure()
    assert breaker.opened_at == opened_at


def test_cache_metadata_drives_exact_cost_saved() -> None:
    provider = FakeLLMProvider("primary", 0.0, 1, 0.01)
    breaker = CircuitBreaker("primary", 3, 1)
    gateway = ReliabilityGateway(
        [provider],
        {"primary": breaker},
        ResponseCache(ttl_seconds=60, similarity_threshold=0.9),
    )

    first = gateway.complete("cost metadata query")
    cached = gateway.complete("cost metadata query")

    assert cached.cache_hit
    assert cached.estimated_cost_saved == pytest.approx(first.estimated_cost)
    assert cached.latency_ms > 0
    assert "source_provider=primary" in cached.route_reason


def test_fallback_route_reason_names_every_provider_outcome() -> None:
    primary = FakeLLMProvider("primary", 1.0, 1, 0.01)
    backup = FakeLLMProvider("backup", 0.0, 1, 0.005)
    gateway = ReliabilityGateway(
        [primary, backup],
        {
            "primary": CircuitBreaker("primary", 1, 1),
            "backup": CircuitBreaker("backup", 1, 1),
        },
    )

    response = gateway.complete("route evidence")

    assert response.route == "fallback"
    assert response.route_reason == "primary:provider_failure;backup:success"


def test_concurrent_scenario_exports_load_and_route_metrics() -> None:
    config = build_config()
    scenario = ScenarioConfig(
        name="all_healthy",
        provider_overrides={"primary": 0.0},
    )
    metrics = run_scenario(config, ["one", "two", "three"], scenario)

    assert metrics.total_requests == 12
    assert metrics.concurrency == 4
    assert metrics.route_counts == {"primary": 12}
    assert metrics.provider_counts == {"primary": 12}
    assert len(metrics.latencies_ms) == 12
    assert metrics.wall_clock_duration_ms < sum(metrics.latencies_ms) * 0.75
    assert metrics.throughput_rps > 0


def test_simulation_exports_per_scenario_details() -> None:
    config = build_config(requests=6, concurrency=3)
    config.scenarios = [
        ScenarioConfig(name="all_healthy", provider_overrides={"primary": 0.0})
    ]
    metrics = run_simulation(config, ["one", "two"])

    details = metrics.scenario_details["all_healthy"]
    assert details["status"] == "pass"
    assert details["concurrency"] == 3
    assert details["route_counts"] == {"primary": 6}


def test_recovery_scenario_records_open_to_closed_duration() -> None:
    config = build_config(requests=8, concurrency=2)
    config.providers.append(
        ProviderConfig(
            name="backup",
            fail_rate=0.0,
            base_latency_ms=30,
            cost_per_1k_tokens=0.005,
        )
    )
    scenario = ScenarioConfig(
        name="primary_recovery",
        provider_overrides={"primary": 1.0, "backup": 0.0},
        cache_enabled=False,
        recovery_provider="primary",
        recovery_after_requests=2,
        recovery_pause_seconds=0.06,
    )

    metrics = run_scenario(config, ["one", "two"], scenario)

    assert metrics.recovery_time_ms is not None
    assert metrics.recovery_time_ms < 1000
    assert metrics.route_counts["fallback"] > 0
    assert metrics.route_counts["primary"] > 0
    assert metrics.circuit_state_counts == {"closed": 2}
