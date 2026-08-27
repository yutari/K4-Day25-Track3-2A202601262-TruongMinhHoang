from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from reliability_lab.chaos import load_queries, run_scenario
from reliability_lab.config import ScenarioConfig, load_config


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare an all-healthy run with and without cache")
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--out", default="reports/cache_comparison.json")
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    config = load_config(args.config)
    config.load_test.requests = args.requests
    scenario = ScenarioConfig(
        name="all_healthy",
        description="Deterministic cache comparison",
        provider_overrides={provider.name: 0.0 for provider in config.providers},
    )
    queries = load_queries()

    without_cache_config = config.model_copy(deep=True)
    without_cache_config.cache.enabled = False
    random.seed(args.seed)
    without_cache_metrics = run_scenario(without_cache_config, queries, scenario)
    without_cache = without_cache_metrics.to_report_dict()

    with_cache_config = config.model_copy(deep=True)
    with_cache_config.cache.enabled = True
    random.seed(args.seed)
    with_cache_metrics = run_scenario(with_cache_config, queries, scenario)
    with_cache = with_cache_metrics.to_report_dict()

    comparison = {
        "seed": args.seed,
        "requests_per_run": args.requests,
        "without_cache": without_cache,
        "with_cache": with_cache,
        "delta": {
            "latency_p50_ms": round(
                with_cache_metrics.percentile(50) - without_cache_metrics.percentile(50),
                2,
            ),
            "latency_p95_ms": round(
                with_cache_metrics.percentile(95) - without_cache_metrics.percentile(95),
                2,
            ),
            "estimated_cost": round(
                with_cache_metrics.estimated_cost - without_cache_metrics.estimated_cost,
                6,
            ),
            "cache_hit_rate": round(
                with_cache_metrics.cache_hit_rate - without_cache_metrics.cache_hit_rate,
                4,
            ),
        },
    }

    output_path = Path(args.out)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(comparison, indent=2), encoding="utf-8")
    print(f"wrote {output_path} (seed={args.seed})")


if __name__ == "__main__":
    main()
