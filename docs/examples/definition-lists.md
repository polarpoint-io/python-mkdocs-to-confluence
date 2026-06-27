---
title: Definition Lists
---

# Definition Lists

Definition lists (`term\n:   definition`) are converted to HTML `<dl>/<dt>/<dd>`
elements, which Confluence storage format renders natively.

## Glossary example

API
:   Application Programming Interface. A set of rules that allows different
    software applications to communicate with each other.

REST
:   Representational State Transfer. An architectural style for distributed
    hypermedia systems.

XHTML
:   Extensible HyperText Markup Language. The format used by Confluence's
    storage format for page content.

CQL
:   Confluence Query Language. Used to search for pages, blogs, and other
    content in a Confluence space.

## Configuration options

`host_url`
:   The base URL of your Confluence instance's REST API.
    Example: `https://yourorg.atlassian.net/wiki/rest/api/content`

`space`
:   The Confluence space key where pages will be published.
    Example: `ENG`

`parent_page_name`
:   The title of the root parent page. Supports nested paths separated by `/`.
    Example: `Engineering / Docs`

`dryrun`
:   When `true`, the plugin logs all actions but makes no changes to Confluence.
    Useful for verifying what would be published before running for real.

## HTTP status codes

200 OK
:   The request succeeded. Page was found or updated.

201 Created
:   The resource was created. A new page was successfully published.

404 Not Found
:   The requested page does not exist.

409 Conflict
:   A page with that title already exists under the same parent.
