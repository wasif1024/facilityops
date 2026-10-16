# FacilityOps AI — Web app

The Phase 1 frontend: React 19, TypeScript, Vite, Tailwind CSS 4 and shadcn/ui, talking to the backend over
**Connect RPC** with a client generated from the shared protobuf contracts in [`../proto`](../proto).

What it does, screen by screen, is specified in
[docs/frontend-specification.md](../docs/frontend-specification.md).

## Run it

Requires **Node 24** (`nvm use` reads [`.nvmrc`](.nvmrc)) and the backend on `http://localhost:8000`, either
natively or with `docker compose up -d` from the repository root.

```bash
nvm use
npm install
cp .env.example .env.local     # defaults work for local development
npm run dev                    # http://localhost:5173
```

In development there is no sign-in: pick who you are with the **identity switcher** in the header. New names
start as tenants; give roles with the backend CLI, for example:

```bash
python -m backend.app.cli grant-role --provider development --external-id fm --role facility_manager
python -m backend.app.cli grant-role --provider development --external-id tech --role technician
```

## Scripts

| Command | What it does |
|---|---|
| `npm run dev` | Dev server with hot reload |
| `npm run build` | Typecheck and production build into `dist/` |
| `npm run test` | Unit and component tests (Vitest, jsdom, an in-memory Connect backend) |
| `npm run lint` | oxlint |
| `npm run typecheck` | `tsc` without emitting |
| `npm run generate` | Regenerate `src/gen` from `../proto` after a contract change |

## How it fits together

- **API:** `src/gen` holds generated messages and service descriptors; screens call them through
  Connect-Query hooks (`useQuery(WorkOrderService.method.listWorkOrders, …)`), so requests and responses are
  typed end to end. The transport sends binary protobuf and adds the identity header.
- **Access:** the session is `AccountService.GetMe`. Navigation, routes and buttons appear only with the
  required permission (`src/navigation.ts`, `src/routes.tsx`); the backend enforces everything again.
- **Personal data:** placeholders such as `[PII_0001]` render as chips. Revealing calls the audited
  `RevealRequestDetails` RPC and keeps the values only in component state, cleared after two minutes; they never
  reach the query cache, browser storage or the URL.
- **Tests** use Connect's `createRouterTransport`, an in-memory server, so components run their real hooks
  without a network (`src/test/fake-backend.ts`).
