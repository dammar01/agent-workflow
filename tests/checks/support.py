"""The assertion helper shared by every checks/ module."""


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
