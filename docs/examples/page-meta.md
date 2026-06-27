---
title: Page Meta — TOC & Page Properties
toc: true
confluence_properties:
  Owner: Platform Engineering
  Status: In Review
  Last reviewed: 2024-01-15
  Audience: Internal
---

# Page Meta — TOC & Page Properties

Two MkDocs frontmatter keys unlock Confluence-native features.

## Table of Contents (`toc: true`)

Add `toc: true` to a page's frontmatter to inject a Confluence **Table of
Contents** macro at the top of the page. It automatically lists headings
from H2 to H4.

```yaml
---
toc: true
---
```

The TOC macro is interactive in Confluence — readers can jump to any section.

## Page Properties (`confluence_properties`)

The `confluence_properties` key accepts a flat dictionary of key/value pairs.
These are rendered using the Confluence **Page Properties** macro (also called
the **Details** macro), making the page queryable via **Page Properties Report**
macros elsewhere in your space.

```yaml
---
confluence_properties:
  Owner: Alice
  Status: Approved
  Last reviewed: 2024-01-01
  Audience: Engineering
---
```

### Why use Page Properties?

You can create a **Page Properties Report** on a summary page to automatically
aggregate metadata from all tagged pages — building dynamic dashboards like:

- All ADRs and their statuses
- All runbooks with their owners
- All KB articles by audience

## Combining both

Both features can be used together (as in this page's frontmatter):

```yaml
---
toc: true
confluence_properties:
  Owner: Platform Engineering
  Status: In Review
---
```

## Section A

Placeholder section to demonstrate the auto-generated TOC links above.

## Section B

Another section for the TOC.

### Subsection B.1

The TOC includes H3 headings too (up to H4 by default).
