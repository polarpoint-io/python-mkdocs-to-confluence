---
tags:
  - guide
  - user
  - githubactions
  - python
---

[WHAT](#what) [WHY](#why) [HOW](#how) [NOTES & REFS](#notes--refs)

## **WHAT**

___

**AS AN** Engineer  
**I WANT TO** Use the Python template  
**SO THAT** my Python application conforms to our standards  

---

## **WHY**

___

- Promotes code reusability and helps maintain consistency across projects.  
- Ensures consistent CI/CD practices and code quality across repositories.  

---

## **HOW**

___

### **Prerequisites**

Before proceeding, make sure you have the following prerequisites:

✅ You can log into our GitHub at [https://github.com/NewDay-Data](https://github.com/NewDay-Data)  
✅ Git installed on your local machine (download and install it from [https://git-scm.com](https://git-scm.com))  
✅ Python 3.11 or later installed on your local machine  

---

### **Procedure**

Shared GitHub Actions allow you to reuse predefined actions across different workflows and repositories. This promotes code reusability and helps maintain consistency across projects. Within our GitHub instance, we have multiple repository templates.

To use the Python repository template and create a new repository based on it, perform the following steps:

1. Go to our GitHub instance at [https://github.com/NewDay-Data](https://github.com/NewDay-Data)  
2. Click **New repository** → **Create repository from template**  
3. Select the **Python repository template**  
   - Add a name and description of your project or repository  
   - Click **Create**  

4. **Update `github/workflows/python-app.yaml`**  
   
Set the `name` for your application in the following places:

```yaml
name: Python CI

permissions:
  id-token: write
  contents: write
  pull-requests: write
  actions: read
  security-events: write

on:
  workflow_dispatch:
  workflow_call:
  pull_request:
    types: [opened, synchronize, reopened]
  push:
    branches:
      - 'main'

jobs:
  ci:
    uses: NewDay-Data/shared-github-actions/.github/workflows/python-ci.yaml@v1
    with:
      source_dir: 'src'
      tests_dir: 'tests'
  cd:
    needs: ci
    uses: NewDay-Data/shared-github-actions/.github/workflows/python-cd.yaml@v1
    with:
      artifact_name: python-repository-template
      repository_name: nexus_pypi
```

5. **Set the project metadata in `pyproject.toml`**  

Open `pyproject.toml` and update the project metadata to match your application details:

```toml
[project]
name = "python-repository-template"
version = "0.1.0"
description = "Template for Python projects"
authors = [
    { name = "Your Name", email = "you@example.com" }
]
dependencies = [
    "requests",
    "pyyaml==6.0"
]

[project.optional-dependencies]
dev = [
    "pytest",
    "pytest-mock",
    "black",
    "ruff",
    "mypy",
    "coverage"
]
```


6. **Set up Semantic Release**  

Update the `.releaserc` file to reflect the new `pyproject.toml` structure:

```json
{
  "branches": "main",
  "debug": "true",
  "plugins": [
    "@semantic-release/commit-analyzer",
    "@semantic-release/release-notes-generator",
    [
      "@semantic-release/changelog",
      {
        "changelogFile": "CHANGELOG.md",
        "changelogTitle": "# Semantic Versioning Changelog"
      }
    ],
    [
      "@semantic-release/git",
      {
        "message": "chore(release): ${nextRelease.version} [skip ci]\n\n${nextRelease.notes}",
        "assets": ["README.md","CHANGELOG.md","pyproject.toml"]
      }
    ],
    "@semantic-release/github"
  ]
}


!!! note 

    The `name` in `pyproject.toml` for your application should match the `artifact_name` in `github/workflows/python-app.yaml`.

## **NOTES & REFS**
___

- [GitHub Actions Documentation](https://docs.github.com/en/actions)
- [GitHub Actions Marketplace](https://github.com/marketplace?type=actions)
- [GitHub Actions Workflow Syntax](https://docs.github.com/en/actions/reference/workflow-syntax-for-github-actions)
