О фреймворке
============

Фреймворк для создания сервисов на `python3.7 <python37_>`_ используя асинхронное программирование с применением `asyncio <asyncio_>`_


Ключевые особенности
--------------------

* компонентная модель приложения с поддержкой graceful shutdown
* единый интерфейс логирования в zipkin, prometheus, sentry и пр.
* библиотеки на разные случаи жизни, например: RPC поверх HTTP/AMQP, отложенные задачи, ...

Требования
----------

* python >= 3.7

Установка библиотеки
--------------------

Библиотеку можно установить через пакетный менеджер::

    pip install --extra-index-url https://pip.sys.ipl/simple --trusted-host pip.sys.ipl ipapp

Вы также можете установить дополнительные зависимости, если планируете использовать:

1. СУБД PostgreSQl::

    pip install --extra-index-url https://pip.sys.ipl/simple --trusted-host pip.sys.ipl ipapp[postgres]

2. Брокер очередей RabbitMq::

    pip install --extra-index-url https://pip.sys.ipl/simple --trusted-host pip.sys.ipl ipapp[rabbitmq]

3. RPC::

    pip install --extra-index-url https://pip.sys.ipl/simple --trusted-host pip.sys.ipl ipapp[iprpc]

4. Инструменты для тестирования::

    pip install --extra-index-url https://pip.sys.ipl/simple --trusted-host pip.sys.ipl ipapp[testing]

Или все вышеперечисленное сразу::

    pip install --extra-index-url https://pip.sys.ipl/simple --trusted-host pip.sys.ipl ipapp[postgres,rabbitmq,iprpc,testing]


Начало работы
-------------

Простой HTTP сервер:

.. literalinclude:: ../../examples/helloworld.py
   :caption: examples/helloworld.py
   :language: python
   :linenos:

Запуск:

.. code-block:: console

    $ APP_HTTP_HOST=127.0.0.1 \
      APP_HTTP_PORT=8888 \
      python -m examples.helloworld
    INFO:root:Configuring logger
    INFO:root:Prepare for start
    INFO:root:Starting...
    INFO:root:Starting HTTP server
    INFO:root:Running HTTP server on http://127.0.0.1:8888
    INFO:root:Running...

Проверка работоспособности:

.. code-block:: console

    $ curl 'http://127.0.0.1:8888/'
    Hello, world!


Создание сервиса
----------------

Для удобства создания новых сервисов был создан шаблон проекта `cookiecutter <cookiecutter_>`_:

.. code-block:: console

    $ cookiecutter git@gitlab.app.ipl:template/ipapp-service.git
    # вводим, например:
    #     project_name: Inplat Payments API
    #     project_slug: api

    $ cd api

    # создание виртуального окружения и установка зависимостей:
    $ make venv

    # запуск линтеров
    $ make lint

    # запуск в docker зависимых сервисов(т.к. PostgreSQL)
    $ make test-prepare

    # прогон автоматических тестов:
    $ make test

    $ git init && git add . && git commit -m "Initial commit"
    # далее добавить git remote и сделать git push

    # запуск сервиса в виртуальном окружении
    $ . .venv/bin/activate
    $ python -m api

Обратите внимание на аргументы командной строки для запуска сервиса:

.. code-block:: console

    $ python -m api --help


.. _asyncio: https://docs.python.org/3.7/library/asyncio.html
.. _python37: https://docs.python.org/3.7/
.. _cookiecutter: https://cookiecutter.readthedocs.io/en/latest/
