# Автоматический запуск SQL-пайплайна ЕИС

## Что было изучено в репозиториях ksenianiglas

В открытых проектах `mapping_estonia`, `Tallinn_population_density`, `lastfm`, `facebook_message_stats` и `tennis_stats` нет готового Airflow/cron или SQL-пайплайна ЕИС. Переносимы общие идеи: явно фиксировать источник данных, отделять загрузку от очистки и агрегации, применять повторно используемые функции и строить отдельные пространственные/статистические слои. Код из этих проектов не копируется без проверки лицензии и адаптации.

## Две практические архитектуры

| Подход | Как работает | Плюсы | Ограничения |
|---|---|---|---|
| cron + Python watcher | cron каждые 15 минут проверяет папку `data/raw/eis`, считает SHA-256 и запускает фиксированный PostgreSQL ETL | простая установка, мало зависимостей, подходит одному серверу | слабее UI, ретраи и мониторинг нужно настроить отдельно |
| Airflow DAG | планировщик запускает DAG, DAG вызывает watcher и ETL, состояние и ошибки видны в Airflow | ретраи, журнал запусков, зависимости, UI и масштабирование | тяжелее установка и эксплуатация, нужен Airflow |

Для одного проекта с выгрузками раз в 15–60 минут обычно достаточно cron. Airflow оправдан, если появляются несколько источников, контрольные шаги, ретраи, уведомления, зависимости и аудит запусков.

## Папки

```text
data/raw/eis/              # новые CSV/JSON/XML, не редактировать
 data/state/eis_manifest.json # checksum и статус обработки
watch_eis_exports.py       # идемпотентный watcher
 eis_etl_postgres.sql       # фиксированный ETL
 dags/eis_exports_etl.py   # Airflow DAG
cron/eis-etl.cron          # cron-вариант
```

## Идемпотентность

Watcher считает SHA-256 каждого файла. Файл считается обработанным только при совпадении имени, checksum и статуса `processed` в manifest. Если обработка завершилась ошибкой, manifest не обновляется, поэтому следующий запуск попробует файл снова. `flock` или `max_active_runs=1` запрещает параллельную обработку. На стороне базы `ON CONFLICT` использует ключи `procurement_id`, `contract_id` и `bid_id`.

## Установка cron

Скопируйте проект на сервер, создайте каталог `data/raw/eis` и установите PostgreSQL-клиент. Перед запуском задайте `DATABASE_URL` в защищённом файле окружения, например `/etc/klimovsk-eis.env` с правами `0600`; секрет не должен находиться в публичном репозитории. Обёртка `run_eis_from_git.sh` сначала выполняет `git pull --ff-only`, затем запускает watcher.

```bash
mkdir -p /opt/klimovsk-open-data/data/raw/eis /opt/klimovsk-open-data/data/state
chmod 750 /opt/klimovsk-open-data/data/raw/eis
# Пример защищённого файла окружения:
# DATABASE_URL=postgresql://user:password@db.example:5432/procurement
# chmod 600 /etc/klimovsk-eis.env
# Перед установкой crontab загрузите переменную через системный env-file
# или адаптируйте строку cron так, чтобы она выполняла `source /etc/klimovsk-eis.env`.
crontab /opt/klimovsk-open-data/cron/eis-etl.cron
```

Проверить работу можно в dry-run режиме:

```bash
python3 watch_eis_exports.py \
  --input-dir data/raw/eis \
  --manifest data/state/eis_manifest.json \
  --sql eis_etl_postgres.sql \
  --dry-run
```

В текущей версии watcher считает новые файлы и вызывает фиксированный SQL. Между этими шагами должен быть адаптер, который загружает конкретный CSV/JSON/XML в нужную staging-таблицу через `COPY` или отдельный парсер. Названия полей ЕИС могут различаться, поэтому нельзя без проверки направлять любой файл напрямую в staging.

## Airflow

Скопируйте `dags/eis_exports_etl.py` в каталог DAGs, создайте Airflow Connection `eis_postgres`, а каталог `data/raw/eis` наполняйте коммитами в GitHub. DAG запускается каждые 15 минут, сначала делает `git pull --ff-only`, затем считает checksum и обрабатывает только новые или изменённые файлы. Параллельные DAG-запуски запрещены, а строка подключения берётся из Airflow Connection.

```bash
airflow connections add eis_postgres \
  --conn-type postgres \
  --conn-host db.example \
  --conn-schema procurement \
  --conn-login eis_loader \
  --conn-password 'use-secret-manager' \
  --conn-port 5432
```

Секреты не помещайте в DAG, `.env`, GitHub issue или публичный репозиторий. Используйте Secret Backend/переменные окружения защищённого сервиса. Для продакшена добавьте отдельные задачи `validate_raw`, `load_staging`, `run_etl`, `quality_checks` и `publish_metrics`; при ошибке качества DAG должен завершаться с ошибкой.

## Появление файлов в GitHub

Если новые выгрузки действительно появляются через commit в GitHub, лучше запускать workflow по событию `push` и вызывать обработчик на защищённом runner/сервере. Не храните в публичном репозитории закрытые документы, банковские данные, персональные данные или технические логи ЭТП. Если GitHub только хранит код, а выгрузки поступают в серверную папку, используйте cron/Airflow как описано выше.

## Журналирование и контроль

Каждый запуск должен писать время, имя файла, checksum, число строк до и после очистки, число новых и обновлённых ключей, число ошибок и версию ETL. Храните исходные файлы неизменяемыми, сохраняйте SHA-256 документов и не публикуйте приватные сырые данные. Для исправления файла создавайте новую версию, а не заменяйте старую без следа.

## Ограничения

Этот комплект не скачивает данные из ЕИС автоматически и не обходит ограничения сайта. Он обрабатывает файлы, которые оператор законно получил и положил в staging. Для полного массива 232 контрактов всё равно нужны официальный экспорт и адаптер фактического формата выгрузки.

## Источники

[1]: https://github.com/ksenianiglas "Профиль ksenianiglas"
[2]: https://github.com/ksenianiglas/mapping_estonia "mapping_estonia"
[3]: https://github.com/ksenianiglas/Tallinn_population_density "Tallinn_population_density"
[4]: https://github.com/ksenianiglas/lastfm "lastfm"
[5]: https://github.com/ksenianiglas/tennis_stats "tennis_stats"
[6]: https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/dags.html "Apache Airflow DAGs"
[7]: https://crontab.guru/ "Cron expression reference"
