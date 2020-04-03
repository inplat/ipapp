from __future__ import annotations

import json
import os
import sys
from collections import OrderedDict
from decimal import Decimal
from enum import Enum
from io import BufferedIOBase, RawIOBase, TextIOBase
from typing import (
    IO,
    Any,
    Callable,
    Dict,
    Generic,
    List,
    Mapping,
    Optional,
    Type,
    TypeVar,
    Union,
)
from uuid import UUID

import yaml
from pydantic.fields import SHAPE_SINGLETON
from pydantic.main import BaseModel, Extra
from pydantic.schema import field_class_to_schema
from pydantic.typing import is_callable_type
from yaml import SafeDumper, SafeLoader

from .misc import json_encoder

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
        return self._dict_to_env(self.to_dict(), [])

    @classmethod
    def _dict_to_env(cls, val: dict, path: List[str]) -> Dict[str, str]:
        res = OrderedDict()
        for cname, cmodel in val.items():
            _path = list(path) + [cname]
            env_name: str
            if isinstance(cmodel, dict):
                for subname, submodel in cmodel.items():
                    if isinstance(submodel, dict):
                        pass
                    else:
                        _sub_path = _path + [subname]
                        env_name = cls._to_env_name(*_sub_path)
                        res[env_name] = cls._to_env_val(submodel, _sub_path)
            else:
                env_name = cls._to_env_name(cname)
                res[env_name] = cls._to_env_val(cmodel, _path)
        return res

    @classmethod
    def _to_env_val(cls, val: Any, path: List[str]) -> str:
        if val is None:
            return ''
        if not isinstance(val, (bool, int, float, str, Decimal)):
            raise NotImplementedError(
                'configuration value must be scalar in %s. %s'
                % ('.'.join(path), type(val))
            )

        if isinstance(val, bool):
            return '1' if val else '0'
        else:
            return str(val)

    @staticmethod
    def _to_env_name(*path: str) -> str:
        return '_'.join([v.upper() for v in path])

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

    @classmethod  # noqa: C901
    def to_env_schema(
        cls: Type[T],
        prefix: str,
        model: Optional[Type[BaseModel]] = None,
        schema: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if model is None:
            model = cls

        if schema is None:
            schema = {}

        for field in model.__fields__.values():
            field_type = field.type_

            if field.sub_fields or is_callable_type(field_type):
                continue

            examples = {
                UUID: "6eca48d9-3abf-46f8-876a-96cb29e0862b",
                str: "",
                int: 0,
                float: 0.0,
                bool: 0,
                type(None): "",
            }

            name = f"{prefix}{field.alias or field.name}".upper()
            f_schema: Dict[str, Any] = dict(
                required=field.required,
                ge=field.field_info.ge,
                gt=field.field_info.gt,
                le=field.field_info.le,
                lt=field.field_info.lt,
                regex=field.field_info.regex,
                deprecated=field.field_info.extra.get("deprecated", False),
            )

            if field.field_info.description:
                f_schema["description"] = field.field_info.description

            default = field.field_info.default
            try:
                default = json_encoder(default)
            except TypeError:
                pass

            if not isinstance(default, type(Ellipsis)):
                if default is None:
                    f_schema["default"] = ""
                elif default is True:
                    f_schema["default"] = 1
                elif default is False:
                    f_schema["default"] = 0
                else:
                    f_schema["default"] = default

            example = field.field_info.extra.get("example", Ellipsis)
            if not isinstance(example, type(Ellipsis)):
                if example is None:
                    f_schema["example"] = ""
                elif example is True:
                    f_schema["example"] = 1
                elif example is False:
                    f_schema["example"] = 0
                else:
                    f_schema["example"] = example
            elif not isinstance(default, type(Ellipsis)):
                f_schema["example"] = f_schema["default"]
            else:
                for e in examples:
                    if issubclass(field_type, e):
                        f_schema["example"] = examples[e]

            if issubclass(field_type, Enum):
                f_schema.update({"enum": [item.value for item in field_type]})

            for type_, t_schema in field_class_to_schema:
                if issubclass(field_type, type_):
                    f_schema.update(t_schema)
                    break

            if issubclass(field_type, BaseModel):
                cls.to_env_schema(f"{name}_", field_type, schema)
                continue

            schema[name] = f_schema

        return schema

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
