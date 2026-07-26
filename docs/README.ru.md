# Project Atlas

Project Atlas — независимый от конкретного AI-инструмента протокол, который создаёт проверяемую карту программного проекта. Карта объясняет назначение продукта, runtime, движение данных, состояние, полномочия, пользовательские потоки, реальные границы тестов, риски, целевую архитектуру и безопасный путь миграции.

Codex-адаптер — основной и полный. Адаптер Claude Code — вторичная интеграция и первый пример переноса протокола на другой AI-инструмент. Оба адаптера используют одну методологию, модель доказательств, правила глубины и контракт результатов.

[English README](../README.md)

> **Community-проект.** Project Atlas не является официальным продуктом OpenAI или Anthropic. Перед важными изменениями проверяйте созданные документы. Карта не заменяет runtime-тесты, мониторинг, резервные копии, процедуры восстановления, security review и человеческое решение в критичных областях.

## Чем карта отличается от README

README обычно описывает, как проект задуман и как им пользоваться. Atlas исследует, как текущая система подтверждённо устроена. Он:

- отделяет подтверждённый факт от вывода, гипотезы, целевого предложения и неизвестного;
- связывает существенные утверждения с файлами, конфигурацией, схемами, тестами, командами или runtime-наблюдениями;
- разделяет текущую и целевую архитектуру;
- фиксирует границы покрытия и открытые вопросы;
- оставляет handoff, по которому следующая сессия продолжит работу без полного повторного чтения репозитория.

Project Atlas полезен перед рефакторингом, при передаче проекта, расследовании legacy-системы, поиске источников истины, проверке потоков данных и полномочий, а также при обновлении устаревшей документации. Для маленького очевидного скрипта он может быть избыточным — используйте QUICK или ограничьтесь обычным README.

## Глубина исследования

| Режим | Когда применять | Результат |
| --- | --- | --- |
| **QUICK** | Маленький, низкорисковый или одноразовый проект | Один компактный `PROJECT_ATLAS.md` |
| **STANDARD** | Живое приложение, сервис или библиотека средней сложности | Маршрутизируемые документы по текущему состоянию, потокам, рискам, целевой архитектуре, миграции и handoff |
| **FORENSIC** | Критичная, старая, многосервисная или запутанная система | Модульные документы, полные реестры, traceability, количественное покрытие, воспроизводимые проверки и независимое ревью |

Размер репозитория — только один фактор. Также учитываются цена ошибки, production-критичность, количество runtime-процессов и хранилищ, чувствительность данных, автоматические решения, сложность полномочий, legacy-контуры, размер команды и ожидаемый срок жизни продукта. Автовыбор сохраняет вспомогательные контуры доступными для проверки, но не считает слова из tests, fixtures, templates, examples или вложенной документации свойствами продукта. Каждый завершённый Atlas фиксирует, кто выбрал глубину, какие автоматические сигналы конфликтовали с решением, какое покрытие намеренно исключено и при каком условии нужна эскалация.

Подробнее: [уровни глубины](depth-levels.md) и [методология](methodology.md).

## Что создаётся

Без явно заданного пути:

- QUICK пишет `./PROJECT_ATLAS.md`;
- STANDARD и FORENSIC используют `./docs/project-atlas/`, если `docs/` уже является каталогом пользовательской документации;
- иначе STANDARD и FORENSIC используют `./project-atlas/`.

Явный путь результата всегда имеет приоритет. Существующие документы сначала читаются и затем обновляются инкрементально; молчаливая перезапись запрещена. Точный состав файлов зависит от режима и описан в [контракте результатов](outputs.md).

## Установка и использование

Marketplace — основной способ распространения. CI проверяет форму пакетов, синхронность адаптеров и изолированную standalone-установку. После отправки кандидата maintainers также запускают приведённые ниже Codex- и Claude-команды в одноразовых чистых профилях; этот host-CLI гейт выполняется вручную до создания version tag и GitHub Release и не заявляется как проверка GitHub Actions.

Встроенный helper и служебные скрипты требуют Python 3.10 или новее. Standalone-установщики также требуют Bash, `diff` и POSIX directory-descriptor primitives, доступные на macOS и Linux; native Windows standalone-установка не поддерживается. Требования marketplace-установки определяет выбранный AI-хост.

### Codex — основной адаптер

```bash
codex plugin marketplace add ddenny-s/project-atlas
codex plugin add project-atlas@project-atlas
```

Проверить список установленных плагинов:

```bash
codex plugin list --marketplace project-atlas --json
```

Явный вызов:

```text
Используй $project-atlas:map-project и создай STANDARD-карту этого репозитория.
```

Если навык не появился, начните новую сессию Codex. Для обновления сначала обновите snapshot marketplace, затем переустановите плагин:

```bash
codex plugin marketplace upgrade project-atlas
codex plugin remove project-atlas@project-atlas
codex plugin add project-atlas@project-atlas
```

В текущем CLI Codex используется связка refresh и remove/add; отдельной команды обновления установленного плагина нет.

Удаление:

```bash
codex plugin remove project-atlas@project-atlas
codex plugin marketplace remove project-atlas
```

### Claude Code — вторичный адаптер

```bash
claude plugin marketplace add ddenny-s/project-atlas
claude plugin install project-atlas@project-atlas
```

Проверка и вызов:

```bash
claude plugin details project-atlas@project-atlas
```

```text
/project-atlas:map-project
```

Обновление и удаление:

```bash
claude plugin marketplace update project-atlas
claude plugin update project-atlas@project-atlas
```

Чтобы загрузить обновлённый плагин, перезапустите Claude Code.

```bash
claude plugin uninstall project-atlas@project-atlas
claude plugin marketplace remove project-atlas
```

### Standalone Skill для Codex

Актуальный пользовательский каталог standalone-навыков — `$HOME/.agents/skills`:

```bash
git clone https://github.com/ddenny-s/project-atlas.git
cd project-atlas
./scripts/install.sh --user-scope
```

Без `--user-scope` установщик использует `${CODEX_HOME:-$HOME/.codex}/skills` как legacy-совместимый путь. Он не перезаписывает существующий навык без `--force`. Предыдущая user-scope версия сохраняется в `$HOME/.agents/.skill-backups/project-atlas/`, а legacy-версия — в `${CODEX_HOME:-$HOME/.codex}/.skill-backups/project-atlas/`. Standalone-вызов не содержит namespace плагина: `$map-project`.

При ручном копировании `adapters/codex/skills/map-project` также замените `$project-atlas:map-project` на `$map-project` в `agents/openai.yaml`; готовый `install.sh` делает это автоматически и проверяет установленное дерево.

Обновление user-scope установки из clone:

```bash
git pull --ff-only
./scripts/install.sh --user-scope --force
```

Удаление именно user-scope копии:

```bash
rm -r -- "$HOME/.agents/skills/map-project"
```

Для legacy-установки соответствующий target — `${CODEX_HOME:-$HOME/.codex}/skills/map-project`.

### Standalone Skill для Claude Code

Установка из того же clone в `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills/map-project`:

```bash
./scripts/install-claude.sh
```

Существующая копия без явного `--force` не перезаписывается. Обновление сохраняет предыдущую версию в backup:

```bash
git pull --ff-only
./scripts/install-claude.sh --force
```

Удаляйте только проверенный standalone-target:

```bash
atlas_claude_root="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
rm -r -- "$atlas_claude_root/skills/map-project"
```

Это отдельный lifecycle, не связанный с marketplace-плагином Claude Code. Standalone-вызов — `/map-project`, marketplace-вызов — `/project-atlas:map-project`.

## Примеры запросов

```text
Используй $project-atlas:map-project и создай QUICK-карту этого небольшого CLI.
```

```text
Используй $project-atlas:map-project и построй STANDARD-карту текущего и целевого состояния перед рефакторингом сервиса.
```

```text
Используй $project-atlas:map-project в режиме FORENSIC. Зафиксируй все runtime roots, хранилища данных, writers состояния, границы полномочий, recovery paths и пробелы тестов. Не вноси изменения, пока я не утвержу карту.
```

```text
Используй $project-atlas:map-project, инкрементально обнови существующую карту и покажи drift.
```

```text
Используй $project-atlas:map-project и подготовь continuation handoff для другой сессии Codex.
```

Для Claude Code замените invocation на `/project-atlas:map-project`. Дополнительные сценарии: [examples.md](examples.md).

## Безопасность и приватность

Project Atlas — workflow на основе инструкций, а не изолированный локальный анализатор. Содержимое репозитория может обрабатываться выбранным AI-хостом, поэтому применяются его условия и настройки данных.

- До поиска прочитайте инструкции проекта и список запрещённых путей.
- Не открывайте секреты, ключи, credentials, production-дампы и явно исключённые каталоги.
- Не публикуйте atlas до проверки путей, идентификаторов и чувствительной бизнес-информации.
- Запрос на карту не разрешает менять продуктовый код, данные, инфраструктуру или production.
- Сначала используйте дешёвый индекс и ограниченное чтение, затем углубляйтесь по доказанной необходимости.
- Встроенные скрипты анализа не требуют сети; сетевой доступ агента является отдельной возможностью и требует соответствующего разрешения хоста и пользователя.
- Независимо проверяйте высокорисковые выводы.

Уязвимости сообщаются по правилам [SECURITY.md](../SECURITY.md).

## Ограничения

- Код и зелёные тесты не доказывают поведение production.
- Dynamic dispatch, генерация кода, runtime-конфигурация и внешние сервисы могут скрывать важные связи.
- Утверждение о покрытии действует только в зафиксированных scope и evidence boundary.
- Traceability снижает, но не устраняет риск ошибки AI-агента.
- Детерминированная валидация доказывает структуру, существование bounded sources и replay команд, но не смысловую выводимость текста и не личность reviewer. Реальное независимое semantic review обеспечивает release governance.
- Карта устаревает вместе с кодом, конфигурацией, инфраструктурой и операционными процессами.
- Возможности и permissions конкретного адаптера ограничивают доступные проверки.
- Встроенный helper `atlas.py` требует безопасной POSIX-поддержки directory descriptors и no-follow. Он работает на macOS и Linux, а на native Windows останавливается fail-closed; сам протокол там можно выполнять ограниченными host-native инструментами, явно фиксируя этот verification gap.
- Для FORENSIC replay требуется доступный для записи временный каталог ОС: туда материализуется только allowlisted-копия доказательств. Исходники продукта остаются read-only; полностью read-only sandbox останавливает replay fail-closed.
- Обход safe inventory ограничен 100 000 файлами, 20 000 каталогами, глубиной 64 и 16 MiB UTF-8 байтов относительных путей; сериализованный JSON ограничен 8 MiB и для файла, и для stdout. Точная классификация `git check-ignore --no-index` выполняется в изолированном временном worktree со стабильными копиями только project-local `.gitignore` и metadata candidate paths, с ограниченным выводом и deadline 15 секунд. Исходные `.git` metadata, `info/exclude`, Git config и внешние excludes-файлы не читаются. Custom ignore поддерживает экранированные символы шаблонов и Git-семантику завершающих пробелов; нечитаемые, некорректные или неподдерживаемые in-scope metadata останавливают операцию fail-closed. Очистка дочерних процессов охватывает только процессы, оставшиеся в исходной POSIX process group; намеренно отделившийся процесс той же учётной записи находится вне этого containment boundary. Превышение останавливает операцию fail-closed без усечённого evidence.
- Standalone-установка ограничена 2 048 файлами, 512 каталогами, глубиной 32, размером 4 MiB на файл и 64 MiB суммарно. Каждый внешний `diff` ограничен 30 секундами; timeout или перехватываемый сигнал завершает его process group и запускает rollback. Превышение package ceiling останавливает публикацию fail-closed.
- При обычной ошибке или перехватываемом сигнале installer пытается откатиться. Но `SIGKILL`, отключение питания или сбой durability файловой системы может оставить stale lock или staging tree, отсутствующий target со старой копией в `.skill-backups/project-atlas/` либо неоднозначное состояние target/backup. Crash-recovery journal отсутствует. Убедитесь, что installer не работает, проверьте `skills/map-project`, `.skill-backups/project-atlas/` и `.map-project.install-*`, удалите через `rmdir` только проверенный пустой stale lock, затем вручную восстановите идентифицированный backup или повторите установку. Не выбирайте authoritative tree только по времени имени.
- Adapter sync ограничен глубиной 32, 2 048 каталогами, 4 096 файлами, 8 192 суммарными entries, 8 MiB на файл и 64 MiB суммарно. Записывающий режим подготавливает и проверяет оба бандла до координированного commit под repository lock. Journal восстанавливает зафиксированные прерывания, но повреждённый или отсутствующий journal после power loss всё равно требует ручной проверки. Write mode поддерживается на macOS и Linux и останавливается fail-closed на native Windows.
- `python3 scripts/sync_adapters.py --check` — state-free read-only comparison: команда не создаёт `.scratch`, lock, staging paths или recovery journal и останавливается fail-closed при уже существующем незавершённом transaction state. Перед публикацией она обязана завершиться успешно.
- Транзакционные installer и sync защищают публичные пути от обычных конкурирующих writers, но не изолируют вредоносный процесс, уже работающий от той же учётной записи ОС и имеющий прямой доступ на запись. При неожиданном сообщении о quarantine сначала вручную проверьте сохранённый путь и только затем повторяйте операцию.

## Разработка и тестирование

Каноническая методика находится в `core/`; адаптеры только переводят её в формат хоста. Изменение evidence labels, глубины, выходного контракта или safety-правил должно быть синхронизировано между адаптерами и покрыто conformance-тестами.

```bash
python3 -m unittest discover -s tests -v
git diff --check
```

Перед вкладом прочитайте [CONTRIBUTING.md](../CONTRIBUTING.md), [архитектуру адаптеров](adapters.md) и [ACKNOWLEDGEMENTS.md](../ACKNOWLEDGEMENTS.md).

Проект распространяется по [MIT License](../LICENSE).
