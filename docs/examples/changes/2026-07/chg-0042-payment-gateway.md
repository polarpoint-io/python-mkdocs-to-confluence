---
title: "CHG-0042 — Payment Gateway v3 to v4 Upgrade"
confluence_properties:
  Change ID: CHG-0042
  Date: 2026-07-15
  Window: "22:00–02:00 UTC"
  Owner: Alice Chen
  Risk: High
  Status: Approved
  Systems Affected: "Payment Gateway, Checkout API, Order Service"
  Approver: Bob Smith
  CAB Meeting: 2026-07-10
labels: [change-management, high-risk, payment]
---

# CHG-0042 — Payment Gateway v3 to v4 Upgrade

## Summary

Upgrading the payment gateway client library from v3.8.2 to v4.1.0 to gain
support for 3DS2 authentication and address the deprecation of the v3 API
scheduled for 2026-09-01 by our payment provider.

## Change details

**Scheduled date:** 2026-07-15  
**Change window:** 22:00–02:00 UTC (4-hour window)  
**Risk level:** High — payment flow is business-critical  
**Owner:** Alice Chen  
**Approver:** Bob Smith  
**CAB Meeting:** 2026-07-10

## Scope

- `payment-gateway-service` — library upgrade + config changes
- `checkout-api` — updated integration contract for 3DS2 flow
- `order-service` — updated webhook handling for new event schema

## Pre-change checklist

- [x] Change tested in staging for 5 business days
- [x] Load test run against staging with production traffic replay
- [x] Rollback plan documented and rehearsed
- [x] On-call engineer (David) notified and briefed
- [x] Payment ops team informed — monitoring dashboards ready
- [ ] Final CAB sign-off (2026-07-10)

## Implementation steps

1. Enable maintenance mode on checkout flow (error page for new sessions only)
2. Deploy `payment-gateway-service` v4.1.0 to production (blue/green)
3. Run smoke tests against new deployment
4. Shift 10% of traffic to new deployment — monitor for 15 minutes
5. Shift remaining 90% of traffic
6. Disable maintenance mode
7. Monitor for 30 minutes before declaring success

## Rollback plan

**Trigger criteria:** error rate on payment flow exceeds 0.5% for 5 consecutive
minutes, or any failed transaction that cannot be attributed to user error.

**Steps:**
1. Shift all traffic back to v3 deployment (blue/green swap — ~2 min)
2. Disable v4 deployment
3. Notify payment ops and engineering on-call
4. Open post-incident review

## Post-change validation

- [ ] Payment success rate ≥ 99.5% over 30-minute window
- [ ] Latency p99 within 10% of baseline
- [ ] No increase in chargebacks or fraud signals
- [ ] 3DS2 flow tested end-to-end with test cards
- [ ] Stakeholders notified of completion

!!! warning "High-risk change"
    This change affects the live payment flow. The on-call engineer must
    remain available for the full 4-hour window. Escalation contact:
    Head of Payments — Alice Chen (+44 7700 900000).

## Related links

- [Jira ticket: PAY-1234](https://jira.example.com/PAY-1234)
- [Payment gateway v4 migration guide](https://docs.provider.example.com/v4-migration)
- [Staging test results](https://confluence.example.com/staging-results)
- [Runbook: payment gateway rollback](../runbooks/payment-gateway-rollback.md)
