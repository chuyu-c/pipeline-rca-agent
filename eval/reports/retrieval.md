# Retrieval eval

Generated 2026-10-28 06:42 UTC · embedder `fastembed:BAAI/bge-small-en-v1.5` · k=5 · 9 scenarios · query = scenario `symptoms`

## Summary

| mode | mean recall@5 | MRR | scenarios with all expected docs in top 5 |
|---|---|---|---|
| vector | 0.94 | 0.94 | 8/9 |
| keyword | 1.00 | 0.93 | 9/9 |
| hybrid | 0.94 | 0.94 | 8/9 |

## Per scenario (recall@k / reciprocal rank)

| scenario | expected | vector | keyword | hybrid |
|---|---|---|---|---|
| bad_deploy | runbooks/bad_deploy, postmortems/2026-02-fanout-join-after-deploy | 0.50 / 1.00 | 1.00 / 1.00 | 0.50 / 1.00 |
| duplicate_load | runbooks/duplicate_load, postmortems/2025-12-double-loaded-orders-batch | 1.00 / 0.50 | 1.00 / 1.00 | 1.00 / 0.50 |
| flaky_noise | runbooks/oncall_general | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 |
| injection | runbooks/schema_drift, postmortems/2025-09-vendor-renamed-price-column | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 |
| late_upstream | runbooks/late_upstream, postmortems/2025-11-inventory-feed-delayed | 1.00 / 1.00 | 1.00 / 0.33 | 1.00 / 1.00 |
| null_spike | runbooks/data_quality, postmortems/2026-01-null-region-from-checkout | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 |
| permission | runbooks/permission, postmortems/2026-03-revoked-grant-after-role-cleanup | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 |
| resource | runbooks/resource, postmortems/2025-10-black-friday-timeouts | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 |
| schema_drift | runbooks/schema_drift, postmortems/2025-09-vendor-renamed-price-column | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 |

## Misses (top 5 documents per mode)

**bad_deploy** — expected runbooks/bad_deploy, postmortems/2026-02-fanout-join-after-deploy
- vector: postmortems/2026-02-fanout-join-after-deploy, runbooks/duplicate_load, postmortems/2025-10-black-friday-timeouts, postmortems/2025-12-double-loaded-orders-batch, runbooks/resource
- hybrid: postmortems/2026-02-fanout-join-after-deploy, runbooks/schema_drift, runbooks/duplicate_load, postmortems/2025-09-vendor-renamed-price-column, postmortems/2025-12-double-loaded-orders-batch
