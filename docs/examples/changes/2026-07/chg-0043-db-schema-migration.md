---
title: "CHG-0043 — User Table Schema Migration"
confluence_properties:
  Change ID: CHG-0043
  Date: 2026-07-22
  Window: "01:00–03:00 UTC"
  Owner: David Park
  Risk: Medium
  Status: Pending Approval
  Systems Affected: "User Service, Auth Service, Admin Dashboard"
  Approver: ""
  CAB Meeting: ""
labels: [change-management, database, medium-risk]
---

# CHG-0043 — User Table Schema Migration

## Summary

Adding a `preferred_name` column to the `users` table and backfilling
existing rows. Required for the Q3 personalisation feature.

## Change details

**Scheduled date:** 2026-07-22  
**Change window:** 01:00–03:00 UTC  
**Risk level:** Medium — additive schema change, no column removal  
**Owner:** David Park  
**Approver:** TBD (pending CAB)

## Scope

- PostgreSQL `users` table — `ALTER TABLE` to add nullable column
- `user-service` — updated model and API serialisation
- `auth-service` — updated JWT claims to include `preferred_name`
- `admin-dashboard` — new field in user profile editor

## Pre-change checklist

- [x] Migration script reviewed by DBA
- [x] Tested on staging with production data snapshot
- [x] Estimated migration time: < 5 seconds (no row lock on nullable add)
- [ ] CAB approval
- [ ] On-call engineer notified

## Implementation steps

1. Run `ALTER TABLE users ADD COLUMN preferred_name VARCHAR(255)`;
2. Deploy `user-service` v2.4.0
3. Deploy `auth-service` v1.9.0
4. Deploy `admin-dashboard` v3.1.0
5. Run backfill job (sets `preferred_name = display_name` for existing rows)
6. Verify via smoke tests

## Rollback plan

**Trigger criteria:** Any service returning 5xx at > 1% error rate post-deploy.

**Steps:**
1. Roll back `user-service`, `auth-service`, `admin-dashboard` to previous versions
2. Column can remain in schema — it is nullable and ignored by old code
3. Column removal scheduled for next maintenance window if needed

## Post-change validation

- [ ] `user-service` health check passing
- [ ] `auth-service` health check passing
- [ ] JWT tokens contain `preferred_name` field
- [ ] Admin dashboard shows new field correctly
- [ ] No increase in error rates

??? note "Schema migration approach"
    We chose a nullable column with no default so the `ALTER TABLE`
    completes instantly without locking the table. The backfill job runs
    asynchronously after deployment and is safe to re-run if interrupted.
