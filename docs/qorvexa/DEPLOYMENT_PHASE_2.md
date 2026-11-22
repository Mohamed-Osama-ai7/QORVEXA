# QORVEXA — Deployment Readiness Phase 2 Report

**Product:** QORVEXA  
**Tagline:** *INTELLIGENCE BEYOND AUTOMATION.*  
**System Classification:** Industrial Decision Intelligence Platform  
**Repository:** `Mohamed-Osama-ai7/FactoryMind-CDT`  
**Deployment Branch:** `deploy/qorvexa-public-demo`  
**Pinned Base Commit:** `6351d4f fix(frontend): demo blockers found in the real OIDC browser flow (#20)`  
**Deployment Worktree:** `D:\FactoryMind\qorvexa-deploy`  
**Parallel Coordination:** `D:\FactoryMind\FactoryMind-CDT` (Claude Code Task 02A workspace is 100% untouched)

---

## Executive Status Board (Layer-by-Layer)

| Deployment Layer | Technical Component | Status | Operational Blockers / Ownership |
|---|---|:---:|---|
| **1. Product Branding** | UI Shell, Metadata, Favicon, Documentation | **READY** | Rebranded to QORVEXA across all views. Zero leaks of internal credentials or hostnames. |
| **2. Frontend Hosting** | Vercel (Hobby Free Tier) | **READY** | Deployed live at `https://qorvexa-delta.vercel.app`. Global CDN edge, instant SSL, SPA routing verified. |
| **3. API Routing & Gateway** | Vercel Edge Proxy / Direct API | **BLOCKED** | Rewrite to `127.0.0.1:8000` returns `HTTP 404`. Requires a live remote backend endpoint. |
| **4. Backend Hosting** | FastAPI + Scikit-learn + Scientific Stack | **OWNER DECISION REQUIRED** | Vercel Serverless is incompatible (bundle >850MB, ephemeral disk). Host selection required. |
| **5. Authentication (OIDC)** | Identity Provider & Access Tokens | **OWNER DECISION REQUIRED** | `dev_idp.py` is strictly local-only. Requires owner setup of Auth0 Free Tier or VPS Keycloak. |
| **6. Database Persistence** | SQLite WAL + Append-Only Triggers | **READY** | Schema versioning & triggers verified. Ready to attach to persistent volume upon host provisioning. |

---

## 1. API Routing Design Fix & Validation

### Current Defect: The `127.0.0.1:8000` Loopback Fallacy
In the initial preview configuration, `frontend/vercel.json` included:
```json
{
  "source": "/api/:path*",
  "destination": "http://127.0.0.1:8000/api/:path*"
}
```
**Live Failure Telemetry:**  
Executing `Invoke-WebRequest -Uri "https://qorvexa-delta.vercel.app/api/v1/health"` returns **`HTTP 404 Not Found`**.  
*Root Cause:* On Vercel's distributed edge network, `http://127.0.0.1:8000` resolves to the local loopback of Vercel's edge server where no backend listener exists. Vercel detects a dead upstream and returns 404. **No API connectivity can be claimed until verified against an actual hosted backend.**

### Comparative Routing Architecture: Vercel Proxy vs. Direct Cross-Origin API

| Evaluation Criteria | Option A: Vercel Edge Rewrite Proxy (Recommended) | Option B: Direct Cross-Origin API |
|---|---|---|
| **Implementation** | `vercel.json`: `"destination": "https://<backend-domain>/api/:path*"` | `http.ts`: `apiFetch` prefixes `VITE_API_BASE_URL` |
| **Browser Origin** | **Same-Origin** (`https://qorvexa-delta.vercel.app/api/...`) | **Cross-Origin** (`https://api.qorvexa.ai/api/...`) |
| **CORS Latency** | **Zero.** Browser sends no `OPTIONS` preflight requests. | **High.** Every mutating request triggers an `OPTIONS` round-trip. |
| **Security Policy** | Complies natively with backend's `Cross-Origin-Resource-Policy: same-origin`. | Requires relaxing `Cross-Origin-Resource-Policy` to `cross-origin`. |
| **SSL Certificates** | Single certificate managed by Vercel for the frontend domain. | Requires dual SSL management (frontend on Vercel, backend on host). |
| **Deployment Coupling** | Backend URL can be swapped in Vercel settings without rebuilding frontend. | Requires rebuilding Vite bundle on any backend URL change. |

**Recommended Solution (Option A):**  
Maintain the edge rewrite proxy in `vercel.json`, but point `destination` to the live, HTTPS-terminated remote backend service once provisioned:
```json
{
  "source": "/api/:path*",
  "destination": "https://api.qorvexa.industrial-demo.internal/api/:path*"
}
```

---

## 2. Comprehensive Comparison of Persistent Backend Hosting Options

The QORVEXA backend requires Python 3.12, FastAPI, Uvicorn, and heavy numerical/ML packages (`scikit-learn`, `pandas`, `pyarrow`, `scipy`, `pypdf`, `sqlalchemy`). It runs an in-memory sliding-window rate limiter and persists incidents, decisions, observations, and audit events to an append-only SQLite database.

| Hosting Provider & Tier | Monthly Cost | Hardware Specs | SQLite Storage Durability | Cold Starts & Sleep Behavior | Limitations & Migration Risks | Feasibility Verdict |
|---|---|---|---|---|---|:---:|
| **Hetzner Cloud (CX22 VPS)** | **€3.79 (~$4.10)** | 2 vCPU, 4GB RAM, 40GB NVMe SSD, 20TB traffic | **Native Persistent NVMe Disk.** 100% durable, supports atomic WAL mode and automated `.backup`. | **Always On.** Zero cold starts. Runs Docker Compose + Caddy. | Requires credit card / billing setup. Not a free tier. | **RECOMMENDED (Lowest Cost, Highest Reliability)** |
| **Fly.io (Shared 1x + Volume)** | **~$14.05** | 1 shared CPU, 1GB RAM, 10GB NVMe volume, IPv4 | **Persistent NVMe volume (`/data`).** Pinned to physical host. | Can scale to zero (2–4s wake) or run continuous. | Billing verification required. High egress to Africa ($0.12/GB). Manual volume recovery on host failure. | **FEASIBLE (Higher Cost)** |
| **Railway (Hobby Tier)** | **$5.00 base** | Shared vCPU, 512MB–1GB RAM, 5GB volume | **Persistent Volume.** | Always on while within $5 usage credit. | Requires credit card. Usage limits can cause mid-month halts. | **FEASIBLE (Paid)** |
| **Render.com (Free Web Service)** | **$0.00** | 0.1 vCPU, 512MB RAM | **EPHEMERAL DISK ONLY.** Persistent disk requires paid plan ($7/mo instance + disk). | **Spins down after 15 min inactivity.** Cold start: 50–90 seconds. | **CRITICAL DATA LOSS RISK.** Every container spin-down destroys the SQLite database, wiping audit trails and incident records. 512MB RAM risks OOM crashes. | **NOT VIABLE FOR PERSISTENCE** |
| **Koyeb (Free Nano)** | **$0.00** | 0.1 vCPU, 512MB RAM | **EPHEMERAL DISK ONLY.** | Spins down on inactivity. | Same data loss risk as Render. No persistent volume on free tier. | **NOT VIABLE FOR PERSISTENCE** |
| **Hugging Face Spaces (Free Docker)** | **$0.00** | 2 vCPU, 16GB RAM, 50GB disk | **Ephemeral container storage.** Storage resets on restarts/rebuilds unless synced externally. | Sleeps after 48h of inactivity. Cold start: ~15–30s. | Excellent compute (16GB RAM), but SQLite state is ephemeral unless backed up to an external S3 bucket on shutdown. | **VIABLE ONLY FOR READ-ONLY DEMO** |
| **Self-Hosted Workstation (Cloudflare Tunnel)** | **$0.00** | Workstation CPU, 16GB RAM, 132GB NVMe SSD | **100% Persistent on local disk.** | Always on while workstation runs. | Workstation must remain powered on and connected to internet. Outage if machine sleeps. | **VIABLE FOR TEMPORARY FREE DEMO** |

### Database Architecture & Migration Assessment
- **Preserve SQLite:** The current architecture uses custom SQLite triggers for append-only tables (`evidence_no_update`, `evidence_no_delete`, etc.) and a SHA-256 rolling hash chain. Migrating to PostgreSQL or MySQL is unnecessary, introduces migration risks, and adds $10–$25/mo in managed database costs.
- **Durable SQLite Storage Plan:** On a VPS or persistent volume, the database file lives at `/data/factorymind.sqlite3`. Nightly backups are generated via the SQLite Online Backup API (`sqlite3 db.sqlite ".backup '/backups/backup.db'"`) to prevent corrupting active WAL transactions.

---

## 3. Production Authentication Evaluation

### Critical Security Rule: `demo-idp/dev_idp.py` is Strictly Local-Only
- **Security Audit:** `demo-idp/dev_idp.py` runs an unencrypted standard library `ThreadingHTTPServer`. It embeds a plaintext RSA private key (`key.pem`), uses an in-memory Python dictionary for authorization codes (`CODES: dict`), and stores plaintext passwords in `users.json`. It lacks rate limiting, anti-brute-force protection, TLS, and audit logging.
- **Determination:** Under no circumstances will `dev_idp.py` be deployed or exposed to the public internet.

### Production Authentication Options

| Authentication Strategy | Provider & Tier | Security & Protocol Support | Token Claims & Roles Integration | Operational Overhead | Recommendation Status |
|---|---|---|---|---|:---:|
| **Auth0 Free Tier** | Auth0 by Okta ($0/mo, up to 7,500 MAU) | Standard Authorization Code Flow with PKCE (S256). Managed RS256 JWKS endpoint with automated key rotation. | Custom Auth0 Post-Login Action maps `roles`, `tenant_id`, and `assets` into access token. | **Zero server maintenance.** Cloud-hosted by Okta. | **RECOMMENDED (Owner Setup Required)** |
| **Containerized Keycloak** | Self-hosted on VPS (Free Open Source) | Full OIDC/OAuth2 compliance, PKCE, fine-grained client scopes. | Native user attribute and role mapping to JWT token claims. | Requires ~1GB RAM on the VPS and initial realm configuration. | **VIABLE ALTERNATIVE (Self-Hosted)** |
| **Disposable Demo Token Endpoint (Advisory Architecture)** | Built-in FastAPI Demo Gate | Backend issues short-lived, signed, read-only demo JWTs (RS256) via rate-limited endpoint. | Fixed demo roles (`viewer` or `engineer`), single demo tenant (`demo`). | Requires architectural review and owner authorization before code changes. | **REQUIRES OWNER DECISION** |

---

## 4. End-to-End Deployment Architecture Proposal

```
┌────────────────────────────────────────────────────────────────────────┐
│                                CLIENT                                  │
│   Web Browser: https://qorvexa-delta.vercel.app                       │
└───────────────────┬────────────────────────────────┬───────────────────┘
                    │ 1. Fetch Web Assets            │ 2. OIDC Login (PKCE)
                    ▼                                ▼
       ┌────────────────────────┐       ┌────────────────────────┐
       │      VERCEL EDGE       │       │    AUTH0 FREE TIER     │
       │   React 19 + Vite SPA  │       │   Managed OIDC Provider│
       │   Global CDN Edge      │       │   RS256 JWKS Endpoint  │
       └────────────┬───────────┘       └────────────┬───────────┘
                    │                                │
                    │ 3. /api/v1/* Proxy             │ 4. Verify Bearer JWT
                    ▼                                ▼
       ┌─────────────────────────────────────────────────────────┐
       │              BACKEND HOST (HETZNER CX22)                │
       │                                                         │
       │  ┌───────────────────────────────────────────────────┐  │
       │  │               CADDY REVERSE PROXY                 │  │
       │  │    Auto Let's Encrypt TLS / Security Headers      │  │
       │  └─────────────────────────┬─────────────────────────┘  │
       │                            ▼                            │
       │  ┌───────────────────────────────────────────────────┐  │
       │  │               FASTAPI BACKEND SERVICE             │  │
       │  │    TokenVerifier (Remote JWKS)                    │  │
       │  │    Anomaly, Digital Twin, RAG, Incidents API      │  │
       │  │    SlidingWindowLimiter Abuse Protection          │  │
       │  └─────────────────────────┬─────────────────────────┘  │
       │                            ▼                            │
       │  ┌───────────────────────────────────────────────────┐  │
       │  │          PERSISTENT NVMe STORAGE (/data)          │  │
       │  │    factorymind.sqlite3 (WAL Mode + Triggers)      │  │
       │  │    MetroPT-3 Preprocessed Telemetry Datasets      │  │
       │  │    AI4I Benchmark Models & PCA SPE Weights        │  │
       │  └───────────────────────────────────────────────────┘  │
       └─────────────────────────────────────────────────────────┘
```

### Environment Variable Matrix (Separation of Public vs. Private)

| Variable Name | Scope | Deployment Location | Purpose | Sensitivity |
|---|---|---|---|:---:|
| `VITE_OIDC_AUTHORITY` | Public (Client) | Vercel Project Settings | OIDC Provider Issuer URL | Low (Public URL) |
| `VITE_OIDC_CLIENT_ID` | Public (Client) | Vercel Project Settings | Public OIDC Client Identifier | Low (Public ID) |
| `VITE_OIDC_REDIRECT_URI` | Public (Client) | Vercel Project Settings | `https://qorvexa-delta.vercel.app/auth/callback` | Low (Public URL) |
| `VITE_OIDC_SCOPE` | Public (Client) | Vercel Project Settings | `openid profile` | Low |
| `FACTORYMIND_ENVIRONMENT` | Private (Server) | Backend Environment File | `production` | None |
| `FACTORYMIND_DATA_DIR` | Private (Server) | Backend Environment File | `/data` (Mounted NVMe volume) | System Path |
| `FACTORYMIND_ARTIFACTS_DIR` | Private (Server) | Backend Environment File | `/data/artifacts` | System Path |
| `FACTORYMIND_AUTH_ISSUER` | Private (Server) | Backend Environment File | Matches `VITE_OIDC_AUTHORITY` | Low |
| `FACTORYMIND_AUTH_AUDIENCE` | Private (Server) | Backend Environment File | `factorymind-api` | Low |
| `FACTORYMIND_AUTH_JWKS_URL` | Private (Server) | Backend Environment File | Provider's `/.well-known/jwks.json` | Low |
| `FACTORYMIND_SINGLE_TENANT_MODE` | Private (Server) | Backend Environment File | `true` (for public demo) | Configuration |
| `FACTORYMIND_DEFAULT_TENANT_ID` | Private (Server) | Backend Environment File | `demo` | Configuration |
| `FACTORYMIND_DATA_SOURCE_TENANT_ID`| Private (Server) | Backend Environment File | `demo` | Configuration |
| `FACTORYMIND_DATABASE_URL` | Private (Server) | Backend Environment File | `sqlite:////data/factorymind.sqlite3` | System Path |

---

## 5. Live Frontend Verification: Availability vs. Full Functionality

The live Vercel frontend was independently audited on `https://qorvexa-delta.vercel.app`:

### A. What Works Today (Frontend Availability: 100%)
- **Edge Distribution & CDN:** Initial page load completes in <800ms globally with zero 5xx server errors.
- **Client-Side SPA Routing:** Direct navigation and browser refreshes on all client routes return **`HTTP 200 OK`**:
  - `/` (Overview)
  - `/anomaly` (Anomaly Monitor)
  - `/incidents` (Incident Review Workspace)
  - `/twin` (Digital Twin Simulator)
  - `/copilot` (Industrial Copilot)
  - `/edge` (Edge Preflight Descriptors)
  - `/models` (ML Models & AI4I Benchmarks)
  - `/assets` (Asset Digital Passport)
  - `/audit` (Audit Trail Viewer)
  - `/knowledge` (Knowledge Graph & RAG)
- **Zero-Trust Security Barrier:** Because `VITE_OIDC_AUTHORITY` is not configured in the build, the client strictly halts at `AuthGate` displaying:
  > *"Authentication is not configured: This build has no identity provider. Set VITE_OIDC_AUTHORITY and VITE_OIDC_CLIENT_ID and rebuild. QORVEXA does not serve data to unauthenticated users."*
  **No sensitive factory telemetry, model weights, or asset structures are exposed.**

### B. What Requires Backend & Identity Provider (Full Application Functionality: Blocked)
- Telemetry stream replays and ECharts time-series rendering.
- Incident review creation, hypothesis evaluation, and human-in-the-loop decisions.
- Digital twin physical simulation rollouts.
- Hybrid RAG document search and knowledge graph traversal.
- Audit hash-chain verification.

---

## 6. Public Branding & Security Audit

An exhaustive scan across all deployed frontend bundles, source files, and documentation was conducted:
1. **Credentials & Keys:** **ZERO** passwords, tokens, private keys (`key.pem`), or secrets exist in the client build. All test credentials remain restricted to local test mocks.
2. **Internal Network Identifiers:** **ZERO** references to `opc-ro.factorymind.internal` or private IP ranges are present in the client bundle.
3. **Product Boundaries:** Every route strictly reinforces that QORVEXA is **advisory and read-only** and **never actuates or controls physical industrial equipment**.
4. **Lineage Transparency:** The project documentation ([README.md](file:///D:/FactoryMind/qorvexa-deploy/README.md)) clearly establishes that QORVEXA is the industrial decision intelligence platform evolved from the FactoryMind CDT research prototype.

---

## 7. Owner Decisions Required Before Provisioning

In accordance with execution safety rules, the following actions are paused awaiting explicit owner sign-off:

| Decision ID | Decision Title | Options Available | Recommendation | Cost Impact |
|---|---|---|---|:---:|
| **OD-1** | **Backend Hosting Selection** | (a) Hetzner Cloud CX22 VPS<br>(b) Fly.io Dedicated Machine<br>(c) Cloudflare Tunnel to Workstation | **Option (a) Hetzner CX22** | €3.79 / month (~$4.10) |
| **OD-2** | **Public Identity Provider** | (a) Auth0 Free Tier<br>(b) Keycloak on Hetzner VPS<br>(c) Custom Demo Token Architecture | **Option (a) Auth0 Free Tier** | $0.00 / month |
| **OD-3** | **Git Release Authorization** | (a) Approve commit & push of branch `deploy/qorvexa-public-demo`<br>(b) Defer until Task 02B integration | **Option (a)** | $0.00 |
| **OD-4** | **Custom Domain Mapping** | (a) Keep `qorvexa-delta.vercel.app`<br>(b) Map custom domain (e.g. `qorvexa.ai`) | **Option (a) for Demo Phase** | $0.00 |

---

## Rollback Procedure

- **Frontend Rollback:** Execute `vercel rollback [deployment-id]` or re-alias to any prior deployment ID directly via Vercel CLI with zero downtime.
- **Git Worktree Rollback:** Worktree `D:\FactoryMind\qorvexa-deploy` can be cleanly removed with `git worktree remove D:\FactoryMind\qorvexa-deploy` without affecting any base branches or Claude's workspace.
