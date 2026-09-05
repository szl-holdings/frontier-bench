# Sovereign deploy — frontier-bench API

The maintained deployment path is now the [bench-plane controller](bench-plane/BENCH_PLANE_OPERATIONS.md).
It verifies receipt admission, stages all three APIs, checks exact payload identity, and
witnesses restart before completing. The commands below describe the older single-service
Compose path; use the controller for new deployments.

Runs on owned hardware (the RTX 4000 Ada bench node). No third-party hosting tier required.

## Prerequisites

- Docker with the compose plugin on the bench node
- Receipts present under `receipts/` (genesis BLOCKED receipt ships with the repo)

## Steps

1. Export verified results (fail-closed; aborts on any bad receipt):

   python tools/sync_results.py receipts site/results.json

2. Build and run:

   docker compose -f deploy/docker-compose.yml up -d --build

3. Verify:

   curl http://127.0.0.1:7861/healthz
   curl http://127.0.0.1:7861/api/results

   Expect state EMPTY_HONEST until the first MEASURED receipt lands. That is correct behavior, not an error.

## Updating data

After new receipts arrive: re-run step 1, then `docker compose -f deploy/docker-compose.yml restart`. The site mount is read-only; the API never mutates results.

## Honesty contract

- /api/results serves only rows exported from hash-chain-verified MEASURED receipts.
- In the controller deployment, missing or unreadable data fails admission. EMPTY_HONEST
  is reserved for a validated dataset containing zero measured rows.
