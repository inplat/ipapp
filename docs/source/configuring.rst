Конфигурирование
================

Конфигурирование приложения должно производиться путем конфиггурирования его компонентов.

Конфигурацией компонента должен быть экземпляр класса
`pydantic.BaseModel <https://pydantic-docs.helpmanual.io/usage/models/#basic-model-usage>`_.

Конфигурация всего приложения должен быть экземпляр класса `ipapp.config.BaseConfig`.

Например:

.. code-block:: python

    class Config(ipapp.BaseConfig):
        db: ipapp.db.pg.PostgresConfig

    class Application(ipapp.BaseApplication):
        def __init__(self, cfg: Config) -> None:
            super().__init__(cfg)
            self.add('db', ipapp.db.pg.Postgres(cfg.db))

    cfg = Config.from_env()
    app = Application(cfg)
    app.run()


Конфигурация может быть загружена либо из переменных окружения, либо из файла в формате json или yaml.
Можно создать объект конфигурации из другого источника, но в этом случае разработчику следует самостоятельно
решать вопрос получения и декодирования значений.

Чтение из переменных окружения
------------------------------

Имена переменных окружения должны быть названы по следующему правилу:
`<PREFIX>_<COMPONENT>_<ATTR>`, где `<PREFIX>` - аргумент функции Config.from_env, `<COMPONENT>` - атрибут объекта Config с конфигурацией для компонента, `<ATTR>` - атрибут конфигурации компонента.


Пример:

.. code-block:: python

    class OtherConfig(pydantic.BaseModel):
        anykey: int = 0

    class Config(ipapp.BaseConfig):
        db: ipapp.db.pg.PostgresConfig
        oth: OtherConfig

    # Чтение из переменных окружения
    # например APP_DB_URL=postgres://own@localhost/main
    #          APP_OTH_ANYKEY=1
    cfg = Config.from_env(prefix='APP_')
    assert cfg.db.url == 'postgres://own@localhost/main'  # значение из переменной окружения APP_DB_URL
    assert cfg.oth.anykey == 1  # значение из переменной окружения APP_OTH_ANYKEY
    assert cfg.db.pool_min_size == 4  # значение по-умолчанию

Чтение из JSON или YAML файла
-----------------------------

Пример содержимого файла:

.. code-block:: javascript
    :caption: config.json

    {
        "db": {
            "url": "postgres://own@localhost/main"
        },
        "oth": {
            "anykey": 1
        }
    }

Пример загрузки конфигурации:

.. code-block:: python

    class OtherConfig(pydantic.BaseModel):
        anykey: int = 0

    class Config(ipapp.BaseConfig):
        db: ipapp.db.pg.PostgresConfig
        oth: OtherConfig

    cfg = Config.from_json('config.json')
    assert cfg.db.url == 'postgres://own@localhost/main'  # значение из переменной окружения APP_DB_URL
    assert cfg.oth.anykey == 1  # значение из переменной окружения APP_OTH_ANYKEY
    assert cfg.db.pool_min_size == 4  # значение по-умолчанию
    # или
    cfg = Config.from_json(io.StringIO('{"db": {"url": "postgres://own@localhost/main"},"oth": {"anykey": 1}}'))