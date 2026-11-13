"""Д68-досье: захват фактической серверной метки (application_name) соединений
тестового прогона. Опрашивает pg_stat_activity, пока идёт указанный процесс.

Использование: label_monitor.py <PID_прогона> <файл_вывода> <имя_тестовой_БД>
"""
import os
import sys
import time

import psycopg2

run_pid, out_path, test_db = sys.argv[1], sys.argv[2], sys.argv[3]
pg = psycopg2.connect(dbname="postgres", user=os.environ.get("PGUSER", "a1020391_django_radio"),
                      password=os.environ.get("PGPASSWORD"), host="127.0.0.1", port=5432)
pg.set_session(autocommit=True)
seen = {}
with open(out_path, "w") as f:
    f.write(f"monitor: run_pid={run_pid} test_db={test_db}\n")
    while True:
        try:
            os.kill(int(run_pid), 0)
        except (ProcessLookupError, ValueError):
            f.write("monitor: run process exited\n")
            break
        with pg.cursor() as cur:
            cur.execute("SELECT pid, application_name, state, query FROM pg_stat_activity "
                        "WHERE datname = %s", [test_db])
            for pid, app, state, query in cur.fetchall():
                key = (pid, app)
                if key not in seen:
                    seen[key] = True
                    line = (f"sample pid={pid} application_name={app!r} state={state} "
                            f"last_query={(query or '')[:100]!r}")
                    f.write(line + "\n")
        time.sleep(0.2)
