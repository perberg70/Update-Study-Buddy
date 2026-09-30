# AGENTS.md

## 1. Project Context & Stack
- **Repository:** Payment & Checkout Microservice
- **Runtime:** Node.js 22 LTS
- **Package Manager:** `pnpm` (version 9.x) - do NOT use `npm` or `yarn`.
- **Frameworks:** Fastify, TypeScript 5.5, Prisma ORM, Vitest.

---

## 2. Environment & Commands
Agents should run all commands from the repository root.

- **Dependency Installation:** `pnpm install --frozen-lockfile`
- **Build / Typecheck:** `pnpm build` (Runs `tsc --noEmit`)
- **Lint & Format:** `pnpm lint` && `pnpm format:check`
- **Auto-Fix Format:** `pnpm format`
- **Local Dev Server:** `pnpm dev`

---

## 3. Testing Workflows
Always execute relevant tests before submitting or declaring a task complete.

- **Run all unit tests:** `pnpm test:unit`
- **Run a single test file:** `pnpm vitest run src/modules/billing/invoice.test.ts`
- **Run with coverage:** `pnpm test:coverage`
- **E2E / Integration tests:** `pnpm test:e2e` (Requires local Docker dependencies via `docker compose up -d`)

---

## 4. Directory Structure
```text
src/
├── api/          # Route handlers and input schema validation (Zod)
├── core/         # Business logic and domain entities (framework-agnostic)
├── db/           # Prisma client, migrations, and seed scripts
└── utils/        # Shared helper functions and custom error classes
tests/
├── fixtures/     # Mock data and test helpers
└── integration/  # API-level end-to-end test suites
```

---

## 5. Architectural Rules & Patterns
- **Typing:** Strict TypeScript only. Avoid `any` under all circumstances. Use `unknown` with type guards if types are unpredictable.
- **Validation:** All incoming request bodies must be validated with Zod schemas in `src/api/schemas/`.
- **Error Handling:** Throw domain-specific errors extending `AppError` from `src/utils/errors.ts`. Never let raw database errors bubble to the HTTP client.
- **Async/Await:** Prefer `async/await` syntax over raw Promise chaining (`.then()`).

---

## 6. Agent Guardrails & Constraints
- **Database Migrations:** Do NOT create or run Prisma migrations (`pnpm prisma migrate dev`) unless the prompt explicitly requests a schema change.
- **Dependencies:** Do NOT add new third-party packages without explicit instructions.
- **Protected Files:** Do not modify `.github/workflows/`, `docker-compose.yml`, or production environment templates (`.env.production`).
- **Git Protocol:** Keep commits atomic with conventional commit format (e.g., `fix(billing): handle null card tokens`).
