Документация Sphinx
===================

Для автоматической генерации sphinx документации конфигурации сервиса
добавьте ``ipapp.sphinx.config`` в ``extensions`` в sphinx ``conf.py``

.. code-block:: python

    extensions = [
       ...
       "ipapp.sphinx.config",
    ]

а так же добавьте настройки

.. code-block:: python

    from servicename.config import Config

    ipapp_config = {
        "class": Config,
        "prefix": "APP_",
    }

после этого вы можете использовать директиву ``config``
в любом месте документации

.. code-block:: restructuredtext

   .. config::
