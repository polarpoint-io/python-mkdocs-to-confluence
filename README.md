# MkDocs Confluence Plugin

**Version:** 1.26.0  
**Python:** >=3.7  

A MkDocs plugin that automatically publishes your documentation to Confluence, with advanced navigation matching and semantic page resolution.

## Features

- **Automatic publishing** - Seamlessly export your MkDocs documentation to Confluence
- **Navigation matching** - Matching between MkDocs navigation and documentation pages, including:
  - Support for complex nested navigation structures
  - Context-aware matching for pages in subdirectories
  - Semantic matching with abbreviation expansion (e.g., "ADRs" → "Architecture Design Records")
  - Fuzzy matching as fallback for edge cases
  - YAML front matter title recognition
- **Flexible configuration** - Extensive configuration options for Confluence integration
- **Dry-run mode** - Test your configuration without publishing
- **Debug mode** - Detailed logging for troubleshooting
- **Footer support** - Optional GitHub edit links and auto-generation notices
- **Folder structure preservation** - Maintains your documentation hierarchy in Confluence

## Installation

### Install from Source

```shell
pip install .
```

### Development Installation

For development with optional dependencies:

```shell
pip install -e ".[dev]"
```

### Build from Source

Using modern Python build tools:

```shell
python -m build
pip install dist/mkdocs_confluence_plugin-*.whl
```

### Additional MkDocs Plugins (Optional)

Install additional MkDocs plugins as needed for your documentation:

```shell
# Popular MkDocs plugins for enhanced documentation
pip install mkdocs-material mkdocs-awesome-nav
pip install mkdocs-build-plantuml-plugin mkdocs-git-revision-date-localized-plugin
```

## Python Requirements

- **Python:** >=3.7
- **Build System:** setuptools>=61, wheel, build

## Dependencies

### Core Dependencies (from pyproject.toml)
- **mkdocs** - The static site generator this plugin extends
- **atlassian-python-api** - Confluence API client  
- **md2cf** - Markdown to Confluence markup converter
- **mistune** - Markdown parser
- **requests** - HTTP library for API calls
- **pyyaml==6.0** - YAML parsing library
- **mime** - MIME type detection

### Testing Dependencies
- **pytest==8.0.0** - Testing framework
- **pytest-mock==3.12.0** - Mocking utilities for tests
- **coverage==7.5** - Code coverage analysis
- **pre-commit** - Git hook management

### Development Dependencies (Optional)
Install with: `pip install -e ".[dev]"`
- **black** - Code formatting
- **mkdocs** - For local testing
- **pytest** - Testing framework
- **coverage** - Coverage reporting
- **md2cf** - Markdown conversion
- **atlassian-python-api** - Confluence integration

### Recommended MkDocs Plugins
These plugins work well with the Confluence plugin but are installed separately:
- **mkdocs-awesome-nav** - Advanced navigation management with `.nav.yml` files
- **mkdocs-material** - Modern Material Design theme
- **mkdocs-build-plantuml-plugin** - PlantUML diagram support
- **mkdocs-git-revision-date-localized-plugin** - Git-based page timestamps

## Configuration

The plugin is automatically registered as a MkDocs plugin via the entry point:
```
confluence = "mkdocs_confluence_plugin.plugin:ConfluencePlugin"
```

Add the plugin to your `mkdocs.yml` configuration:

```yaml
plugins:
  - awesome-nav:
      filename: ".nav.yml"
  - confluence:
      host_url: https://your-domain.atlassian.net/wiki/rest/api/content
      space: YOUR_SPACE_KEY
      parent_page_name: 'Documentation Root'
      github_base_url: "https://github.com/your-org/your-repo/blob/main"
      enable_footer: true
      enabled_if_env: MKDOCS_TO_CONFLUENCE
      dryrun: false
      debug: true
      verbose: true
```

### Configuration Options

| Option | Description | Default | Required |
|--------|-------------|---------|----------|
| `host_url` | Confluence API endpoint URL | | ✅ |
| `space` | Confluence space key | | ✅ |
| `parent_page_name` | Parent page name in Confluence | | ✅ |
| `github_base_url` | Base URL for GitHub edit links | | |
| `enable_footer` | Add footer with edit links | `false` | |
| `enabled_if_env` | Environment variable to enable plugin | | |
| `dryrun` | Test mode without publishing | `false` | |
| `debug` | Enable debug logging | `false` | |
| `verbose` | Enable verbose output | `false` | |

## Usage

### Basic Usage

1. Configure the plugin in your `mkdocs.yml`
2. Set up environment variables for Confluence authentication:
   ```bash
   export CONFLUENCE_USERNAME=your-email@domain.com
   export CONFLUENCE_PASSWORD=your-api-token
   export MKDOCS_TO_CONFLUENCE=1
   ```
3. Build and publish your documentation:
   ```bash
   mkdocs build
   ```

### Using with mkdocs-awesome-nav

For complex navigation structures, use `mkdocs-awesome-nav` with a `.nav.yml` file:

```yaml
# docs/.nav.yml
nav:
  - Index: index.md

  - Support:
      - support/*.md
      - support/**/*.md

  - Technical-Practices:
      - Architecture Design Records:
          - technical-practices/architecture_design_records/*.md
          - technical-practices/architecture_design_records/**/*.md
      - Code-Maintainability:
          - technical-practices/code-maintainability/*.md
          - technical-practices/code-maintainability/**/*.md
      - Continuous-Delivery:
          - technical-practices/continuous-delivery/*.md
          - technical-practices/continuous-delivery/**/*.md
      - Monitoring-Observability:
          - technical-practices/monitoring-observability/*.md
          - technical-practices/monitoring-observability/**/*.md

  - Template Files:
      - template_files/*.md
      - template_files/**/*.md
```

### Environment Setup

Set up the required environment variables for Confluence authentication and plugin configuration:

```bash
# Required for Confluence authentication
export CONFLUENCE_USERNAME="your-email@domain.com"
export CONFLUENCE_PASSWORD="your-confluence-api-token"

# Plugin enablement
export MKDOCS_TO_CONFLUENCE=1

# Optional: Override configuration via environment variables
export host_url="https://your-domain.atlassian.net/rest/api/content"
export space_key="YOUR_SPACE_KEY"
export parent_page_name="Documentation Root"
export enable_footer="true"
export dryrun="false"
export debug="true"
export verbose="true"
```

**Required Environment Variables:**
- `CONFLUENCE_USERNAME` - Your Confluence/Atlassian email
- `CONFLUENCE_PASSWORD` - Your Confluence API token (not your login password)
- `MKDOCS_TO_CONFLUENCE` - Set to `1` or `true` to enable the plugin

### Dry Run Mode

Test your configuration without publishing to Confluence:

```yaml
plugins:
  - confluence:
      # ... other config ...
      dryrun: true
      debug: true
      verbose: true
```

## Testing

### Run All Tests

Tests are configured via pyproject.toml with optimized settings:

```shell
# Run tests with project settings (maxfail=1, no warnings, quiet)
python -m pytest tests/

# Or run with verbose output
python -m pytest tests/ -v
```

**Test Configuration (from pyproject.toml):**
- Test directory: `tests/`
- Max failures: 1 (stops after first failure)
- Warnings disabled for cleaner output
- Quiet mode by default

### Run Specific Test Categories

```shell
# Navigation matching tests
python -m pytest tests/test_navigation_matching.py -v

# Similarity and semantic matching tests  
python -m pytest tests/test_similarity.py -v

# Title-based matching tests
python -m pytest tests/test_title_based_matching.py -v

# Folder structure tests
python -m pytest tests/test_folder_titles.py -v

# Nested navigation tests
python -m pytest tests/test_nested_matching.py -v
```

### Coverage Report

The project is configured for comprehensive coverage reporting with a minimum threshold:

```shell
# Generate coverage report (configured in pyproject.toml)
coverage run --source=src -m pytest -vv tests/
coverage report  # Shows missing lines, fails if under 30% coverage
coverage html    # Generate HTML report
```

**Coverage Settings (from pyproject.toml):**
- Branch coverage enabled
- Source directory: `src`
- Minimum coverage: 30%
- Shows missing lines in reports

### Debug Scripts

The project includes debug scripts for troubleshooting navigation matching:

```shell
# Debug navigation matching step-by-step
python debug_step.py

# Debug word extraction and similarity
python debug_words.py

# Debug page collection logic
python debug_collect.py

# Debug navigation flattening
python debug_flatten.py
```

## Code Quality

### Code Formatting

We use [Black](https://black.readthedocs.io/) for consistent code formatting:

```shell
black .
```

### Linting

We use [ruff](https://docs.astral.sh/ruff/) for fast, comprehensive linting:

```shell
ruff check .
```

### Pre-commit Hooks

Install pre-commit hooks for automatic code quality checks:

```shell
pre-commit install
```

## Development

### Local Development Setup

1. Clone the repository
2. Install the package with development dependencies: `pip install -e ".[dev]"`
3. Install additional MkDocs plugins if needed: `pip install mkdocs-material mkdocs-awesome-nav`
4. Set up environment variables:
   ```bash
   export CONFLUENCE_USERNAME=your-email@domain.com
   export CONFLUENCE_PASSWORD=your-api-token
   export MKDOCS_TO_CONFLUENCE=1
   ```
5. Run tests: `python -m pytest tests/`

### Build System

The project uses modern Python packaging with `pyproject.toml`:

```shell
# Build distribution packages
python -m build

# Install built package
pip install dist/mkdocs_confluence_plugin-*.whl
```

**Build Configuration:**
- Build system: setuptools>=61, wheel, build
- Package discovery: automatic from `src/` directory
- Entry point: `confluence = "mkdocs_confluence_plugin.plugin:ConfluencePlugin"`

### Versioning

The project uses semantic versioning with automated releases:
- Version managed in `pyproject.toml`
- Semantic release configured for automated version bumps
- Current version: 1.26.0

### Testing Your Changes

1. Run the full test suite: `python -m pytest tests/ -v`
2. Test with a real MkDocs build: `mkdocs build -f mkdocs-test.yml`
3. Use dry-run mode to test Confluence integration without publishing

### Contributing

1. Ensure all tests pass
2. Format code with Black: `black .`
3. Check linting with ruff: `ruff check .`
4. Add tests for new functionality
5. Update documentation as needed

## Architecture

The plugin provides sophisticated navigation matching through:

- **Semantic word extraction** - Extracts meaningful words from navigation entries and page paths
- **Abbreviation expansion** - Recognizes and expands common abbreviations (e.g., "ADRs" → "Architecture Design Records")
- **Context-aware matching** - Uses folder context and parent page information for better matching
- **Multi-stage matching** - Title-based, semantic, and fuzzy matching with configurable thresholds
- **Robust error handling** - Graceful degradation and comprehensive logging


