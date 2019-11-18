class Error(Exception):
    pass


class GracefulExit(SystemExit):
    pass


class PrepareError(Error):
    pass


class ConfigurationError(Error):
    pass
