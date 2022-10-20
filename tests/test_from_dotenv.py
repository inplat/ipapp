import os

from examples.from_dotenv import Config


def test_from_env(capsys) -> None:
    _o = object()
    old = os.environ.get("APP_MSG_INDEX", _o)
    os.environ["APP_MSG_INDEX"] = "TEST_ENV"

    config: Config = Config.from_env(prefix="app_")
    assert config.msg.index == "TEST_ENV"

    if old is _o:
        del os.environ["APP_MSG_INDEX"]
    else:
        os.environ["APP_MSG_INDEX"] = old

    capsys.readouterr()  # hide stdout


def test_from_dotenv(capsys, pytestconfig) -> None:
    config: Config = Config.from_env(
        prefix="app_",
        env_file=str(pytestconfig.rootpath / 'examples' / 'from_dotenv.env'),
    )

    assert config.msg.index == "TEST_DOT"

    capsys.readouterr()  # hide stdout


def test_from_env_and_dot(capsys, pytestconfig) -> None:
    _o = object()
    old = os.environ.get("APP_MSG_INDEX", _o)
    os.environ["APP_MSG_INDEX"] = "TEST_ENV_P"

    config: Config = Config.from_env(
        prefix="app_",
        env_file=str(pytestconfig.rootpath / 'examples' / 'from_dotenv.env'),
    )
    assert config.msg.index == "TEST_ENV_P"

    if old is _o:
        del os.environ["APP_MSG_INDEX"]
    else:
        os.environ["APP_MSG_INDEX"] = old

    capsys.readouterr()  # hide stdout
