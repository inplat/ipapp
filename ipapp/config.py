import copy
import os
from collections import OrderedDict
from typing import Optional, Union, Dict, Any, Type
from os import _Environ

Env = Union[_Environ, dict]


class ConfigError(Exception):
    pass


class Val:

    def __init__(self, name: str, value: Any) -> None:
        self.name = name
        self.value = value

    def __call__(self):  # pragma: nocover
        raise NotImplementedError()

    @staticmethod
    def type_name() -> str:
        return ''

    def args_markdown(self) -> str:
        return ''


class StrVal(Val):

    def __init__(self, name: str, value: Any,
                 max: Optional[int] = None,
                 min: Optional[int] = None) -> None:
        super().__init__(name, value)
        self.min = min
        self.max = max

    def __call__(self) -> str:
        if not isinstance(self.value, str):
            raise ConfigError("%s must be a string" % self.name)
        if self.min is not None and len(self.value) < self.min:
            raise ConfigError("length of %s must be greater than or equal to "
                              "%s"
                              "" % (self.name, self.min))
        if self.max is not None and len(self.value) > self.max:
            raise ConfigError("length of %s must be less than or equal to %s"
                              "" % (self.name, self.max))

        return self.value

    @staticmethod
    def type_name() -> str:
        return 'string'

    def args_markdown(self) -> str:
        text = ''
        if self.min is not None:
            text += '\n  min length: %s' % self.min
        if self.max is not None:
            text += '\n  max length: %s' % self.max
        return text


class BoolVal(Val):

    def __call__(self) -> bool:
        if isinstance(self.value, bool):
            return self.value
        if isinstance(self.value, int):
            return self.value != 0
        if isinstance(self.value, str):
            if self.value.lower() in ('1', 'on', 'true', 't'):
                return True
            if self.value.lower() in ('0', 'off', 'false', 'f'):
                return False
        raise ConfigError("%s must be a boolean" % self.name)

    @staticmethod
    def type_name() -> str:
        return 'boolean'


class IntVal(Val):

    def __init__(self, name: str, value: Any,
                 max: Optional[int] = None,
                 min: Optional[int] = None) -> None:
        super().__init__(name, value)
        self.min = min
        self.max = max

    def __call__(self) -> int:
        try:
            val = int(self.value)
        except Exception:
            raise ConfigError("%s must be an integer" % self.name)
        if self.min is not None and val < self.min:
            raise ConfigError("%s must be greater than or equal to %s"
                              "" % (self.name, self.min))
        if self.max is not None and val > self.max:
            raise ConfigError("%s must be less than or equal to %s"
                              "" % (self.name, self.max))
        return val

    @staticmethod
    def type_name() -> str:
        return 'integer'

    def args_markdown(self) -> str:
        text = ''
        if self.min is not None:
            text += '\n  min value: %s' % self.min
        if self.max is not None:
            text += '\n  max value: %s' % self.max
        return text


class FloatVal(Val):

    def __init__(self, name: str, value: Any,
                 max: Optional[float] = None,
                 min: Optional[float] = None) -> None:
        super().__init__(name, value)
        self.min = min
        self.max = max

    def __call__(self) -> float:
        try:
            val = float(self.value)
        except Exception:
            raise ConfigError("%s must be a float" % self.name)
        if self.min is not None and val < self.min:
            raise ConfigError("%s must be greater than or equal to %s"
                              "" % (self.name, self.min))
        if self.max is not None and val > self.max:
            raise ConfigError("%s must be less than or equal to %s"
                              "" % (self.name, self.max))
        return val

    @staticmethod
    def type_name() -> str:
        return 'float'

    def args_markdown(self) -> str:
        text = ''
        if self.min is not None:
            text += '\n  min value: %s' % self.min
        if self.max is not None:
            text += '\n  max value: %s' % self.max
        return text


class FileVal(Val):

    def __init__(self, name: str, value: Any,
                 mode: str = 'r',
                 encoding: str = 'UTF-8') -> None:
        super().__init__(name, value)
        self.mode = mode
        self.encoding = encoding

    def __call__(self, mode: str = 'r', encoding: str = 'UTF-8') -> str:
        try:
            with open(self.value, mode, encoding=encoding):
                pass
        except Exception as e:
            raise ConfigError("Could not access to file %s with error: %s"
                              "" % (self.value, e))
        return self.value

    @staticmethod
    def type_name() -> str:
        return 'string(path to file)'

    def args_markdown(self) -> str:
        text = ''
        if self.mode is not None:
            text += '\n  assess mode: %s' % self.mode
        if self.encoding is not None:
            text += '\n  encoding: %s' % self.encoding
        return text


class DirVal(Val):

    def __call__(self) -> str:
        if not os.path.exists(self.value):
            raise ConfigError("Directory %s does not exist"
                              "" % self.value)
        return self.value

    @staticmethod
    def type_name() -> str:
        return 'string(path to dir)'


class Config:
    _vars: Dict[str, Union[Dict, OrderedDict]] = {}

    def __init__(self,
                 env: Optional[Env] = None) -> None:
        self._env = env or os.environ
        self._conf = copy.deepcopy(self._vars)
        self._description: Dict[str, Dict] = {}
        for key, val in self._conf.items():
            val_type = val.pop('type')
            val_name = val.pop('name')
            val_default = None
            if 'default' in val:
                val_default = val.pop('default')
            required = False
            if 'required' in val:
                required = bool(val.pop('required'))
            descr = ''
            if 'descr' in val:
                descr = str(val.pop('descr'))

            self._description[val_name] = {
                'type': val_type,
                'default': val_default,
                'required': required,
                'descr': descr,
            }

            value = self._env.get(val_name, val_default)
            if value is None:
                if required is True:
                    raise ConfigError("%s is required" % val_name)
                else:
                    setattr(self, key, None)
            else:
                v: Any = self._get_val(val_type)
                setattr(self, key, v(val_name, value, **val)())

    @classmethod
    def _get_val(cls, val_type: Any) -> Type[Val]:
        if val_type == str:
            return StrVal
        elif val_type == bool:
            return BoolVal
        elif val_type == int:
            return IntVal
        elif val_type == float:
            return FloatVal
        elif val_type == 'file':
            return FileVal
        elif val_type == 'dir':
            return DirVal
        elif isinstance(val_type, type) and issubclass(val_type, Val):
            return val_type
        else:
            raise UserWarning('Invalid configuration settings')

    @classmethod
    def as_markdown(cls):
        result = []

        for name, options in cls._vars.items():
            options = copy.deepcopy(options)

            type_cls = cls._get_val(options.pop('type'))
            env_name = options.pop('name')
            descr = ''
            if 'descr' in options:
                descr = ': %s' % options.pop('descr')
            text = '* %s%s\n  type: %s' % (env_name, descr,
                                           type_cls.type_name())
            if 'required' in options:
                required = options.pop('required')
                if required:
                    text += '\n\n  required'
            if 'default' in options:
                default = options.pop('default')
                if default:
                    text += '\n  default: %s' % default

            inst = type_cls(env_name, None, **options)
            text += inst.args_markdown()

            result.append(text)

        return '\n\n'.join(result) + '\n'
