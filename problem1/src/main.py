import argparse
import logging
from pathlib import Path

import pipeline
import report
from core import config


def main() -> None:
    parser = argparse.ArgumentParser(description="gem5 -> McPAT -> HotSpot simulation flow")
    parser.add_argument("config", type=Path, help="experiment configuration (YAML)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")

    cfg = config.load(args.config)
    failed = pipeline.run_all(cfg)
    page = report.write(cfg)
    logging.info("report: %s", page)
    if failed:
        raise SystemExit(f"{len(failed)} run(s) failed: {', '.join(failed)}")


if __name__ == "__main__":
    main()
