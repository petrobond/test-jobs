# GitHub Setup: CI/CD с обязательными тестами при PR

> Проект: brainbox_I · Роль: CTO / Founding Engineer  
> Цель: автоматизировать проверку качества кода при каждом pull request, чтобы AI-генерируемый код не попадал в main без верификации

---

## 1. Общая схема

```
Разработчик (или AI-агент) создаёт ветку
        │
        ▼
Открывает Pull Request в main
        │
        ▼
GitHub Actions запускает пайплайн:
  1. Линтинг (ruff / eslint)
  2. Type check (mypy / tsc)
  3. Юнит-тесты (pytest / jest)
  4. E2E-тесты (Playwright)
  5. Проверка контрактов API (OpenAPI diff)
        │
        ▼
Все проверки пройдены ✅ → можно мёржить
Хотя бы одна упала ❌ → мёрж заблокирован
```

---

## 2. Структура репозитория

```
/platform
  .github/
    workflows/
      ci.yml              # основной пайплайн
      pr-title-check.yml   # валидация названия PR
    CODEOWNERS             # обязательное ревью
  /api/                   # FastAPI backend
  /frontend/              # Next.js frontend
  /docs/                 # ADR, архитектурные решения
  /specs/               # OpenAPI-спектрация
  Makefile              # единая точка входа
  docker-compose.yml
  .env.example
```

---

## 3. Файл `.github/workflows/ci.yml`

```yaml
name: CI

on:
  pull_request:
    branches: [main]
    types: [opened, synchronize, reopened]
  push:
    branches: [main]

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

jobs:

  # ── Линтинг ──
  lint:
    name: Lint
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - run: pip install ruff
      - run: ruff check api/

      - uses: actions/setup-node@v4
        with: { node-version: '20' }
      - run: cd frontend && npm ci
      - run: cd frontend && npx eslint . --ext .ts,.tsx

  # ── Type check ──
  typecheck:
    name: Type Check
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - run: pip install mypy
      - run: mypy api/ --strict

      - uses: actions/setup-node@v4
        with: { node-version: '20' }
      - run: cd frontend && npm ci
      - run: cd frontend && npx tsc --noEmit

  # ── Юнит-тесты ──
  unit:
    name: Unit Tests
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_USER: test
          POSTGRES_PASSWORD: test
          POSTGRES_DB: test
        ports: ['5432:5432']
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
      redis:
        image: redis:7-alpine
        ports: ['6379:6379']
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - run: pip install -r api/requirements.txt
      - run: cd api && pytest --cov=. --cov-report=xml --junitxml=junit.xml
      - uses: actions/setup-node@v4
        with: { node-version: '20' }
      - run: cd frontend && npm ci
      - run: cd frontend && npx jest --coverage

  # ── E2E-тесты ──
  e2e:
    name: E2E Tests
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_USER: test
          POSTGRES_PASSWORD: test
          POSTGRES_DB: test
        ports: ['5432:5432']
      redis:
        image: redis:7-alpine
        ports: ['6379:6379']
    steps:
      - uses: actions/checkout@v4
      - run: docker compose up -d --wait
      - run: pip install playwright
      - run: playwright install --with-deps chromium
      - run: pytest tests/e2e/ --base-url http://localhost:3000
      - run: docker compose down

  # ── Проверка контрактов API ──
  openapi-diff:
    name: API Contract Check
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - run: pip install openapi-spec-validator
      # Валидация спекы
      - run: openapi-spec-validator api/openapi.json
      # Проверка breaking changes (если есть предыдущая версия)
      - name: Check for breaking changes
        run: |
          git show origin/main:api/openapi.json > old_spec.json 2>/dev/null || echo "{}" > old_spec.json
          pip install openapi-diff
          openapi-diff old_spec.json api/openapi.json || true
```

---

## 4. Файл `.github/workflows/pr-title-check.yml`

```yaml
name: PR Title Check

on:
  pull_request:
    branches: [main]
    types: [opened, edited, synchronize, reopened]

jobs:
  check-title:
    name: Validate PR title
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: amannn/action-semantic-pull-request@v5
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        with:
          types: |
            feat
            fix
            docs
            refactor
            test
            chore
            ci
          requireScope: false
          disallowScopes: |
            release
```

**Формат названий PR:**
- `feat: добавлен AI-матчинг менторов`
- `fix: исправлена валидация email в форме фаундера`
- `docs: обновлён ADR по выбору LLM-провайдера`
- `test: добавлены e2e-тесты для Monthly Update`

---

## 5. Файл `.github/CODEOWNERS`

```
# CTO — обязательное ревью на все изменения
*       @cto

# Архитектурные решения — только CTO
/specs/   @cto
/docs/adr/ @cto

# Критические модули — CTO + maintainer
/api/     @cto @maintainer-api
/frontend/ @cto @maintainer-frontend
/matching/ @cto @maintainer-matching
```

**Правило:** без аппрува от CTO (или maintainer-а модуля) мёрж в main невозможен.

---

## 6. Branch protection rules (настройка в GitHub UI)

Settings → Branches → Add rule для `main`:

| Правило | Значение |
|---------|----------|
| **Require a pull request before merging** | ✅ |
| **Require approvals** | 1 (CTO или maintainer) |
| **Dismiss stale approvals** | ✅ |
| **Require status checks to pass before merging** | ✅ |
| **Status checks:** | `lint`, `typecheck`, `unit`, `e2e`, `openapi-diff` |
| **Require conversation resolution before merging** | ✅ |
| **Require branches to be up to date** | ✅ |
| **Do not allow bypassing the above settings** | ✅ (для всех, включая админов) |

---

## 7. Локальный запуск (Makefile)

```makefile
.PHONY: lint test e2e check-all

lint:
	ruff check api/
	cd frontend && npx eslint . --ext .ts,.tsx

typecheck:
	mypy api/ --strict
	cd frontend && npx tsc --noEmit

test:
	cd api && pytest --cov=. -x
	cd frontend && npx jest --coverage

e2e:
	docker compose up -d --wait
	pytest tests/e2e/ --base-url http://localhost:3000
	docker compose down

check-all: lint typecheck test
	@echo "✅ All checks passed"

openapi-validate:
	openapi-spec-validator api/openapi.json
```

**Использование:** `make check-all` перед пушем — те же проверки, что и в CI.

---

## 8. Что происходит при провале тестов

| Ситуация | Действие |
|----------|----------|
| Линтинг упал | PR помечается `❌ lint failed`. Разработчик правит и пушит исправление |
| Type check упал | PR блокирован. Нельзя мёржить с type-ошибками |
| Юнит-тесты упали | CI показывает diff. Разработчик чинит тесты или код |
| E2E упали | CI сохраняет скриншоты и логи как артефакты. Разработчик анализирует |
| OpenAPI diff показал breaking change | CI предупреждает (не блокирует). Требуется явное одобрение CTO |

---

## 9. AI-генерируемый код и тесты

**Принцип:** AI генерирует код и тесты одновременно. CTO ревьюит оба.

**Как это работает в пайплайне:**
1. CTO описывает задачу в issue: «Добавить эндпоинт POST /api/monthly-update с полями transcript_text, founder_id»
2. AI-агент генерирует:
   - FastAPI-эндпоинт
   - Pydantic-схему
   - pytest-тесты (3 теста: happy path, валидация, 401)
   - Фронтенд-компонент (форма загрузки + polling статуса)
3. AI создаёт PR с меткой `ai-generated`
4. CI прогоняет все проверки
5. CTO ревьюит, при необходимости правит промпт и перегенерирует
6. CTO мёржит

**Ключевое правило:** AI-генерированный код без прохождения CI не попадает в main. Тесты — это контракт между AI и человеком.

---

## 10. Метрики качества

| Метрика | Цель | Как измерять |
|---------|------|-------------|
| **Coverage** | > 80% | pytest --cov + jest --coverage |
| **CI pass rate** | > 90% | GitHub Actions dashboard |
| **Time to merge** | < 24 часа | GitHub metrics |
| **AI-generated PR pass rate** | > 80% с первого раза | Метка `ai-generated` + статус CI |
| **Breaking changes detected** | 0 пропущенных | OpenAPI diff в CI |

---

## 11. Roadmap: что добавить позже

- [ ] **Playwright-скриншоты** при падении E2E (артефакты в CI)
- [ ] **Автоматический деплой** в staging после мёржа в main (GitHub Actions + Docker)
- [ ] **Dependabot** для автоматического обновления зависимостей
- [ ] **CodeQL** для статического анализа безопасности
- [ ] **Pre-commit hooks** (ruff, mypy) — зеркало CI на локальной машине
- [ ] **Авто-лейблинг PR** по типу изменений (frontend, backend, docs, ai-generated)