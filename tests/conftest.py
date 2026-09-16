def pytest_addoption(parser):
    parser.addoption("--update-goldens", action="store_true", help="rewrite tests/scenarios/*.golden.txt")
