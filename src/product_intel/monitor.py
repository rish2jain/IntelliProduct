"""Monitor daemon (Section 2.3).

A plain Python scheduled job — no n8n; this is "a cron loop with three alert
rules." On the always-on Mac Studio it runs under ``launchd`` (see
``deploy/com.productintel.monitor.plist``). Two modes:

- ``--once``: a single sweep of all active tracked products, then exit. This is
  the launchd-friendly form (launchd owns the schedule via StartInterval).
- default: an in-process loop sleeping ``MONITOR_INTERVAL_SECONDS`` between
  sweeps, for running under a bare ``python -m product_intel.monitor`` or a
  systemd/launchd KeepAlive service.
"""

from __future__ import annotations

import argparse
import logging
import time

from .service import Service

log = logging.getLogger("product_intel.monitor")


def run_once(service: Service) -> int:
    results = service.check_all_active()
    fired = sum(len(r.get("alerts_fired", [])) for r in results)
    log.info("monitor sweep: %d products checked, %d alerts fired", len(results), fired)
    for r in results:
        for a in r.get("alerts_fired", []):
            log.info("ALERT [%s] %s", a["layer"], a["message"])
    return fired


def run_loop(service: Service, interval: int) -> None:
    log.info("monitor loop started; interval=%ss", interval)
    while True:
        try:
            run_once(service)
        except Exception:  # noqa: BLE001 — never let a sweep crash the daemon
            log.exception("monitor sweep failed")
        time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="product-intel monitor daemon")
    parser.add_argument("--once", action="store_true", help="run a single sweep and exit")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    service = Service()
    try:
        if args.once:
            run_once(service)
        else:
            run_loop(service, service.config.monitor_interval_seconds)
    finally:
        service.close()


if __name__ == "__main__":
    main()
