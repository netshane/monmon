# Unit Tests

These are the core unit tests for the datapipeline project.  They can be run to ensure that the core functionality of the project is working as expected.

These are intended to be run after all development to ensure no unexpected dependencies have changed.

Marks for tests are configured the `pyproject.toml` file.


Add additional folders under tests for integration and external tests.


## Running tests

```sh
# run all tests
python -m pytest .\tests\unit\

# single test
python -m pytest .\tests\unit\ -k "test_sample"

python -m pytest .\tests\unit\test_main.py -k "test_sample"

# show stdout
python -m pytest -s .\tests\unit\test_main.py -k "test_sample"

# run not slow unit tests
python -m pytest .\tests\unit\ -m "unit and not slow"
```
