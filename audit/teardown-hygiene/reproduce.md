# Воспроизведение evidence-пунктов досье Д68-Т

Окружение: стенд `/opt/MyApps/wagtail/wagtail-blogs`, venv
`/opt/MyApps/wagtail/venv-wg81` (Python 3.14.4, Django 6.0.9, Wagtail 8.1),
локальный PostgreSQL 14.15 (`127.0.0.1:5432`, пользователь
`a1020391_django_radio`). Все прогоны — на изолированных тестовых БД
(`test_*`); перед каждым сценарием при необходимости
`manage.py collectstatic --noinput` (чистый checkout требует манифест).

## 1. Базовый прогон на `ff296f9` (без дренажа)

```bash
git worktree add /opt/MyApps/wagtail/d68audit-base ff296f9
cp vsproject/settings/local.py /opt/MyApps/wagtail/d68audit-base/vsproject/settings/local.py
cd /opt/MyApps/wagtail/d68audit-base
python manage.py collectstatic --noinput
python manage.py test blog.tests_epoch --noinput
# → 24 теста OK, [test_runner] foreign session (pid, epoch-SELECT),
#   ObjectInUse на DROP, exit=1
```
Лог: `logs/base-ff296f9-quartet.log`.

## 2. Квартет на `main` ×15

```bash
for i in $(seq -w 1 15); do
  python manage.py test blog.tests_epoch --noinput > logs/quartet-$i.log 2>&1
done
# → 15/15 exit=0; дренаж завершает по 1 собственной утечке
```

## 3. Полный сюит ×2

```bash
for i in 1 2; do
  python manage.py test --noinput > logs/full-suite-$i.log 2>&1
done
# → 159 тестов, OK, 4 skipped, exit=0 ×2
```

## 4. Реальная серверная метка

```bash
python manage.py test blog.tests_epoch --noinput > quartet.log 2>&1 &
python tools/label_monitor.py $! label-capture.log test_a1020391_django_radio
# → все сессии прогона: application_name='vs-wagtail-tests-<pid>-<hex8>'
```
Лог: `logs/label-capture.log`.

## 5. Трасса потоков/asend

```bash
python tools/trace_connections.py \
  blog.tests_epoch.ConcurrentBumpTests blog.tests_epoch.MigrationChainTests \
  blog.tests_epoch.ConcurrentRestoreTests blog.tests_epoch.RequestStartedIntegrationTests
# → таймлайн тестов/соединений; утечка открывается внутри
#   test_async_asend_resets_cache, два MainThread-коннекта default живы одновременно
```
Лог: `logs/threads-trace.log`.

## 6. Чужая сессия на тест-БД (переживает teardown)

```bash
python manage.py test blog.tests_epoch --noinput > scenario.log 2>&1 &
PGAPPNAME=d68-foreign-manual psql -d test_a1020391_django_radio \
  -c "SELECT pg_sleep(60)" &
# после завершения прогона: [test_runner] WARNING: unresolved foreign session(s)
#   … foreign pid=… application_name='d68-foreign-manual'
# DROP падает с ObjectInUse, exit=1, чужая сессия остаётся подключённой
```
Лог: `logs/no-label-foreign-scenario.log` (вариант без PGAPPNAME —
application_name='psql' — поведение идентично).

## 7. Параллельные прогоны (разные тест-БД)

```bash
DB_NAME=a1020391_django_radio python manage.py test blog.tests_epoch --noinput &
DB_NAME=wagtail_dev DJANGO_SETTINGS_MODULE=vsproject.settings.test_env_db \
  PYTHONPATH=. python -m django test blog.tests_epoch --noinput &
# оба exit=0; каждый дренаж обслуживает только свою БД и свою метку
```
Логи: `logs/parallel-run-A.log`, `logs/parallel-run-B.log`.

## 8. Граница дренажа (регрессионный тест в сюите)

```bash
python manage.py test blog.tests_runner_teardown --noinput
# → своя утечка завершается; чужая сессия (другая application_name)
#   не завершается и жива на сервере; сессия TestCase не затронута
```
Лог: `logs/boundary-regression-test.log`.

## 9. Демо углов раннера

```bash
python tools/demo_drain_report_gap.py   # logs/drain-report-gap-demo.log
python tools/demo_dead_pg.py            # logs/dead-pg-demo.log
```

## 10. Следующий шаг для теста серверной метки внутри сюита

В `blog/tests_runner_teardown.py` добавить утверждение: открыть второе
psycopg2-подключение к postgres и убедиться, что живая сессия
`connection.connection.get_backend_pid()` видна в `pg_stat_activity`
с `application_name == run_label` (подтверждение серверной метки
непосредственным тестом, дополнение к монитору пункта 4).
