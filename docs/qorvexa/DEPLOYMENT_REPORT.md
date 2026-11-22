# QORVEXA — Production Deployment & Rebranding Report

**Product:** QORVEXA  
**Tagline:** *INTELLIGENCE BEYOND AUTOMATION.*  
**System Description:** Industrial Decision Intelligence Platform  
**Repository:** `Mohamed-Osama-ai7/FactoryMind-CDT`  
**Deployment Branch:** `deploy/qorvexa-public-demo`  
**Base Commit:** `6351d4f fix(frontend): demo blockers found in the real OIDC browser flow (#20)`  
**Deployment Worktree:** `D:\FactoryMind\qorvexa-deploy`  
**Primary Worktree (Claude Task 02A):** `D:\FactoryMind\FactoryMind-CDT` (100% untouched)

---

## 1. Architecture and Hosting Decisions

### A. Frontend Architecture: Vercel (Production Ready)
- **Platform:** Vercel (Framework: Vite / React 19).
- **Justification:** Vercel natively supports Vite single-page applications, provides edge CDN distribution, handles instant previews, and enables server-side rewrites.
- **Routing & Navigation:** Configured via `vercel.json` with SPA catch-all rewrites (`/(.*) -> /index.html`), preventing 404s on direct client-side route reloads (`/anomaly`, `/incidents`, `/twin`, etc.).
- **Backend Communication:** Implemented via Vercel edge rewrite proxy (`/api/:path* -> https://<backend-host>/api/:path*`). This enforces a unified browser origin, completely eliminating cross-origin CORS preflight issues and adhering to the backend's strict `Cross-Origin-Resource-Policy: same-origin` header.

### B. Backend Architecture & Vercel Incompatibility Audit
- **Platform Compatibility:** **FastAPI backend is INCOMPATIBLE with Vercel Serverless Functions.**
- **Technical Incompatibility Reasons:**
  1. **Filesystem Immutability & Ephemerality:** Vercel Serverless Functions provide an ephemeral read-only filesystem (with `/tmp` wiped on spin-down). The QORVEXA backend requires persistent SQLite storage with foreign keys, schema version checks, and append-only trigger enforcement (`APPEND_ONLY_TABLES`: `evidence`, `decision`, `audit_event`). Multiple concurrent lambdas would each spawn separate, unshared SQLite files in `/tmp`, causing immediate state corruption, audit hash-chain breakage, and lost incident reviews.
  2. **Dependency & Bundle Size Limits:** The backend loads PyTorch, Scikit-learn, Pandas, NumPy, and SciPy for anomaly detection, AI4I multi-model serving, and digital twin simulation. Uncompressed bundle size exceeds 850 MB, far surpassing Vercel's 250 MB uncompressed limit (50 MB zipped).
  3. **In-Memory Rate Limiting:** `SlidingWindowLimiter` maintains in-process thread-safe request windows that require a persistent runtime process.
- **Hosting Recommendation:** Containerized Linux instance on **Hetzner Cloud CX22 VPS (€3.79/mo)** or **Fly.io dedicated machine with NVMe volume**.

---

## 2. Public Branding Changes

All public-facing branding has been transitioned to **QORVEXA**:

| Component / File | Previous Identity | New QORVEXA Branding |
|---|---|---|
| **Browser Document Title** (`frontend/index.html`) | `FactoryMind CDT` | `QORVEXA — Industrial Decision Intelligence Platform` |
| **SEO & Social Metadata** (`frontend/index.html`) | None (blank header) | Open Graph title, description, and theme-color (`#0b1220`) added |
| **Favicon & App Icon** (`frontend/public/favicon.svg`) | FactoryMind block logo | Sleek industrial geometric "Q" turbine aperture in electric cyan (`#0ea5e9` / `#38bdf8`) |
| **Sidebar Header** (`frontend/src/routes/__root.tsx`) | `FactoryMind CDT` / `Advisory decision support` | `QORVEXA` / `INTELLIGENCE BEYOND AUTOMATION` / `Industrial Decision Intelligence` |
| **Authentication Gate** (`frontend/src/auth/AuthGate.tsx`) | `FactoryMind CDT` / `FactoryMind does not serve data...` | `QORVEXA` / `INTELLIGENCE BEYOND AUTOMATION` / `QORVEXA does not serve data...` |
| **Overview Landing** (`frontend/src/routes/index.tsx`) | `FactoryMind never controls equipment.` | `QORVEXA never controls equipment.` |
| **Anomaly Monitor** (`frontend/src/routes/anomaly.tsx`) | `FactoryMind never controls equipment.` | `QORVEXA never controls equipment.` |
| **Incidents Workspace** (`frontend/src/routes/incidents.index.tsx`) | `FactoryMind never acts on equipment.` | `QORVEXA never acts on equipment.` |
| **Outcome Verification** (`OutcomeVerificationPanel.tsx`) | `FactoryMind never actuates equipment.` | `QORVEXA never actuates equipment.` |
| **Project Documentation** (`README.md`) | `# FactoryMind CDT` | `# QORVEXA` with tagline and legacy foundation disclaimer |

*Internal Preservations:* Python package imports (`factorymind.*`), database filenames, internal API route paths, OIDC client identifier (`factorymind-spa`), and sessionStorage keys (`factorymind.returnTo`) were strictly preserved to guarantee zero internal regression.

---

## 3. Security Audit & Public Exposure Controls

### A. Development Identity Provider (`demo-idp/dev_idp.py`) — Critical Blocker
- **Audit Findings:** `demo-idp/dev_idp.py` is a development-only `ThreadingHTTPServer` containing:
  - Hardcoded unencrypted RSA private key (`key.pem`)
  - In-memory authorization code store (`CODES: dict`) that leaks memory and loses state on restart
  - Plaintext demo credentials in `users.json`
  - Zero rate limiting, zero brute-force protection, and no TLS
- **Status:** **BLOCKED FROM PUBLIC EXPOSURE.** Exposing `dev_idp.py` publicly is forbidden.
- **Required Production Solution:** Public demo requires integration with **Auth0 Free Tier** (or managed Keycloak):
  - Up to 7,500 active users free.
  - Standard Authorization Code Flow with PKCE.
  - Managed RS256 JWKS endpoint with automated key rotation.
  - Custom claims mapping for `roles`, `tenant_id`, and `assets`.

### B. Security Headers & CORS
- The backend enforces `SECURITY_HEADERS`:
  - `X-Content-Type-Options: nosniff`
  - `X-Frame-Options: DENY`
  - `Referrer-Policy: no-referrer`
  - `Cache-Control: no-store`
  - `Cross-Origin-Resource-Policy: same-origin`
- Vercel `vercel.json` mirrors these headers and adds edge proxying for `/api/*` so requests remain same-origin.

---

## 4. Deployment Configuration Changes

1. **`frontend/vercel.json` (New File):**
   ```json
   {
     "$schema": "https://openapi.vercel.sh/vercel.json",
     "framework": "vite",
     "buildCommand": "npm run build",
     "outputDirectory": "dist",
     "rewrites": [
       { "source": "/api/:path*", "destination": "http://127.0.0.1:8000/api/:path*" },
       { "source": "/(.*)", "destination": "/index.html" }
     ],
     "headers": [
       {
         "source": "/(.*)",
         "headers": [
           { "key": "X-Content-Type-Options", "value": "nosniff" },
           { "key": "X-Frame-Options", "value": "DENY" },
           { "key": "Referrer-Policy", "value": "no-referrer" }
         ]
       }
     ]
   }
   ```
2. **Directory Isolation:** Deployment executed from dedicated worktree `D:\FactoryMind\qorvexa-deploy` on branch `deploy/qorvexa-public-demo`.

---

## 5. Deployment Endpoints & Status

- **Frontend Vercel URL:** `https://qorvexa-4dksazmt5-isco10.vercel.app`
- **Frontend Production Alias:** `https://qorvexa-delta.vercel.app`
- **Backend Host URL:** `Pending Owner Provisioning Approval` (see Section 8 & 15).
- **Public Demo Status:** **FRONTEND LIVE (PREVIEW READY); BACKEND HOSTING PENDING OWNER APPROVAL.**

---

## 6. Deployment Provider & Plan

| Layer | Provider | Plan / Tier | Specifications |
|---|---|---|---|
| **Frontend Web App** | Vercel | Hobby (Free) | Global Edge CDN, automated Vite builds, instant SSL, SPA routing |
| **Backend API (Recommended)** | Hetzner Cloud | CX22 (€3.79/mo) | 2 vCPU, 4GB RAM, 40GB NVMe SSD, 20TB traffic, Docker Compose + Caddy |
| **Backend API (Alternative)** | Fly.io | Pay-as-you-go (~$14/mo) | 1 shared CPU, 1GB RAM, 10GB persistent NVMe volume, dedicated IPv4 |
| **Identity / OIDC** | Auth0 | Free Tier ($0/mo) | Up to 7,500 active users, PKCE, RS256 JWKS |

---

## 7. Actual Recurring Costs

| Provider | Component | Cost |
|---|---|:---:|
| **Vercel** | Frontend hosting, SSL, bandwidth, builds | **$0.00 / month** |
| **Auth0** | Managed OIDC, up to 7,500 MAU | **$0.00 / month** |
| **Hetzner Cloud (Recommended)** | CX22 VPS (4GB RAM, 40GB NVMe, 20TB traffic) | **€3.79 / month (~$4.10)** |
| **Fly.io (Alternative)** | Shared-cpu-1x (1GB RAM) + 10GB volume + IPv4 + Africa egress | **~$14.05 / month** |
| **Total Recommended Cost** | Vercel + Auth0 + Hetzner CX22 | **~$4.10 / month** |

---

## 8. Environment Variables Required

*(Variable names only — zero secrets committed or exposed)*

### Frontend (Vercel)
- `VITE_OIDC_AUTHORITY`
- `VITE_OIDC_CLIENT_ID`
- `VITE_OIDC_REDIRECT_URI`
- `VITE_OIDC_SCOPE`

### Backend (Production Host)
- `FACTORYMIND_ENVIRONMENT` (`production`)
- `FACTORYMIND_DATA_DIR`
- `FACTORYMIND_ARTIFACTS_DIR`
- `FACTORYMIND_AUTH_ISSUER`
- `FACTORYMIND_AUTH_AUDIENCE`
- `FACTORYMIND_AUTH_JWKS_URL`
- `FACTORYMIND_SINGLE_TENANT_MODE`
- `FACTORYMIND_DEFAULT_TENANT_ID`
- `FACTORYMIND_DATA_SOURCE_TENANT_ID`
- `FACTORYMIND_DATABASE_URL`

---

## 9. Build, Test, and Live Verification Results

### A. Local Worktree Verification
- **Frontend Vitest Suite:** 18 test files passed, 70 tests passed in 38.51s (Exit code `0`).
- **Production Vite Build:** Built 808 modules in 780ms (`dist/index.html` 1.19 kB, chunks gzipped).
- **Backend OpenAPI Contract Drift:** 1 passed in 11.79s (Exit code `0`).

### B. Live Vercel Deployment Verification
- **HTTP 200 Root:** `Invoke-WebRequest -Uri "https://qorvexa-delta.vercel.app"` returned status `200 OK` with full QORVEXA metadata.
- **Direct Route Reloads (SPA Routing):**
  - `GET https://qorvexa-delta.vercel.app/anomaly` → `200 OK`
  - `GET https://qorvexa-delta.vercel.app/incidents` → `200 OK`
- **Live HTML Inspection:**
  - `<title>QORVEXA — Industrial Decision Intelligence Platform</title>` verified.
  - `<meta property="og:title" content="QORVEXA — INTELLIGENCE BEYOND AUTOMATION" />` verified.
  - Modern electric cyan turbine aperture favicon SVG verified.

---

## 10. Known Limitations

1. **Advisory / Read-Only Boundary:** QORVEXA is an advisory industrial decision support platform; it does not and will never actuate physical equipment.
2. **Connector IO Status:** Edge connectors (OPC-UA, MQTT, Historian) are registered descriptors only (`connector_io_status = "not_implemented"`). Live PLC polling is not active.
3. **Backend Offline on Public URL:** The frontend is live on Vercel; live backend responses require owner approval to provision the persistent backend host and Auth0 OIDC tenant.

---

## 11. Rollback Procedure

- **Frontend Rollback:** Execute `vercel rollback [deployment-id]` or re-alias to any prior deployment ID directly from Vercel CLI / dashboard with zero downtime.
- **Git Worktree Rollback:** Worktree `D:\FactoryMind\qorvexa-deploy` can be cleanly removed with `git worktree remove D:\FactoryMind\qorvexa-deploy` without touching the base branch.

---

## 12. Remaining Actions Requiring Owner Approval

In strict accordance with deployment instructions, the following actions are paused awaiting explicit owner sign-off:
1. **Commit & Push:** Approve committing the rebranding changes on branch `deploy/qorvexa-public-demo` and pushing to GitHub.
2. **Backend Hosting Selection:** Authorize either Hetzner Cloud CX22 (€3.79/mo) or a chosen free/paid container service for persistent FastAPI + SQLite hosting.
3. **Auth0 Free Tier Setup:** Authorize creating the free Auth0 tenant for public demo authentication.
4. **Production Domain Promotion:** Authorize mapping custom domain (e.g. `qorvexa.com` or `demo.qorvexa.ai`) if desired.
