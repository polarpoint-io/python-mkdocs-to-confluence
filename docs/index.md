# python-repository-template
A template for a Python application repository.

---

## Installation

To install the project dependencies, use the following command:

```shell
pip install .
```

## Tests

To perform the following operations, please make sure that you are in the root folder of the repository.

To execute tests, run:

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

To be consistent with code quality we are using [black](https://black.readthedocs.io/).

To format the code run:

```shell
black .
```

To lint the code, run:

```shell
ruff check .
```
