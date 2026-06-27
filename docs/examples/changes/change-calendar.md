---
title: Forward Calendar of Change
confluence_page_properties_report:
  label: change-management
  headings:
    - Change ID
    - Date
    - Window
    - Owner
    - Risk
    - Status
    - Systems Affected
  sort_by: Date
  reverse_sort: false
  max: 100
---

# Forward Calendar of Change

This page is auto-generated. All upcoming and recent changes are pulled
automatically from individual change request pages tagged with the
`change-management` label.

The table above is a live Confluence **Page Properties Report** — it updates
each time a change page is published or modified.

## How to add a change

Create a new markdown file under `docs/changes/YYYY-MM/` following the
[change request template](chg-template.md). The page will appear in the
calendar automatically on the next `mkdocs build`.

## Risk legend

| Risk | Meaning |
|------|---------|
| Low | Routine, well-tested, easily reversible |
| Medium | Some complexity or customer-facing impact possible |
| High | Significant impact or limited rollback window |
| Critical | Major outage risk — CAB approval required |

## Status workflow

`Draft` → `Pending Approval` → `Approved` → `In Progress` → `Completed` / `Rolled Back`
