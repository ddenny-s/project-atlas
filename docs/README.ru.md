# Техническая документация Project Atlas

[Русский](./README.ru.md) · [English](./README.md) · [Описание продукта](../README.ru.md)

Это третий уровень публичной документации. Если детали протокола пока не
нужны, начни с объяснения
[за 30 секунд](../README.ru.md#за-30-секунд) или
[первого запуска](../README.ru.md#за-5-минут-до-первого-запроса).

## Карта источников истины

| Вопрос | Авторитетный источник |
| --- | --- |
| Что означают метки доказательств и этапы | [`core/PROTOCOL.md`](../core/PROTOCOL.md) |
| Как устроены claims, трассировка, покрытие и ревью | [`methodology.ru.md`](./methodology.ru.md) |
| Как выбираются QUICK, STANDARD и FORENSIC | [`depth-levels.ru.md`](./depth-levels.ru.md) |
| Что обязан содержать каждый артефакт | [`outputs.ru.md`](./outputs.ru.md) |
| Как Codex и Claude Code упаковывают протокол | [`adapters.ru.md`](./adapters.ru.md) |
| Как запускать типовые сценарии | [`examples.ru.md`](./examples.ru.md) |
| Как карта превращается в проверенное изменение | [`case-study.ru.md`](./case-study.ru.md) |
| Что можно читать, менять и публиковать | [`SECURITY.md`](../SECURITY.md) |
| Что benchmark может и не может утверждать | [Эффективность и benchmark](./effectiveness.ru.md) |

`core/PROTOCOL.md` — нормативный контракт. Документы его объясняют, а адаптеры
переводят discovery и permissions конкретного host. Ни один адаптер не может
переопределять метки доказательств, смысл глубины, контракт результата или
границы безопасности.

## Протокол одним маршрутом

```text
BOUND
  корень · исключения · цель продукта · цена ошибки
    ↓
FORECAST
  min · обычно · max модельных токенов для следующего блока
    ↓
DISCOVER + CLASSIFY
  runtime · данные · состояние · права · тесты · риски
  CONFIRMED · INFERENCE · HYPOTHESIS · TARGET · UNKNOWN
    ↓
ALIGN
  ревью владельца · адаптивные вопросы · исправленные границы
    ↓
DELIVER
  карта · Future Tasks · handoff · validation
    ↓
USE
  Task Context Packet · проверка ссылок · изменение · тесты · refresh
```

## Границы

- Разрешение на картирование не разрешает реализацию, инфраструктурные правки,
  доступ к production или разрушительные команды.
- `TARGET` описывает желаемое будущее и не выдаётся за текущее состояние.
- `USER_INPUT` подтверждает намерение владельца, но не поведение текущего кода.
- Структурная проверка не доказывает смысловую истинность, полноту или
  production readiness.
- Следующая задача перепроверяет свежесть источников и получает ограниченный
  Task Context Packet, а не всю карту по умолчанию.
- Unknown остаётся открытым, пока новое доказательство его не закроет.

## Воспроизводимые проверки

Из корня репозитория:

```bash
python3 scripts/sync_adapters.py --check
python3 -m unittest discover -s tests -v
git diff --check
```

Только публичный пример «карта → изменение»:

```bash
python3 -m unittest tests.test_documentation_case_study -v
```

Входные данные расчётной модели остаются версионированы как benchmark
`v0.1.0`: релиз v0.1.1 меняет документацию и package metadata, но не dataset и
не поведение протокола.

## Что читать под конкретную работу

| Работа | Маршрут |
| --- | --- |
| Первая небольшая карта | [Примеры](./examples.ru.md) → [QUICK](./depth-levels.ru.md#quick) |
| Рефакторинг или миграция | [STANDARD](./depth-levels.ru.md#standard) → [Результаты](./outputs.ru.md) |
| Рискованный аудит | [FORENSIC](./depth-levels.ru.md#forensic) → [Методология](./methodology.ru.md) |
| Новый нативный адаптер | [Протокол](../core/PROTOCOL.md) → [Адаптеры](./adapters.ru.md) |
| Проверка заявленной пользы | [Сквозной пример](./case-study.ru.md) → [Benchmarks](../benchmarks/) |

Project Atlas — проект сообщества, а не официальный продукт OpenAI или
Anthropic.
