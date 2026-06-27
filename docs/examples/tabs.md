---
title: Tabbed Content
---

# Tabbed Content

MkDocs Material's `=== "Label"` tab blocks are converted to Confluence
**expand** macros — one expand panel per tab. Readers click to reveal each
section's content.

## Installation options

=== "pip"
    Install directly from PyPI:

    ```bash
    pip install mkdocs-confluence-plugin
    ```

=== "poetry"
    Add to your project with Poetry:

    ```bash
    poetry add mkdocs-confluence-plugin
    ```

=== "uv"
    Install with uv:

    ```bash
    uv add mkdocs-confluence-plugin
    ```

## Language examples

=== "Python"
    ```python
    from mkdocs_confluence_plugin import ConfluencePlugin
    ```

    The plugin is automatically discovered by MkDocs when installed.

=== "JavaScript"
    ```javascript
    // No JS integration needed — the plugin runs server-side during `mkdocs build`.
    console.log("Build complete!");
    ```

=== "Go"
    ```go
    // This plugin is Python-only. Call mkdocs from your Go pipeline:
    // exec.Command("mkdocs", "build").Run()
    ```

## Deployment approaches

=== "GitHub Actions"
    ```yaml
    - name: Publish to Confluence
      env:
        CONFLUENCE_USERNAME: ${{ secrets.CONFLUENCE_USERNAME }}
        CONFLUENCE_PASSWORD: ${{ secrets.CONFLUENCE_PASSWORD }}
        PUBLISH_TO_CONFLUENCE: "1"
      run: mkdocs build
    ```

=== "GitLab CI"
    ```yaml
    publish:
      script:
        - PUBLISH_TO_CONFLUENCE=1 mkdocs build
      variables:
        CONFLUENCE_USERNAME: $CONFLUENCE_USERNAME
        CONFLUENCE_PASSWORD: $CONFLUENCE_PASSWORD
    ```

=== "Local"
    ```bash
    export CONFLUENCE_USERNAME=you@example.com
    export CONFLUENCE_PASSWORD=your-api-token
    export PUBLISH_TO_CONFLUENCE=1
    mkdocs build
    ```
