from __future__ import annotations

import json
import os
import sys
from io import BufferedIOBase, RawIOBase, TextIOBase
from typing import (
    IO,
    Any,
    Callable,
    Dict,
    Generic,
    Mapping,
    Optional,
    Type,
    TypeVar,
    Union,
)

import yaml
from pydantic.fields import SHAPE_SINGLETON
from pydantic.main import BaseModel, Extra
from yaml import SafeDumper, SafeLoader

__all__ = ("BaseConfig",)

T = TypeVar("T", bound="BaseConfig")
IO_TYPES = (RawIOBase, TextIOBase, BufferedIOBase)


class BaseConfig(BaseModel, Generic[T]):
    @classmethod
    def _filter_dict(
        cls: Type[T],
        input_dict: Mapping[str, Any],
        prefix: str,
        trim_prefix: bool = True,
    ) -> Mapping[str, Any]:
        output_dict: Dict[str, Any] = {}
        for key, value in input_dict.items():
            if not cls.__config__.case_sensitive:
                key = key.lower()
                prefix = prefix.lower()

            if key.startswith(prefix):
                output_dict[key[len(prefix) :] if trim_prefix else key] = value

        return output_dict

    @classmethod
    def from_env(cls: Type[T], prefix: str = "") -> T:
        d: Dict[str, Optional[Any]] = {}
        env_vars = cls._filter_dict(os.environ, prefix)

        for field in cls.__fields__.values():
            deprecated = field.field_info.extra.get("deprecated", False)
            if deprecated:
                print(
                    f"WARNING: {field.name} field is deprecated",
                    file=sys.stderr,
                )

            field_prefix = field.field_info.extra.get(
                "env_prefix", f"{field.name}_",
            )

            if field.shape == SHAPE_SINGLETON and issubclass(
                field.type_, BaseModel
            ):
                field_values = cls._filter_dict(env_vars, field_prefix)
                d[field.alias] = field.type_(**field_values)

        return cls(**d)

    def to_env(self) -> Dict[str, str]:
        raise NotImplementedError

    @classmethod
    def from_dict(cls: Type[T], input_dict: Dict[str, Any]) -> T:
        return cls(**input_dict)

    def to_dict(self, **kwargs: Any) -> Dict[str, Any]:
        return self.dict(**kwargs)

    @classmethod
    def from_json(
        cls: Type[T],
        stream: Union[str, IO],
        loads: Optional[Callable] = None,
        **kwargs: Any,
    ) -> T:
        loads = loads or cls.__config__.json_loads
        string: Optional[str] = None

        if isinstance(stream, str):
            with open(stream) as f:
                string = f.read()
        elif isinstance(stream, IO_TYPES):
            string = stream.read()
        else:
            raise ValueError

        return cls(**loads(string, **kwargs))

    def to_json(self, stream: Union[str, IO], **kwargs: Any) -> None:
        data = self.json(**{"indent": 4, **kwargs})  # type: ignore

        if isinstance(stream, str):
            with open(stream, "w") as f:
                f.write(data)
        elif isinstance(stream, IO_TYPES):
            stream.write(data)
        else:
            raise ValueError

    @classmethod
    def from_yaml(
        cls: Type[T],
        stream: Union[str, IO],
        load: Optional[Callable] = None,
        **kwargs: Any,
    ) -> T:
        load = load or cls.__config__.yaml_load
        string: Optional[str] = None

        if isinstance(stream, str):
            with open(stream) as f:
                string = f.read()
        elif isinstance(stream, IO_TYPES):
            string = stream.read()
        else:
            raise ValueError

        return cls(**load(string, **{"Loader": SafeLoader, **kwargs}))

    def to_yaml(
        self,
        stream: Union[str, IO],
        dump: Optional[Callable] = None,
        **kwargs: Any,
    ) -> None:
        dump = dump or self.__config__.yaml_dump

        json_str = self.json(**{"indent": 4, **kwargs})  # type: ignore
        json_obj = self.__config__.json_loads(json_str)
        yaml_str = dump(json_obj, **{"Dumper": SafeDumper, **kwargs})

        if isinstance(stream, str):
            with open(stream, "w") as f:
                f.write(yaml_str)
        elif isinstance(stream, IO_TYPES):
            stream.write(yaml_str)
        else:
            raise ValueError

    class Config:
        validate_all = True
        extra = Extra.forbid
        arbitrary_types_allowed = True
        case_sensitive = False
        json_loads: Callable = json.loads
        json_dumps: Callable = json.dumps
        yaml_load: Callable = yaml.load
        yaml_dump: Callable = yaml.dump

    __config__: Config  # type: ignore
