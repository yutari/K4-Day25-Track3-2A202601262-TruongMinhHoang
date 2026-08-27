.PHONY: test lint typecheck run-chaos compare-cache redis-evidence report clean docker-up docker-down

test:
	pytest -q --junitxml=reports/pytest-results.xml

lint:
	ruff check src tests scripts

typecheck:
	mypy src

run-chaos:
	python scripts/run_chaos.py --config configs/default.yaml --out reports/metrics.json

compare-cache:
	python scripts/compare_cache.py --config configs/default.yaml

redis-evidence:
	python scripts/verify_redis.py

report:
	python scripts/compare_cache.py --config configs/default.yaml
	python scripts/verify_redis.py
	python scripts/generate_report.py --metrics reports/metrics.json --out reports/final_report.md

docker-up:
	docker compose up -d

docker-down:
	docker compose down

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache reports/metrics.json reports/metrics.csv reports/cache_comparison.json reports/redis_evidence.json reports/final_report.md
