# Tests

To perform all below operations please make sure that you are in the root folder of the repository.

To execute tests you need to have `pytest` and `pytest-mock`. Please refer to the [requirements.txt](../requirements.txt)

To execute tests run:

```shell
python -m pytest tests/
```

# Coverage

To generate coverage run:

```shell
coverage run --source=src/etl -m pytest -vv tests/
coverage report
```

# Code quality

## Code formatting and linting

To be consistent with code quality we are using [ruff](https://docs.astral.sh/ruff/).

To format the code run:

```shell
ruff format .
```

To use linter run:

```shell
ruff check .
```

## Type checking

To check correct type hinting [mypy](https://mypy-lang.org/) is used.

To perform type check run:
```shell
mypy src/
```