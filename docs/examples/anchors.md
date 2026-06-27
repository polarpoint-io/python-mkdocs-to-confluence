---
title: Heading Anchors
---

# Heading Anchors

Add `{#anchor-id}` after any heading to inject a Confluence **anchor** macro.
This lets you link directly to that section from other pages or from external
systems.

## Usage

```markdown
## My Section {#my-section}
```

Becomes:

```xml
<ac:structured-macro ac:name="anchor">
  <ac:parameter ac:name="anchorName">my-section</ac:parameter>
</ac:structured-macro>
<h2>My Section</h2>
```

You can then link to it from another Confluence page with:

```
[Jump to My Section](./this-page#my-section)
```

## Examples on this page

### Architecture Overview {#arch-overview}

This section has the anchor `arch-overview`.

### API Reference {#api-ref}

This section has the anchor `api-ref`.

#### Authentication endpoint {#auth-endpoint}

Anchors work on H4 headings too.

## When to use anchors

Anchors are most useful when:

- You want stable deep-links into long documentation pages
- You're cross-referencing sections from a README, ticket, or another Confluence page
- You're maintaining a glossary with per-term anchors
- You want to link from a Jira ticket directly to the relevant runbook section
