import os
from typing import Any, Dict, Mapping, Optional, Union

from pydantic.env_settings import BaseSettings, SettingsError
from pydantic.fields import SHAPE_LIST

__all__ = ("BaseConfig",)


class BaseConfig(BaseSettings):
    def get_environ(self) -> Mapping[str, str]:
        return os.environ

    def _build_environ(self) -> Dict[str, Optional[str]]:
        """
        Build environment variables suitable for passing to the Model.
        """
        d: Dict[str, Optional[Any]] = {}

        if self.__config__.case_sensitive:
            env_vars: Mapping[str, str] = self.get_environ()
        else:
            env_vars = {k.lower(): v for k, v in self.get_environ().items()}

        for field in self.__fields__.values():

            val: Optional[Any] = None

            if field.shape == SHAPE_LIST:
                val = []
                if field.type_.__origin__ == Union:
                    for arg in field.type_.__args__:
                        prefix = self.Config.env_prefix + arg.Config.env_prefix
                        kwargs = {}
                        for key, value in env_vars.items():
                            if key.startswith(prefix):
                                pl = len(prefix)
                                kwargs[key[pl:]] = value
                        if kwargs:
                            val.append(arg(**kwargs))
                d[field.alias] = val
                continue

            name: Optional[str] = None
            for env_name in field.field_info.extra['env_names']:
                val = env_vars.get(env_name)
                if val is not None:
                    break
                name = env_name

            if val is None or name is None:
                continue

            if field.is_complex():
                try:
                    val = self.__config__.json_loads(val)  # type: ignore
                except ValueError as e:
                    raise SettingsError(
                        f'error parsing JSON for "{name}"'
                    ) from e
            d[field.alias] = val

        return d
