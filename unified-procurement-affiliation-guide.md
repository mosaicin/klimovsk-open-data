# Практическое объединение данных ЕИС и аффилированности

## Рекомендуемый процесс

Сначала загрузите данные ЕИС в отдельные таблицы `procurements`, `contracts`, `participants`, `participations` и `bids`. Затем загрузите корпоративные связи, технические индикаторы и документы в отдельные таблицы. Только после этого строится аналитическое представление. Это предотвращает размножение строк: один контракт может иметь несколько документов, один участник — много процедур, а одна пара участников — много индикаторов.

## Нормализация

ИНН и ОГРН хранятся как строки, включая ведущие нули; даты приводятся к ISO 8601 с часовым поясом; суммы — к числовому типу с двумя знаками; названия очищаются от двойных пробелов, но исходное написание сохраняется отдельным полем. Реестровые номера никогда не следует извлекать из сокращённого названия, если они доступны в отдельном поле.

Пример на pandas:

```python
import pandas as pd

proc = pd.read_csv("procurements.csv", dtype={"procurement_id": "string", "customer_inn": "string"})
part = pd.read_csv("participations.csv", dtype={"procurement_id": "string", "participant_inn": "string"})
links = pd.read_csv("corporate_links.csv", dtype={"subject_a_inn": "string", "subject_b_inn": "string"})

proc["published_at"] = pd.to_datetime(proc["published_at"], utc=True, errors="coerce")
proc["nmck"] = pd.to_numeric(proc["nmck"], errors="coerce")
part["final_price"] = pd.to_numeric(part["final_price"], errors="coerce")
part["reduction_pct"] = 100 * (proc.set_index("procurement_id")["nmck"] - part["final_price"]) / proc.set_index("procurement_id")["nmck"]

# Дедупликация только после проверки конфликтов.
part = part.drop_duplicates(["procurement_id", "participant_inn", "role", "final_price"])

view = part.merge(proc, on="procurement_id", how="left", validate="many_to_one")
```

Перед расчётом `reduction_pct` проверяйте, что НМЦК и итоговая цена относятся к одному лоту и одной единице измерения. Для многолотовых процедур расчёт делается по `lot_id`.

## Канонизация пар

Чтобы пара участников не дублировалась как A–B и B–A:

```python
links["pair_key"] = links.apply(
    lambda r: "__".join(sorted([str(r["subject_a_inn"]), str(r["subject_b_inn"])])),
    axis=1,
)
```

Агрегируйте число процедур и индикаторов по `pair_key`, но исходные строки и источники сохраняйте. Если связь действует только в определённый период, учитывайте `valid_from` и `valid_to`; нельзя применять текущего руководителя к закупке прошлых лет без проверки исторической записи.

## SQL-представление

```sql
CREATE VIEW procurement_affiliation_view AS
SELECT
    p.procurement_id,
    c.contract_id,
    pa.participant_inn,
    pa.participant_name,
    p.customer_inn,
    p.published_at,
    p.subject,
    p.okpd2,
    p.nmck,
    pa.final_price,
    100.0 * (p.nmck - pa.final_price) / NULLIF(p.nmck, 0) AS reduction_pct,
    pa.role,
    pa.rank,
    COUNT(DISTINCT cl.link_id) AS corporate_link_count,
    COUNT(DISTINCT ti.indicator_id) AS technical_indicator_count,
    COUNT(DISTINCT d.document_id) AS source_document_count
FROM procurements p
LEFT JOIN contracts c ON c.procurement_id = p.procurement_id
JOIN participations pa ON pa.procurement_id = p.procurement_id
LEFT JOIN corporate_links cl
  ON cl.subject_a_inn = pa.participant_inn
  OR cl.subject_b_inn = pa.participant_inn
LEFT JOIN technical_indicators ti ON ti.procurement_id = p.procurement_id
LEFT JOIN documents d
  ON d.entity_id IN (p.procurement_id, c.contract_id)
GROUP BY p.procurement_id, c.contract_id, pa.participant_inn,
         pa.participant_name, p.customer_inn, p.published_at,
         p.subject, p.okpd2, p.nmck, pa.final_price, pa.role, pa.rank;
```

В производственной базе лучше агрегировать связи и документы в отдельных CTE до соединения, иначе множественные `JOIN` могут умножить строки и завысить счётчики. После построения представления сравните число процедур, контрактов и участников с исходными таблицами.

## Контроль качества

| Проверка | Ожидаемый результат |
|---|---|
| Уникальность `procurement_id` | одна процедура в `procurements` |
| Связь контракта | каждый контракт связан с одной процедурой или явно помечен как сирота |
| НМЦК | положительное число или объяснимый пропуск |
| ИНН | 10 или 12 цифр после проверки типа участника |
| Время ставок | валидная дата, часовой пояс и источник |
| Документы | URL, дата скачивания, SHA-256 |
| Связи | тип связи, период, источник и evidence level |
| Индикаторы | метод, порог, значение и альтернативное объяснение |

## Как строить итоговую аналитику

Сначала показывайте факты: сколько процедур, контрактов и участников, какие цены и статусы. Затем отдельным блоком показывайте индикаторы: повторяемость пары, ротацию, синхронность и корпоративные совпадения. В третьем блоке показывайте уровень доказательности и список источников. Не преобразуйте эти блоки в единый «процент вины».

Для каждого индикатора должна быть запись: `indicator_type`, `value`, `method`, `threshold`, `source_document_id`, `alternative_explanation`, `status`. Статус может быть `наблюдается`, `требует проверки`, `опровергнуто`, `подтверждено документом` или `не оценено`.

## Источники

[1]: https://zakupki.gov.ru/epz/order/extendedsearch/search.html "ЕИС: расширенный поиск"
[2]: https://zakupki.gov.ru/epz/contract/search/results.html "ЕИС: реестр контрактов"
[3]: https://egrul.nalog.ru/index.html "ФНС: ЕГРЮЛ"
[4]: https://fas.gov.ru/documents/575635 "ФАС России: методы выявления картелей"
