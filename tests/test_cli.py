import json
import logging
import os
import sys

import mock
import pytest
import yaml

from ipapp import BaseApplication, BaseConfig
from ipapp.cli import Args, _parse_argv, _setup_logging, main


def test_cli_success():
    args = _parse_argv(
        'progname', ['--log-level', 'INFO', '--log-file', '/tmp/sgsfgsdfg']
    )
    assert not args.version
    assert args.log_level == 'INFO'
    assert args.log_file == '/tmp/sgsfgsdfg'


def test_cli_bad_log_level(capsys):
    with pytest.raises(SystemExit):
        _parse_argv(
            'progname',
            ['--log-level', 'SUCCESS', '--log-file', '/tmp/sgsfgsdfg'],
        )
    out, err = capsys.readouterr()
    assert '--log-level' in err
    assert 'SUCCESS' in err


def test_cli_success_show_version():
    args = _parse_argv('progname', ['-v'])
    assert args.version


def test_cli_setup_logging():
    with mock.patch('logging.basicConfig'):
        level = logging.getLevelName(logging.ERROR)

        args = Args(
            version=False,
            autoreload=False,
            env_prefix='APP_',
            show_config=None,
            log_level=level,
            log_file=None,
            config=None,
        )
        _setup_logging(args)
        logging.basicConfig.assert_called_once_with(level=logging.ERROR)

    with mock.patch('logging.basicConfig'):
        level = logging.getLevelName(logging.ERROR)
        args = Args(
            version=False,
            autoreload=False,
            env_prefix='APP_',
            show_config=None,
            log_level=level,
            log_file='/tmp/qwerty',
            config=None,
        )
        _setup_logging(args)
        logging.basicConfig.assert_called_once_with(
            level=logging.ERROR, filename='/tmp/qwerty'
        )


def test_cli_main(capsys):
    class App(BaseApplication):
        created = False
        ran = False

        def __init__(self, cfg: BaseConfig):
            super().__init__(cfg)
            App.created = True

        def run(self):
            App.ran = True
            return 0

    class Cfg(BaseConfig):
        db: str = ''

    old_argv = sys.argv
    old_env = os.environ
    try:
        argv = ['run.py', '-v']
        assert main(argv, '1.2.3.4', App, Cfg) == 0
        out, err = capsys.readouterr()
        assert '1.2.3.4' in out
        assert not App.created
        assert not App.ran

        argv = ['run.py', '--show-config', 'json']
        assert main(argv, '1.2.3.4', App, Cfg) == 0
        out, err = capsys.readouterr()
        cfg = json.loads(out)
        assert 'db' in cfg
        assert not App.created
        assert not App.ran

        argv = ['run.py', '--show-config', 'yaml']
        assert main(argv, '1.2.3.4', App, Cfg) == 0
        out, err = capsys.readouterr()
        cfg = yaml.safe_load(out)
        assert 'db' in cfg
        assert not App.created
        assert not App.ran

        argv = ['run.py']
        assert main(argv, '1.2.3.4', App, Cfg) == 0
        assert App.created
        assert App.ran

    finally:
        sys.argv = old_argv
        os.environ = old_env
