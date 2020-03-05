import argparse
import logging
import sys
from typing import List, NamedTuple, Optional, Type

from .app import BaseApplication
from .config import BaseConfig
from .error import ConfigurationError


class Args(NamedTuple):
    version: bool
    autoreload: bool
    env_prefix: str
    show_config: Optional[str]
    log_level: str
    log_file: Optional[str]
    config: Optional[str]


def _parse_argv(
    prog: str, options: list, default_env_prefix: str = 'APP_'
) -> Args:
    parser = argparse.ArgumentParser(prog=prog)

    parser.add_argument(
        '-c',
        '--config',
        dest='config',
        type=str,
        help='Path to configuration file in JSON format',
    )

    parser.add_argument(
        '--env-prefix',
        dest='env_prefix',
        default=default_env_prefix,
        type=str,
        help='Environment variables prefix',
    )

    parser.add_argument(
        '--show-config',
        dest='show_config',
        type=str,
        choices=['env', 'json', 'yaml'],
        help='Show parsed configuration and exit',
    )

    parser.add_argument(
        '-v',
        '--version',
        action="store_true",
        default=False,
        help="output version information and exit",
    )

    parser.add_argument(
        '--autoreload',
        action="store_true",
        default=False,
        help="Automatically restart the service when a source file(s) "
        "is modified.",
    )

    parser.add_argument(
        '--log-level',
        dest='log_level',
        type=str,
        default='INFO',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
        help='Logging level',
    )

    parser.add_argument(
        '--log-file', dest='log_file', type=str, help='Logging file name',
    )
    parsed = parser.parse_args(args=options)
    return Args(
        config=parsed.config,
        env_prefix=parsed.env_prefix,
        show_config=parsed.show_config,
        version=parsed.version,
        autoreload=parsed.autoreload,
        log_level=parsed.log_level,
        log_file=parsed.log_file,
    )


def _setup_logging(options: Args) -> None:
    config = dict(level=getattr(logging, options.log_level))
    if options.log_file:
        config["filename"] = options.log_file
    logging.basicConfig(**config)


def load_config(options: Args, cfg_cls: Type[BaseConfig]) -> BaseConfig:
    if options.config:
        lcfg = options.config.lower()
        if lcfg.endswith('.yml') or lcfg.endswith('.yaml'):
            return cfg_cls.from_yaml(options.config)
        elif lcfg.endswith('.json'):
            return cfg_cls.from_json(options.config)
        else:
            raise ConfigurationError(
                'Extension of Configuration file must be one of: '
                '.json, .yml, .yaml'
            )
    else:
        return cfg_cls.from_env(prefix=options.env_prefix)


def _show_config(options: Args, cfg: BaseConfig) -> None:
    if options.show_config == 'env':
        print(
            "\n".join(
                [
                    '%s%s=%s' % (options.env_prefix, k, v)
                    for k, v in cfg.to_env().items()
                ]
            )
        )
    elif options.show_config == 'json':
        cfg.to_json(sys.stdout)
    elif options.show_config == 'yaml':
        cfg.to_yaml(sys.stdout)
    else:  # pragma: no cover
        raise UserWarning


def main(
    argv: List[str],
    version: str,
    app_cls: Type[BaseApplication],
    cfg_cls: Type[BaseConfig],
    *,
    default_env_prefix: str = 'APP_',
    build_stamp: Optional[float] = None,
) -> int:
    try:
        prog, args = argv[0], argv[1:]
        options = _parse_argv(prog, args, default_env_prefix)
        if options.version:
            print(version)
            return 0
        if options.autoreload:
            import ipapp.autoreload

            ipapp.autoreload.start()
        _setup_logging(options)
        cfg = load_config(options, cfg_cls)
        if options.show_config:
            _show_config(options, cfg)
            return 0
        app = app_cls(cfg)
        app._version = version
        if build_stamp is not None:
            app._build_stamp = build_stamp
        return app.run()
    except KeyboardInterrupt:  # pragma: no cover
        return 0
