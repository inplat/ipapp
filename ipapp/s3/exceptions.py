class BaseError(Exception):
    pass


class FileTypeNotAllowedError(BaseError):
    pass


class ErrorMoveFile(BaseError):
    pass
