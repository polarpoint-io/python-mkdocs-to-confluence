---
title: Admonitions & Collapsible Sections
---

# Admonitions & Collapsible Sections

The plugin converts MkDocs admonition blocks into native Confluence macros.
No extra configuration is required.

## Always-visible admonitions (`!!!`)

### Note

!!! note "Remember"
    This is a note. It renders as a Confluence **note** (yellow) macro.

### Info

!!! info "Good to know"
    Informational content. Renders as a Confluence **info** (blue) macro.

### Tip

!!! tip "Pro tip"
    Helpful hints. Renders as a Confluence **tip** (green) macro.

### Warning

!!! warning "Watch out"
    Something might go wrong. Renders as a Confluence **warning** (red) macro.

### Danger (also maps to warning)

!!! danger "Critical"
    Data loss risk. Also renders as a Confluence **warning** macro.

### Admonition without a custom title

The type name is used as the title automatically.

!!! success
    Task completed successfully.

## Collapsible sections (`???`)

Use `???` for a collapsed-by-default section (Confluence **expand** macro).

??? note "Click to expand — Implementation details"
    These details are hidden until the reader expands the section.

    You can include:

    - Bullet lists
    - Code examples
    - Any other Markdown

Use `???+` to start the section open.

???+ tip "Advanced options (expanded by default)"
    This section starts open but can be collapsed.

## Admonition with multi-paragraph body

!!! info "Multi-paragraph"
    First paragraph with some content.

    Second paragraph with more detail. The blank line between them is
    preserved inside the Confluence macro body.
