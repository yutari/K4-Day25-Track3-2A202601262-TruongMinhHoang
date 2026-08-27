from __future__ import annotations

import argparse
import random
from pathlib import Path

from reliability_lab.chaos import load_queries, run_simulation
from reliability_lab.config import load_config


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--out", default="reports/metrics.json")
    parser.add_argument("--csv-out", default="reports/metrics.csv")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    random.seed(args.seed)
    config = load_config(args.config)
    metrics = run_simulation(config, load_queries())
    metrics.write_json(args.out)
    metrics.write_csv(args.csv_out)
    print(f"wrote {Path(args.out)} and {Path(args.csv_out)} (seed={args.seed})")


if __name__ == "__main__":
    main()
