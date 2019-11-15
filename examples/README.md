Запуск окружения для логирования

Создаем .env файл::

    cp .env.example .env
    
Формируем секретный ключ для sentry::

    docker-compose run --rm sentry config generate-secret-key
    
Копируем ключ в `.env` в `SENTRY_SECRET_KEY`

Инициализируем базу данных sentry::

    docker-compose run --rm sentry upgrade
    
    
Запускаем окружение::
    
    docker-compose up -d
    
    
Проверяем, что все запустилось::
    
    docker-compose ps
    
sentry: http://localhost:9000/
jaeger: http://localhost:9003/
PGPASSWORD=secretpwd psql -h localhost -p 9001 -U ipapp ipapp

Создаем таблицы для логов апросов::

    docker exec -t ipapp-postgres psql -U ipapp -c 'CREATE SCHEMA log;'
    docker exec -t ipapp-postgres psql -U ipapp -c 'CREATE TABLE log.request(id bigserial PRIMARY KEY,stamp_begin timestamptz NOT NULL,stamp_end timestamptz NOT NULL,is_out boolean NOT NULL,url text,method text,req_hdrs text,req_body text,resp_hdrs text,resp_body text,status_code int,error text,tags jsonb);'

Создаем в sentry проект и конфигурируем приложение

Запускаем пример::
    
    python3 -u -m examples.app
    
Проверяем:

    curl -i 'http://localhost:8888/?widget_id=12'