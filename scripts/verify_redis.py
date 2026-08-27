from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from reliability_lab.cache import SharedRedisCache


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture reproducible Redis shared-cache evidence")
    parser.add_argument("--redis-url", default="redis://localhost:6379/0")
    parser.add_argument("--out", default="reports/redis_evidence.json")
    parser.add_argument("--ttl", type=int, default=300)
    parser.add_argument("--prefix", default="rl:cache:evidence:")
    args = parser.parse_args()

    first = SharedRedisCache(args.redis_url, args.ttl, 0.3, args.prefix)
    second = SharedRedisCache(args.redis_url, args.ttl, 0.3, args.prefix)
    first.flush()

    query = "shared reliability query"
    response = "shared response"
    metadata = {"provider": "primary", "estimated_cost": "0.0042"}
    first.set(query, response, metadata)
    observed, score, observed_metadata = second.get_with_metadata(query)

    sensitive_query = "account balance for user 123"
    first.set(sensitive_query, "private response")
    sensitive_observed, _ = second.get(sensitive_query)

    first.set("refund policy for 2024", "2024 policy")
    false_hit_observed, false_hit_score = second.get("refund policy for 2026")

    redis_key = f"{args.prefix}{first._query_hash(query)}"
    evidence = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "instance_1_ping": first.ping(),
        "instance_2_ping": second.ping(),
        "query": query,
        "expected_response": response,
        "instance_2_response": observed,
        "similarity_score": score,
        "metadata": observed_metadata,
        "shared_state_pass": observed == response and observed_metadata == metadata,
        "privacy_guard_pass": sensitive_observed is None,
        "false_hit_guard_pass": false_hit_observed is None and bool(second.false_hit_log),
        "false_hit_score": false_hit_score,
        "redis_key": redis_key,
        "redis_ttl_seconds": int(second._redis.ttl(redis_key)),
        "redis_hash": second._redis.hgetall(redis_key),
        "matching_keys": sorted(str(key) for key in second._redis.scan_iter(f"{args.prefix}*")),
    }

    output_path = Path(args.out)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    first.close()
    second.close()
    print(f"wrote {output_path}")


if __name__ == "__main__":
    main()
