Логирование
===========

Программный интерфейс
---------------------

Для логирования применяется интерфейс стандарта `OpenTracing <https://github.com/opentracing/specification/blob/master/specification.md>`_.

Для доступа к текущему span-у контекста можно получить доступ через глобальную переменную ipapp.ctx.span:

.. code-block:: python

    from ipapp.ctx import span
    ...
    async def run_something():
        span.tag('tagkey', 'some value')


В случае если необходимо провести манипуляции со span-ом, который будет создан в внутри вызываемой функции
(например при отправке http запроса), можно воспользоваться захватом. Для этого нужно в логгере вызвать функцию
capture_span, в которую нужно передать класс(или родительский класс) перехватываемого span-а,
получить объект :class:`ipapp.logger.span.SpanTrap`. Далее с помощью контекстного менеджера выполить запрос и
произвести манипуляции со span-ом:

.. code-block:: python

    app: 'Application'

    async def run_something():
        with self.app.logger.capture_span(cls=Span) as trap:
            response = await app.http_client.request('GET', 'https://yandex.ru/')
            trap.span.tag('tagkey', 'some value')



Zipkin
------

Трассировка внешних вызовов в сервисе.

:class:`ipapp.logger.adapters.zipkin.ZipkinAdapter`

Работает на базе библиотеки `aiozipkin <https://github.com/aio-libs/aiozipkin>`_.

Prometheus
----------

Сбор метрик внешних вызовов.

:class:`ipapp.logger.adapters.prometheus.PrometheusAdapter`

Работает на базе официальной библиотеки `prometheus <https://github.com/prometheus/client_python>`_.

Sentry
------

Отправка ошибок в Sentry

:class:`ipapp.logger.adapters.sentry.SentryAdapter`

Работает на базе официальной библиотеки `sentry <https://github.com/getsentry/sentry-python>`_.


Requests
--------

Сохранение данных входящих/исходящих http вызовов в БД. Сохранаются: url, тело/заголовки запроса/ответа, информция об ошибках и дл полезная информация.

:class:`ipapp.logger.adapters.requests.RequestsAdapter`

Работает с использованием библиотеки `asyncpg <https://github.com/MagicStack/asyncpg>`_.
