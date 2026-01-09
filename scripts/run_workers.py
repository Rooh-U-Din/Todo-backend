#!/usr/bin/env python3
"""Dev entrypoint for running background workers.

Usage:
    # Single run (process pending items once)
    python scripts/run_workers.py --once

    # Continuous loop (Ctrl+C to stop)
    python scripts/run_workers.py --loop

    # Loop with custom interval
    python scripts/run_workers.py --loop --interval 10

    # Limit iterations (for testing)
    python scripts/run_workers.py --loop --max-iterations 5

Environment variables:
    WORKER_BATCH_SIZE: Items per batch (default: 50)
    WORKER_MAX_RETRIES: Max retries per item (default: 3)
    WORKER_POLL_INTERVAL_SECONDS: Seconds between cycles (default: 5)
    AI_AUTOMATION_ENABLED: Enable AI automation (default: false)
    AI_CONFIDENCE_THRESHOLD: Min confidence for AI actions (default: 0.8)
"""

import argparse
import logging
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.workers import (
    run_worker_once,
    run_worker_loop,
    configure_worker_logging,
)


def main() -> int:
    """Main entrypoint for worker runner."""
    parser = argparse.ArgumentParser(
        description="Run background workers for Todo app",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Mode selection
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--once",
        action="store_true",
        help="Run workers once and exit",
    )
    mode.add_argument(
        "--loop",
        action="store_true",
        help="Run workers continuously in a loop",
    )

    # Configuration
    parser.add_argument(
        "--interval",
        type=int,
        default=None,
        help="Seconds between cycles (loop mode only)",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="Maximum iterations before stopping (loop mode only)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="Items to process per batch",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=None,
        help="Maximum retries per item",
    )

    # Logging
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose (DEBUG) logging",
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Reduce logging to warnings only",
    )

    args = parser.parse_args()

    # Configure logging
    if args.verbose:
        configure_worker_logging(logging.DEBUG)
    elif args.quiet:
        configure_worker_logging(logging.WARNING)
    else:
        configure_worker_logging(logging.INFO)

    logger = logging.getLogger(__name__)

    try:
        if args.once:
            logger.info("Running workers once...")
            result = run_worker_once(
                batch_size=args.batch_size,
                max_retries=args.max_retries,
            )

            # Print summary
            print(f"\n--- Worker Run Summary ---")
            print(f"Workers run: {result.workers_run}")
            print(f"Total processed: {result.total_processed}")
            print(f"Total failed: {result.total_failed}")

            if result.errors:
                print(f"Errors: {len(result.errors)}")
                for err in result.errors:
                    print(f"  - {err}")

            for name, worker_result in result.worker_results.items():
                print(f"\n{name}:")
                print(f"  Processed: {worker_result.processed_count}")
                print(f"  Failed: {worker_result.failed_count}")

            return 0 if not result.errors else 1

        elif args.loop:
            logger.info("Starting worker loop (Ctrl+C to stop)...")
            run_worker_loop(
                interval_seconds=args.interval,
                max_iterations=args.max_iterations,
                batch_size=args.batch_size,
                max_retries=args.max_retries,
            )
            return 0

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        return 0
    except Exception as e:
        logger.error(f"Worker failed: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
