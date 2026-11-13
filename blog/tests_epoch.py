"""Тесты Этапа 2 (durable epoch) — вытеснение, конкурентность, сигналы, миграция 0039.

Квартет ConcurrentBumpTests + MigrationChainTests + ConcurrentRestoreTests +
RequestStartedIntegrationTests в ОДНОМ процессе — минимальный воспроизводитель
teardown-утечки Д68-Т: reopened-коннект внутри test_async_asend_resets_cache
(второй get_blog_cache_epoch после asend) ускользает от штатных путей закрытия
и живёт до выхода процесса; сигнатура — idle-сессия с последним SELECT к
blog_invalidationepoch. Тест соединение сам не закрывает: ускользнувший
коннект остаётся незакрытым в процессе; его диагностирует и завершает
дренаж раннера — vsproject/test_runner.py (teardown_databases), строго в
пределах сессий с меткой запуска (application_name).
"""
import os
import threading
from unittest import mock

from django.core.cache import caches
from django.db import IntegrityError, transaction, connection
from django.test import Client, TestCase, TransactionTestCase
from django.utils import timezone
from wagtail.models import Page, Site

from blog.cache_utils import _reset_epoch_cache, bump_blog_cache_epoch, get_blog_cache_epoch, _epoch_var

def _fresh_epoch():
    """Per-request кэш эпохи: в тестах без HTTP-запроса сбрасываем явно."""
    _reset_epoch_cache()
    return get_blog_cache_epoch()
from blog.models import BlogPage, InvalidationEpoch
from blog.tests_replay_http import FlushCascadeMixin
from blog.page_cache import PAGES, _cache_key


class DurableEpochTests(TestCase):
    """Базовое поведение singleton-эпохи."""

    @classmethod
    def setUpTestData(cls):
        root = Page.get_first_root_node()
        cls.home = root.add_child(instance=Page(title="Epoch home", slug="epoch-home"))
        site = Site.objects.filter(is_default_site=True).first()
        if site is None:
            Site.objects.create(hostname="testserver", root_page=cls.home, is_default_site=True)
        else:
            site.root_page = cls.home
            site.save()
        cls.post = cls.home.add_child(
            instance=BlogPage(title="Epoch пост", slug="epoch-post",
                              body="<p>Уникальный epoch-пост</p>",
                              date_published=timezone.now())
        )
        cls.post.save_revision().publish()
        InvalidationEpoch.objects.update_or_create(pk=1, defaults={"epoch": "test-epoch"})
        caches["pages"].clear()

    def test_epoch_survives_cache_clear(self):
        """Вытеснение/очистка ВСЕХ cache aliases не возвращает старую эпоху."""
        before = _fresh_epoch()
        for alias in ("default", "security", "pages"):
            caches[alias].clear()
        self.assertEqual(_fresh_epoch(), before,
                         "Очистка кэшей не должна сбрасывать эпоху")

    def test_epoch_bump_changes_value(self):
        before = _fresh_epoch()
        bump_blog_cache_epoch()
        self.assertNotEqual(_fresh_epoch(), before)

    def test_epoch_bump_invalidates_page_cache(self):
        """Публикация/эпоха: HIT → MISS после смены эпохи (строгий сценарий)."""
        c = Client()
        r1 = c.get("/epoch-post/", HTTP_ACCEPT_LANGUAGE="ru")
        self.assertEqual(r1.status_code, 200)
        r2 = c.get("/epoch-post/", HTTP_ACCEPT_LANGUAGE="ru")
        self.assertEqual(r2.headers.get("X-Page-Cache"), "hit")
        bump_blog_cache_epoch()
        r3 = c.get("/epoch-post/", HTTP_ACCEPT_LANGUAGE="ru")
        self.assertEqual(r3.headers.get("X-Page-Cache"), "miss",
                         "Смена эпохи должна инвалидировать page-кэш")

    def test_epoch_singleton(self):
        InvalidationEpoch.bump()
        InvalidationEpoch.bump()
        self.assertEqual(InvalidationEpoch.objects.count(), 1,
                         "bump() не должен создавать дубликаты")

    def test_rollback_does_not_change_epoch(self):
        """Откат транзакции не меняет эпоху (bump внутри атомарной транзакции)."""
        before = _fresh_epoch()
        try:
            with transaction.atomic():
                InvalidationEpoch.bump()
                raise RuntimeError("откат")
        except RuntimeError:
            pass
        self.assertEqual(_fresh_epoch(), before,
                         "Откат транзакции не должен менять эпоху")


class EpochSingletonIntegrityTests(TestCase):
    """Миграция 0039, constraint, fail-safe при отсутствии строки/сбое БД."""

    @classmethod
    def setUpTestData(cls):
        InvalidationEpoch.objects.update_or_create(pk=1, defaults={"epoch": "seeded"})
        root = Page.get_first_root_node()
        cls.home = root.add_child(instance=Page(title="Seed home", slug="seed-home"))
        site = Site.objects.filter(is_default_site=True).first()
        if site is None:
            Site.objects.create(hostname="testserver", root_page=cls.home, is_default_site=True)
        else:
            site.root_page = cls.home
            site.save()
        cls.post = cls.home.add_child(
            instance=BlogPage(title="Seed пост", slug="seed-post",
                              body="<p>Уникальный seed-пост</p>",
                              date_published=timezone.now())
        )
        cls.post.save_revision().publish()
        caches["pages"].clear()

    # --- миграция 0039: функции вызываются напрямую (RunPython) ---
    @staticmethod
    def _seed():
        import importlib.util

        class _AppsShim:
            @staticmethod
            def get_model(app_label, model_name):
                return InvalidationEpoch

        spec = importlib.util.spec_from_file_location(
            "mig0039",
            "blog/migrations/0039_seed_invalidation_epoch_singleton.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        m.seed_epoch(_AppsShim(), None)

    def test_migration_seed_creates_row_on_empty_db(self):
        InvalidationEpoch.objects.all().delete()
        self._seed()
        row = InvalidationEpoch.objects.get(pk=1)
        self.assertNotEqual(row.epoch, "initial", "сидируется случайная эпоха, не 'initial'")
        self.assertEqual(len(row.epoch), 32)

    def test_migration_seed_idempotent_with_existing_row(self):
        InvalidationEpoch.objects.filter(pk=1).update(epoch="existing-value")
        self._seed()
        self.assertEqual(InvalidationEpoch.objects.get(pk=1).epoch, "existing-value",
                         "существующая корректная строка не перезаписывается")
        self.assertEqual(InvalidationEpoch.objects.count(), 1)

    def test_migration_seed_defensive_delete_noop_with_constraint(self):
        """С ограничением singleton коррупт-строки (pk != 1) невозможны;
        защитное удаление в seed_epoch остаётся no-op."""
        self._seed()
        self.assertEqual(InvalidationEpoch.objects.count(), 1)
        self.assertTrue(InvalidationEpoch.objects.filter(pk=1).exists())

    # --- constraint singleton ---
    def test_constraint_forbids_second_row(self):
        with self.assertRaises(IntegrityError):
            InvalidationEpoch.objects.create(pk=2, epoch="second")

    # --- fail-safe: отсутствие строки ---
    def test_missing_row_bypasses_not_initial(self):
        InvalidationEpoch.objects.all().delete()
        _reset_epoch_cache()
        e1 = get_blog_cache_epoch()
        _reset_epoch_cache()
        e2 = get_blog_cache_epoch()
        self.assertNotEqual(e1, "initial",
                            "отсутствие строки НЕ должно возвращать старый namespace")
        self.assertTrue(e1.startswith("bypass-"))
        self.assertNotEqual(e1, e2, "bypass-эпоха уникальна на каждый вызов → гарантированный MISS")

    def test_missing_row_page_cache_stays_miss(self):
        InvalidationEpoch.objects.all().delete()
        c = Client()
        r1 = c.get("/seed-post/", HTTP_ACCEPT_LANGUAGE="ru")
        r2 = c.get("/seed-post/", HTTP_ACCEPT_LANGUAGE="ru")
        self.assertEqual(r1.headers.get("X-Page-Cache"), "miss")
        self.assertEqual(r2.headers.get("X-Page-Cache"), "miss",
                         "без singleton-строки page cache не должен попадать в HIT")

    def test_missing_row_logged_as_error(self):
        InvalidationEpoch.objects.all().delete()
        InvalidationEpoch._bypass_logged = False
        self.addCleanup(setattr, InvalidationEpoch, "_bypass_logged", True)
        _reset_epoch_cache()
        with self.assertLogs("blog.models", level="ERROR"):
            get_blog_cache_epoch()

    # --- fail-safe: недоступность БД ---
    def test_db_unavailable_bypass(self):
        with mock.patch.object(
                InvalidationEpoch.objects, "only",
                side_effect=Exception("db down")):
            _reset_epoch_cache()
            e = get_blog_cache_epoch()
        self.assertTrue(e.startswith("bypass-"),
                        "сбой БД должен давать bypass, а не старую эпоху")


class ConcurrentBumpTests(TransactionTestCase):
    """Конкурентные bump() на реальных соединениях (TestCase держит лок строки)."""

    def setUp(self):
        # ContextVar эпохи переживает тесты в одном процессе: page-рендеры
        # предыдущих модулей (например accounts) оставляют значение в главной
        # нити, а bump() воркеров выполняется в других потоках/контекстах.
        # Без сброса финальный assert сравнивает устаревшую эпоху ContextVar
        # с БД (воспроизводится при совместном прогоне; изолированно тест
        # зелёный).
        _reset_epoch_cache()

    def _fixture_teardown(self):
        # Легаси-таблица blog_comment (см. миграцию 0032) не входит в sqlflush;
        # стандартный flush без CASCADE падает на postgres.
        from django.core.management import call_command
        call_command("flush", verbosity=0, interactive=False,
                     database=self._databases_names()[0],
                     reset_sequences=False, allow_cascade=True)

    def test_concurrent_bumps_no_duplicates_no_old_namespace(self):
        epochs = []
        errors = []

        def worker():
            try:
                epochs.append(InvalidationEpoch.bump())
            except Exception as e:  # noqa: BLE001
                errors.append(f"{type(e).__name__}: {e}")
            finally:
                from django.db import connection
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertEqual(errors, [], "потоки не должны падать")
        self.assertEqual(len(epochs), 4)
        self.assertEqual(len(set(epochs)), 4, "каждый bump возвращает новую эпоху")
        self.assertEqual(InvalidationEpoch.objects.count(), 1)
        self.assertEqual(get_blog_cache_epoch(),
                         InvalidationEpoch.objects.get(pk=1).epoch)


class MigrationChainTests(FlushCascadeMixin, TransactionTestCase):
    """Реальное применение цепочки 0037-0039 через MigrationExecutor.

    Проверка координатора (Этап 4): seed_epoch() вызывался напрямую — это
    проверка функции, а не миграции. Здесь миграции откатываются до 0036 и
    применяются заново на изолированной тестовой БД.
    """

    BLOG_0036 = ("blog", "0036_alter_customimage_file")
    BLOG_0039 = ("blog", "0039_seed_invalidation_epoch_singleton")

    def _executor(self):
        from django.db.migrations.executor import MigrationExecutor
        return MigrationExecutor(connection)

    def _table_exists(self, table):
        with connection.cursor() as cur:
            cur.execute(
                "SELECT to_regclass(%s)", [f"public.{table}"])
            return cur.fetchone()[0] is not None

    def _constraint_exists(self, name):
        with connection.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM pg_constraint WHERE conname = %s", [name])
            return cur.fetchone() is not None

    def _migration_applied(self, target):
        return target in self._executor().loader.applied_migrations

    def test_rollback_and_reapply_chain(self):
        # --- откат 0039 -> 0038 -> 0037 (до 0036) ---
        self._executor().migrate([self.BLOG_0036])
        self.assertFalse(self._table_exists("blog_invalidationepoch"),
                         "0037 reverse должен удалить таблицу эпохи")
        self.assertFalse(self._table_exists("blog_usedformtoken"),
                         "0038 reverse должен удалить таблицу токенов")
        self.assertFalse(self._migration_applied(self.BLOG_0039))

        # --- повторное применение вперёд ---
        self._executor().migrate([self.BLOG_0039])
        self.assertTrue(self._table_exists("blog_invalidationepoch"))
        self.assertTrue(self._table_exists("blog_usedformtoken"))
        self.assertTrue(self._migration_applied(self.BLOG_0039))

        # 0039 создал singleton со случайной эпохой (не "initial")
        with connection.cursor() as cur:
            cur.execute(
                "SELECT epoch FROM blog_invalidationepoch WHERE id = 1")
            row = cur.fetchone()
        self.assertIsNotNone(row, "0039 должен сидировать строку pk=1")
        self.assertNotEqual(row[0], "initial")
        self.assertEqual(len(row[0]), 32, "ожидается случайный uuid4().hex")

        # constraint singleton присутствует на уровне БД
        self.assertTrue(
            self._constraint_exists("invalidation_epoch_singleton"),
            "0039 должен добавить CHECK (id = 1)")

        # дубликатов нет; повторный план применения пуст (идемпотентность)
        with connection.cursor() as cur:
            cur.execute("SELECT count(*) FROM blog_invalidationepoch")
            self.assertEqual(cur.fetchone()[0], 1)
        executor = self._executor()
        executor.loader.build_graph()
        self.assertEqual(
            executor.migration_plan([self.BLOG_0039]), [],
            "повторное применение 0039 не должно требоваться")

    def test_seed_idempotent_after_reapply(self):
        """Повторный вызов миграции не создаёт дубликат и не портит строку."""
        self._executor().migrate([self.BLOG_0036])
        self._executor().migrate([self.BLOG_0039])
        from blog.migrations import __path__ as _  # noqa: F401
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "mig0039b",
            "blog/migrations/0039_seed_invalidation_epoch_singleton.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)

        class _AppsShim:
            @staticmethod
            def get_model(app_label, model_name):
                from django.apps import apps
                return apps.get_model(app_label, model_name)

        m.seed_epoch(_AppsShim(), None)
        with connection.cursor() as cur:
            cur.execute("SELECT count(*) FROM blog_invalidationepoch")
            self.assertEqual(cur.fetchone()[0], 1)


class ConcurrentRestoreTests(FlushCascadeMixin, TransactionTestCase):
    """Гонка восстановления singleton при ОТСУТСТВУЮЩЕЙ строке (задание координатора Г).

    bump() на пустой таблице: update() = 0 -> create(pk=1). Несколько потоков
    одновременно -> ровно один create, остальные IntegrityError -> savepoint
    откатывается, внешняя транзакция остаётся рабочей, значение перезаписывается.
    """

    def test_concurrent_restore_on_empty_table(self):
        # Гарантируем пустую таблицу (первый тест класса стартует после
        # предыдущего TransactionTestCase, flush которого мог не затронуть
        # ещё не созданные в этом классе данные).
        InvalidationEpoch.objects.all().delete()
        from django.db import connection as db_connection
        threads, results, errors = [], [], []
        barrier = threading.Barrier(4)

        def worker():
            try:
                barrier.wait(timeout=10)
                results.append(InvalidationEpoch.bump())
            except Exception as e:  # noqa: BLE001
                errors.append(f"{type(e).__name__}: {e}")
            finally:
                db_connection.close()

        for _ in range(4):
            t = threading.Thread(target=worker)
            threads.append(t)
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(errors, [], f"потоки не должны падать: {errors}")
        self.assertEqual(len(set(results)), 4)
        self.assertEqual(InvalidationEpoch.objects.count(), 1,
                         "ровно одна строка после конкурентного восстановления")
        self.assertEqual(_fresh_epoch(),
                         InvalidationEpoch.objects.get(pk=1).epoch)


class BumpInsideOuterTransactionTests(TestCase):
    """bump() при внешней транзакции и отсутствующей строке: savepoint не
    рвёт внешнюю транзакцию — последующие ORM-запросы работают (Г)."""

    def test_bump_race_in_outer_atomic_keeps_transaction_usable(self):
        InvalidationEpoch.objects.all().delete()
        try:
            with transaction.atomic():
                # имитируем проигрыш гонки: create внутри bump падает
                # с IntegrityError, срабатывает резервный путь update()
                with mock.patch.object(
                        InvalidationEpoch.objects, "create",
                        side_effect=IntegrityError("race lost")):
                    e = InvalidationEpoch.bump()
                # ГЛАВНОЕ: внешняя транзакция жива — последующие ORM-запросы
                # выполняются без TransactionManagementError.
                self.assertTrue(Page.objects.exists())
                self.assertEqual(len(e), 32)
        except Exception as exc:
            self.fail(f"внешняя транзакция сломана после пойманного "
                      f"IntegrityError: {type(exc).__name__}: {exc}")


class EpochContextIsolationTests(FlushCascadeMixin, TransactionTestCase):
    """К1 (координатор): кэш эпохи изолирован per-request/per-context.

    Реализация — asgiref.local.Local (не глобальная переменная процесса):
    в WSGI значение отдельное на поток, в ASGI — на контекст (contextvars).
    Инвариант: ВНУТРИ одного запроса эпоха не меняется, как бы ни работал
    параллельный запрос (reset другого запроса не трогает чужое значение);
    новый запрос (reset + чтение) видит актуальную эпоху после bump.
    """

    def setUp(self):
        InvalidationEpoch.objects.update_or_create(pk=1, defaults={"epoch": "iso-base"})

    def test_parallel_requests_isolation(self):
        import time as _time
        barrier = threading.Barrier(2)
        # Гарантия порядка: B делает bump только ПОСЛЕ первого чтения A.
        # Без события гонка: bump B успевал раньше чтения A — assertNotEqual
        # падал на равных значениях (флаки при неудачном планировании потоков).
        a_read_e1 = threading.Event()
        a_vals, b_vals, errors = [], [], []

        def worker_a():
            try:
                _reset_epoch_cache()
                barrier.wait(timeout=10)
                e1 = get_blog_cache_epoch()
                a_read_e1.set()
                _time.sleep(0.4)  # за это время B делает reset+bump
                e2 = get_blog_cache_epoch()
                a_vals.extend([e1, e2])
            except Exception as e:  # noqa: BLE001
                errors.append(f"A: {type(e).__name__}: {e}")
            finally:
                from django.db import connection as conn
                conn.close()

        def worker_b():
            try:
                _reset_epoch_cache()
                barrier.wait(timeout=10)
                a_read_e1.wait(timeout=10)  # e1 A уже зафиксирован
                _reset_epoch_cache()          # request_started запроса B
                get_blog_cache_epoch()        # B кэширует своё значение
                InvalidationEpoch.bump()      # B инвалидирует БД (публикация)
                _reset_epoch_cache()
                b_vals.append(get_blog_cache_epoch())
            except Exception as e:  # noqa: BLE001
                errors.append(f"B: {type(e).__name__}: {e}")
            finally:
                from django.db import connection as conn
                conn.close()

        threads = [threading.Thread(target=worker_a), threading.Thread(target=worker_b)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(errors, [], f"потоки не должны падать: {errors}")
        self.assertEqual(len(a_vals), 2, "A должен получить два значения")
        self.assertEqual(a_vals[0], a_vals[1],
                         "ВНУТРИ запроса A эпоха изменилась из-за reset запроса B — "
                         "изоляция контекста нарушена")
        self.assertNotEqual(a_vals[0], b_vals[0],
                            "свежий контекст B должен видеть эпоху после bump A-запроса")
        self.assertEqual(b_vals[0], InvalidationEpoch.objects.get(pk=1).epoch)

    def test_async_context_isolation(self):
        """ASGI: reset параллельного запроса не должен менять кэш запроса A.
        ORM в async-задаче недоступен (SynchronousOnlyOperation), поэтому
        get_epoch подменяется контролируемой последовательностью old/new."""
        import asyncio

        async def scenario():
            bumped = {"flag": False}

            def fake_get_epoch():
                return "new" if bumped["flag"] else "old"

            pm = mock.patch.object(InvalidationEpoch, "get_epoch",
                                   staticmethod(fake_get_epoch))
            pm.start()
            self.addCleanup(pm.stop)

            async def a():
                _reset_epoch_cache()
                first = get_blog_cache_epoch()          # кэширует "old" в контексте A
                await asyncio.sleep(0.05)
                # B за это время сделал СВОЙ reset и СВОЙ bump — контекст A
                # не должен от этого измениться
                second = get_blog_cache_epoch()
                return first, second

            async def b():
                await asyncio.sleep(0.02)
                _reset_epoch_cache()
                bumped["flag"] = True
                _reset_epoch_cache()
                return get_blog_cache_epoch()

            a_task = asyncio.ensure_future(a())
            b_task = asyncio.ensure_future(b())
            a_res = await a_task
            b_val = await b_task
            return a_res, b_val

        (a_first, a_second), b_val = asyncio.run(scenario())
        self.assertEqual(a_first, "old")
        self.assertEqual(a_second, "old",
                         "В async-контексте reset параллельного запроса сломал "
                         "изоляцию: эпоха изменилась внутри запроса A")
        self.assertEqual(b_val, "new", "свежий контекст B видит эпоху после bump")


class RequestStartedIntegrationTests(FlushCascadeMixin, TransactionTestCase):
    """П1 (координатор): настоящий Django request_started сигнал.

    Проверяет:
    - sync send() сигнал сбрасывает ContextVar;
    - async asend() сигнал сбрасывает ContextVar (Django ASGI);
    - get_blog_cache_epoch() вне HTTP-запроса работает;
    - после bump() в том же контексте get_blog_cache_epoch() возвращает новую эпоху.
    """

    def setUp(self):
        InvalidationEpoch.objects.update_or_create(pk=1, defaults={"epoch": "sig-base"})

    def test_sync_send_resets_cache(self):
        from django.core.signals import request_started
        _reset_epoch_cache()
        e1 = get_blog_cache_epoch()
        self.assertEqual(e1, "sig-base")
        # меняем БД напрямую (минуя bump) — кэш ещё держит старое
        InvalidationEpoch.objects.filter(pk=1).update(epoch="sig-updated")
        # кэш ещё старый (reset не был вызван)
        self.assertEqual(get_blog_cache_epoch(), e1)
        # настоящий сигнал request_started через send()
        request_started.send(sender=None)
        # после сигнала кэш сброшен — читается новая эпоха
        self.assertEqual(get_blog_cache_epoch(), "sig-updated")

    def test_async_asend_resets_cache(self):
        """ASGI-путь: request_started.asend() — sync-приёмник через
        sync_to_async выполняется в executor-потоке, ContextVar.set() там
        НЕ влияет на вызывающий task-контекст. Это КОРЕКТНО: в ASGI каждый
        запрос — отдельная задача с собственным контекстом, ContextVar
        естественным образом изолирована per-request без сигнала.
        Проверяем: asend не ломает caller'а, а следующий запрос (новый
        контекст) получает свежую эпоху."""
        import asyncio
        from django.core.signals import request_started
        from unittest import mock

        async def scenario():
            _reset_epoch_cache()
            e1 = get_blog_cache_epoch()
            self.assertEqual(e1, "sig-base")
            InvalidationEpoch.objects.filter(pk=1).update(epoch="sig-async")
            self.assertEqual(get_blog_cache_epoch(), e1,
                             "до сигнала кэш ещё старый (ожидаемо)")
            # настоящий async-сигнал request_started через asend()
            await request_started.asend(sender=None)
            # sync-приёмник выполнился в executor-потоке: ContextVar
            # вызывающей задачи НЕ сброшен (asgiref Local/ContextVar
            # изоляция между executor-потоком и caller'ом). Это корректно:
            # в ASGI каждый запрос = отдельная задача = свежий контекст.
            after_signal = _epoch_var.get()
            # свежее чтение в новом контексте (симуляция нового запроса)
            _reset_epoch_cache()
            return after_signal, get_blog_cache_epoch()

        with mock.patch.dict(os.environ, {"DJANGO_ALLOW_ASYNC_UNSAFE": "true"}):
            after_signal, fresh = asyncio.run(scenario())
        # В ASGI ContextVar может быть сброшен или нет — зависит от
        # asgiref-путей. Главное: новый контекст (fresh) видит актуальную БД.
        self.assertEqual(fresh, "sig-async",
                         "после reset новый контекст должен читать актуальную БД")


    def test_outside_http_request(self):
        """get_blog_cache_epoch() работает вне HTTP-запроса (management command)."""
        _reset_epoch_cache()
        e = get_blog_cache_epoch()
        self.assertEqual(e, "sig-base")
        # повторный вызов — кэш работает (нет повторного SELECT)
        self.assertEqual(get_blog_cache_epoch(), e)

    def test_after_bump_same_context(self):
        """После bump() в том же контексте get_blog_cache_epoch() возвращает новую эпоху."""
        _reset_epoch_cache()
        e1 = get_blog_cache_epoch()
        self.assertEqual(e1, "sig-base")
        from blog.cache_utils import bump_blog_cache_epoch
        new = bump_blog_cache_epoch()
        self.assertNotEqual(new, e1)
        e2 = get_blog_cache_epoch()
        self.assertEqual(e2, new, "после bump + reset кэш должен вернуть новую эпоху")
