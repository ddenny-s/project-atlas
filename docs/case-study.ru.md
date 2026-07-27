# Сквозной пример: от карты до проверенного исправления

[Русский](./case-study.ru.md) · [English](./case-study.md)

Это маленький, полностью публичный пример полного цикла Atlas:

```text
CURRENT → карта → находка → TARGET-задача → изменение → проверка → новый CURRENT
```

Пример использует синтетический сервис посылок из
[`tests/fixtures/standard_service`](../tests/fixtures/standard_service). В нём
нет рабочих данных, ключей или внешней сети.

## 1. До карты

У сервиса два входа:

- API принимает новую посылку и отклоняет пустой `parcel_id` в
  [`api.py`](../tests/fixtures/standard_service/service/api.py#L8-L12);
- фоновый worker записывает результат доставки через
  [`worker.py`](../tests/fixtures/standard_service/service/worker.py#L25-L41);
- оба пути используют общий писатель SQLite из
  [`state.py`](../tests/fixtures/standard_service/service/state.py#L8-L21).

Проблема не видна, если открыть только API. Worker не повторяет проверку и
передаёт пустой идентификатор прямо в общее хранилище.

Воспроизводимый запуск из корня Project Atlas:

```bash
PYTHONPATH=tests/fixtures/standard_service \
PYTHONDONTWRITEBYTECODE=1 python3 - <<'PY'
import sqlite3
import tempfile
from pathlib import Path

from service.api import accept_parcel
from service.worker import process_delivery

database = Path(tempfile.mkdtemp()) / "probe.sqlite"
try:
    accept_parcel(database, "   ")
except ValueError as error:
    print(f"api blank: rejected ({error})")
else:
    raise AssertionError("API accepted a blank parcel_id")

process_delivery(database, "   ", lambda: "delivered")
row = sqlite3.connect(database).execute(
    "SELECT parcel_id, status, writer FROM parcel_state"
).fetchone()
print(f"worker blank: {row}")
PY
```

Фактический результат:

```text
api blank: rejected (parcel_id is required)
worker blank: ('   ', 'delivered', 'worker')
```

## 2. Что попадает в карту

Полная замороженная
[`before/PROJECT_ATLAS.md`](./case-study-artifacts/standard-service/before/PROJECT_ATLAS.md)
проходит финальную QUICK-проверку Atlas. Её главные утверждения:

```text
CURRENT · CONFIRMED · CLAIM-API-001
API отклоняет пустой parcel_id.
Источник: service/api.py:8-12

CURRENT · CONFIRMED · CLAIM-WORKER-002
Worker доходит до общего record_status без такой проверки.
Источники: service/worker.py:25-41, service/state.py:8-21

CURRENT · UNKNOWN · UNKNOWN-PROVIDER-ORDERING
Порядок событий провайдера после тайм-аута не установлен.
Источник: README.md:8-10
```

Карта не пишет «весь сервис сломан». Она фиксирует одну доказанную
несогласованность и сохраняет отдельное неизвестное про провайдера.

## 3. Из находки получается готовая задача

Карта «до» превращает несогласованность в ограниченную задачу:

```text
ATLAS-001 · TARGET · READY

Результат:
  Все пути записи отклоняют пустой parcel_id.

Почему:
  API уже проверяет идентификатор, worker — нет.

Граница:
  Общий писатель service/state.py и проверки двух входов.

Не входит:
  Ретраи провайдера, права администратора, схема статусов.

Приёмка:
  1. Пустой parcel_id отклоняется и из API, и из worker.
  2. Корректная доставка по-прежнему записывается.
  3. UNKNOWN-PROVIDER-ORDERING остаётся UNKNOWN.
```

Именно эта карточка делает карту полезной после создания: следующая сессия
получает причину, границы, критерии и ссылки, а не начинает исследование заново.

Перед правкой агент собирает ограниченный
[`Task Context Packet`](./case-study-artifacts/standard-service/ATLAS-001-context-packet.md):

```text
Task: ATLAS-001
CURRENT claims: CLAIM-API-001, CLAIM-WORKER-002
Sources: service/api.py, service/worker.py, service/state.py
Authority boundary: admin override не меняется
Related unknown: UNKNOWN-PROVIDER-ORDERING
Required checks: blank rejected; valid worker write preserved
Freshness: три ссылки перечитаны в текущем снимке
Excluded: provider retries, status design, deployment
```

Пакет показывает не только включённый, но и исключённый контекст. Поэтому
выбор нужной части большой карты можно повторить и оспорить.

## 4. Минимальное изменение в правильном месте

Проверка переносится в общий слой записи, потому что им пользуются оба входа:

Тест применяет точный версионированный
[`ATLAS-001.patch`](./case-study-artifacts/standard-service/ATLAS-001.patch),
опубликованный вместе с этим примером:

```diff
 def record_status(database: Path, parcel_id: str, status: str, *, writer: str) -> None:
     """Persistent state writer shared by the API and worker runtimes."""
+    if not parcel_id.strip():
+        raise ValueError("parcel_id is required")
     database.parent.mkdir(parents=True, exist_ok=True)
```

Это иллюстративный патч. Исходный fixture намеренно остаётся в состоянии
**до исправления**, чтобы любой человек мог повторить находку.

## 5. Повторная проверка

Публичный тест копирует fixture во временную папку, подтверждает состояние
«до», применяет показанное изменение только к временной копии, а затем
проверяет оба входа и корректные записи:

- пустой идентификатор отклонён и API, и worker;
- корректная API-посылка записана как `accepted`;
- корректная worker-посылка записана как `delivered`.

Фактический результат после патча:

```text
api blank: rejected (parcel_id is required)
worker blank: rejected (parcel_id is required)
valid: [('parcel-7', 'delivered', 'worker'),
        ('parcel-api', 'accepted', 'api')]
```

Запуск:

```bash
python3 -m unittest tests.test_documentation_case_study -v
```

Код проверки:
[`tests/test_documentation_case_study.py`](../tests/test_documentation_case_study.py).

## 6. Карта после изменения

После зелёной проверки
[`after/PROJECT_ATLAS.md`](./case-study-artifacts/standard-service/after/PROJECT_ATLAS.md)
обновляется и проходит тот же QUICK-валидатор на временной копии с патчем:

```text
CURRENT · CONFIRMED · CLAIM-STATE-003
Общий record_status отклоняет пустой parcel_id для API и worker.
Доказательство: tests/test_documentation_case_study.py

Квитанция задачи: ATLAS-001 · VERIFIED
Строка будущей задачи: ATLAS-001 · SUPERSEDED

CURRENT · UNKNOWN · UNKNOWN-PROVIDER-ORDERING
Без изменений: порядок событий провайдера после тайм-аута не доказан.

ATLAS-002 · TARGET · BLOCKED
Установить порядок событий до изменения логики ретраев.
```

`VERIFIED` записывается в квитанцию задачи. Каноническая строка Future Tasks
становится `SUPERSEDED`, а отдельное неизвестное остаётся открытым и блокирует
`ATLAS-002`. Успешное исправление идентификатора не выдаётся за ответ на вопрос
про провайдера, который никто не проверял.

Отдельный запуск проверки всей замороженной цепочки:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.test_documentation_case_study.DocumentationCaseStudyTests.test_frozen_atlas_lineage \
  -v
```

Atlas не просто создаёт документацию. Он удерживает цепочку:

```text
утверждение → источник → задача → изменение → проверка → обновлённое утверждение
```

Этот пример доказывает механику на одном синтетическом проекте. Он не
доказывает процент экономии Atlas на реальных репозиториях и не заменяет
проверку конкретного проекта человеком.
