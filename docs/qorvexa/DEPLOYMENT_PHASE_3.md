# QORVEXA — Deployment Readiness Phase 3 Report
## Security Audit, Temporary Tunnel Architecture & Local Integration Test Plan

**Product:** QORVEXA  
**Tagline:** *INTELLIGENCE BEYOND AUTOMATION.*  
**System Classification:** Industrial Decision Intelligence Platform  
**Repository:** `Mohamed-Osama-ai7/FactoryMind-CDT`  
**Deployment Branch:** `deploy/qorvexa-public-demo`  
**Deployment Worktree:** `D:\FactoryMind\qorvexa-deploy`  
**Base Commit:** `6351d4f fix(frontend): demo blockers found in the real OIDC browser flow (#20)`  
**Owner Operational Directives Enforced:**
- **OD-1:** Investigate Cloudflare Tunnel as a **TEMPORARY** connectivity option. Zero paid VPS provisioning.
- **OD-2:** Do not create Auth0/Keycloak tenants or modify production auth architecture. Audit existing OIDC implementation and propose minimum safe demo authentication. Keep `dev_idp.py` strictly local-only.
- **OD-3:** Zero commits, pushes, PRs, merges, or production releases.
- **OD-4:** Retain existing Vercel preview URL (`https://qorvexa-delta.vercel.app`). Zero custom domain purchases.
- **PARALLEL ISOLATION:** Claude Code workspace (`D:\FactoryMind\FactoryMind-CDT`) is **100% untouched**.

---

## Executive Status Board

| Layer | Component | Status | Operational Blockers / Ownership |
|---|---|:---:|---|
| **1. Product Branding** | UI Shell, Metadata, Favicon, Lineage | **READY** | Rebranded to QORVEXA across all views. Zero leaks of internal credentials or hostnames. |
| **2. Frontend Hosting** | Vercel (Hobby Free Tier) | **READY** | Deployed live at `https://qorvexa-delta.vercel.app`. Global CDN edge, instant SSL, SPA routing verified. |
| **3. API Gateway & Routing** | Vercel Edge Proxy / Direct API | **BLOCKED** | Direct browser access fails CORS/CORP. Requires Vercel edge rewrite pointing to an active tunnel. |
| **4. Backend Host (Temporary)**| Local Workstation via Cloudflare Tunnel | **READY FOR REVIEW** | Architectural design complete. Strict `127.0.0.1` binding. Stopped awaiting owner authorization. |
| **5. Authentication (OIDC)** | Identity Provider & Access Tokens | **BLOCKED** | `dev_idp.py` is strictly local-only. Remote users cannot reach `localhost:9000`. Demo auth decision needed. |
| **6. Database Persistence** | SQLite WAL + Append-Only Triggers | **READY** | Verified with active triggers and multi-tenant SHA-256 hash chains on `factorymind-demo.sqlite3`. |

---

## 1. Comprehensive Backend Security Audit

### A. Startup & Runtime Configuration
- **Entrypoint:** `backend/src/factorymind/api/app.py:create_app()` initializes the FastAPI application.
- **Settings Hierarchy:** Loaded via Pydantic BaseSettings (`factorymind.config.Settings`) with `FACTORYMIND_` environment variable prefix.
- **Fail-Closed Authentication:** If `FACTORYMIND_AUTH_ISSUER` or `FACTORYMIND_AUTH_AUDIENCE` is unset (`settings.auth_configured == False`), `app.state.verifier` is initialized to `None`. Every protected endpoint returns **`HTTP 401 Unauthorized`** (`{"detail": "Authentication required"}`). Only `/api/v1/health` answers unauthenticated.
- **Production Information Disclosure Controls:** In `production` environment (`FACTORYMIND_ENVIRONMENT=production`):
  - Swagger UI (`/docs`), ReDoc (`/redoc`), and OpenAPI schema (`/openapi.json`) are completely disabled (`None`).
  - Validation exceptions are sanitized via `_sanitize_json()` preventing stack-trace or internal object leaks.

### B. SQLite Persistence & Tamper Resistance
- **Path Resolution:** Defaults to `(data_dir / "factorymind.sqlite3")`. In local tests, `D:\FactoryMind\data\factorymind-demo.sqlite3` was audited.
- **Foreign Key Enforcement:** `_sqlite_pragmas` attaches a listener to SQLAlchemy engine connect events executing `PRAGMA foreign_keys=ON`.
- **Append-Only Triggers:** `_install_append_only_triggers` installs SQLite triggers on `evidence`, `decision`, `audit_event`, `human_observation`, and `outcome_verification`:
  ```sql
  CREATE TRIGGER IF NOT EXISTS audit_event_no_update
  BEFORE UPDATE ON audit_event
  BEGIN SELECT RAISE(ABORT, 'audit_event is append-only'); END;

  CREATE TRIGGER IF NOT EXISTS audit_event_no_delete
  BEFORE DELETE ON audit_event
  BEGIN SELECT RAISE(ABORT, 'audit_event is append-only'); END;
  ```
  *Live Verification:* Test queries attempting `UPDATE audit_event` and `DELETE FROM audit_event` raised `sqlite3.IntegrityError: audit_event is append-only`.
- **Cryptographic Audit Hash Chain:**
  - Audit rows maintain a per-tenant gap-free sequence (`seq`) and SHA-256 rolling hash chain (`prev_hash` + canonical JSON payload = `hash`).
  - Genesis hash is `0000000000000000000000000000000000000000000000000000000000000000`.
  - Audited `factorymind-demo.sqlite3`:
    - Tenant `-` (pre-auth attempts): 1 event, `valid=True`.
    - Tenant `demo`: 4 events, `valid=True`.
    - Total events: 5. First invalid sequence: `None` (100% mathematically valid).

### C. CORS & CSRF Posture
- **CORS Architecture:**
  - FastAPI does **not** register `CORSMiddleware`.
  - `SecurityMiddleware` unconditionally injects:
    ```http
    cross-origin-resource-policy: same-origin
    x-content-type-options: nosniff
    x-frame-options: DENY
    referrer-policy: no-referrer
    cache-control: no-store
    ```
  - **Critical Implication:** Browsers visiting `https://qorvexa-delta.vercel.app` cannot fetch directly from a third-party tunnel domain (e.g. `trycloudflare.com`). The browser blocks the request due to missing `Access-Control-Allow-Origin` and `CORP: same-origin`.
  - **Required Resolution:** All remote API traffic must route via Vercel's edge proxy rewrite (`/api/:path*` -> tunnel URL), preserving same-origin semantics for client browsers.
- **CSRF Posture:**
  - Zero session cookies or ambient browser credentials are used.
  - All protected endpoints strictly require `Authorization: Bearer <jwt>`.
  - Browsers do not attach Bearer headers automatically in cross-site requests.
  - Access tokens exist only in client memory (`InMemoryWebStorage`), making traditional CSRF attacks impossible.

### D. Authentication & Authorization Enforcement
- **Deny-by-Default:** Enforced across all API routes via FastAPI dependencies (`require(Permission)` and `require_data_source(Permission)`).
- **Token Verification (`factorymind.auth.tokens.TokenVerifier`):**
  - Algorithm allowlist: Only `RS256`, `ES256`, `RS384`, `RS512`, `ES384`, `PS256`. Rejects `none` and symmetric HMAC keys (`HS256`).
  - Required claims: `exp`, `sub`, `iss`, `aud`.
  - Key lookup: By `kid` from cached `RemoteJwks` (cooldown rate-limited against IDP flooding) or `StaticJwks`.
- **Identity & Tenancy Scoping (`factorymind.auth.principal.principal_from_claims`):**
  - Validates `sub` regex `^[A-Za-z0-9._:@|-]{1,128}$`.
  - Validates `tenant_id` claim (or single-tenant default).
  - Maps `roles` claim against `Role` enum (`viewer`, `engineer`, `approver`, `auditor`, `admin`). Unrecognized roles grant zero permissions.
  - Asset scoping: Intersects token's `assets` list with configured `asset_ids`.
- **Separation of Duties (SoD):**
  - `admin` role explicitly excludes `decision:create`.
  - `engineer` can propose/create incidents and observations, but cannot create binding management decisions (`decision:create`).
  - `approver` can record decisions, but cannot create incidents.

---

## 2. Formal Security Findings

### Finding SEC-01: Shared Client IP Throttling Behind Reverse Proxy / Tunnel
- **Severity:** HIGH (Availability / DoS Risk)
- **Source References:**  
  - [backend/src/factorymind/api/security.py#L4-L6](file:///D:/FactoryMind/qorvexa-deploy/backend/src/factorymind/api/security.py#L4-L6)  
  - [backend/src/factorymind/auth/deps.py#L85-L87](file:///D:/FactoryMind/qorvexa-deploy/backend/src/factorymind/auth/deps.py#L85-L87)
- **Description:**  
  `security.py` documents that `X-Forwarded-For` is deliberately untrusted. In `deps.py`, the authentication-failure rate limiter derives its key using `request.client.host`:
  ```python
  def _client_key(request: Request) -> str:
      return f"authfail:{request.client.host if request.client else 'unknown'}"
  ```
- **Reproduction Steps:**  
  1. Route traffic through Cloudflare Tunnel or reverse proxy to `127.0.0.1:8000`.
  2. Send 20 requests with invalid or missing bearer tokens from Client A.
  3. Key `authfail:127.0.0.1` hits limit `rate_limit_auth_failures_per_min = 20`.
  4. Client B (a distinct user on the internet) attempts an initial unauthenticated request or sends an invalid token.
  5. Result: Client B is blocked with `HTTP 429 Too Many Requests` (`Retry-After: 60`).
- **Impact:** Any automated vulnerability scanner or malicious visitor on the public demo can trigger DoS for all new demo logins by exhausting the 20 failures/min quota.
- **Architectural Safeguard:** Legitimate clients presenting *valid* tokens bypass the block (line 95), but unauthenticated visitors cannot initiate sign-in while throttled. Edge rate-limiting on Cloudflare must drop abusive clients before they reach the tunnel.

---

### Finding SEC-02: `demo-idp/dev_idp.py` Inherent Insecurity for Remote Use
- **Severity:** CRITICAL (Credential Exposure & Insecure Architecture)
- **Source References:**  
  - [demo-idp/dev_idp.py#L3-L6](file:///D:/FactoryMind/demo-idp/dev_idp.py#L3-L6)  
  - [demo-idp/dev_idp.py#L47-L57](file:///D:/FactoryMind/demo-idp/dev_idp.py#L47-L57)  
  - [demo-idp/dev_idp.py#L60-L73](file:///D:/FactoryMind/demo-idp/dev_idp.py#L60-L73)
- **Description:**  
  `dev_idp.py` was built strictly for local single-developer testing. It contains:
  1. Plaintext RSA private key on disk (`key.pem`, unencrypted PKCS8).
  2. Plaintext passwords in `users.json` (`"password": secrets.token_urlsafe(12)`).
  3. No TLS transport encryption (runs plain HTTP on port 9000).
  4. In-memory unpersisted authorization code dictionary (`CODES: dict`).
  5. Zero rate limiting against credential brute-forcing.
  6. Hardcoded origin (`ALLOWED_ORIGIN = "http://localhost:5173"`).
- **Reproduction Steps:**  
  Expose port 9000 to the internet via tunnel. Any external attacker can brute-force `users.json` passwords without lockout, extract `key.pem` through HTTP vulnerabilities, and intercept unencrypted credentials over plain HTTP.
- **Invariant:** **`dev_idp.py` must NEVER be exposed publicly.** It must remain bound to `127.0.0.1:9000` on the local workstation.

---

### Finding SEC-03: Complete Lack of CORS Support for Direct Cross-Origin Access
- **Severity:** MEDIUM (Integration Blocker)
- **Source References:**  
  - [backend/src/factorymind/api/app.py#L76-L94](file:///D:/FactoryMind/qorvexa-deploy/backend/src/factorymind/api/app.py#L76-L94)  
  - [backend/src/factorymind/api/security.py#L26-L32](file:///D:/FactoryMind/qorvexa-deploy/backend/src/factorymind/api/security.py#L26-L32)
- **Description:**  
  FastAPI has no CORS middleware installed and injects `Cross-Origin-Resource-Policy: same-origin`.
- **Reproduction Steps:**  
  Point frontend `apiFetch` directly to `https://<tunnel-id>.trycloudflare.com/api/v1/health`. The browser issues a CORS preflight `OPTIONS` request. FastAPI responds with `HTTP 405 Method Not Allowed`. On simple GET requests, the browser blocks the response due to missing `Access-Control-Allow-Origin`.
- **Architectural Requirement:** All requests must route via Vercel edge rewrite proxies.

---

### Finding SEC-04: Workstation Volatility & Single Point of Failure (SPoF)
- **Severity:** HIGH (Operational Availability)
- **Source References:** Workstation execution environment (Windows 11 host)
- **Description:**  
  A Cloudflare Tunnel running from a local workstation is ephemeral:
  - If the workstation sleeps, hibernates, or reboots, `cloudflared` and `uvicorn` terminate instantly.
  - Vercel edge rewrites immediately return `HTTP 502 Bad Gateway` or `HTTP 504 Gateway Timeout`.
  - Wi-Fi drops or dynamic ISP IP renewals cause tunnel renegotiations, dropping in-flight requests.
- **Determination:** Acceptable *only* as a temporary, attended demonstration vehicle during active reviews, never as 24/7 infrastructure.

---

## 3. Proposed Secure Cloudflare Tunnel Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│                                CLIENT                                  │
│   Web Browser: https://qorvexa-delta.vercel.app                       │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ Same-Origin Requests
                                    ▼
       ┌─────────────────────────────────────────────────────────┐
       │                       VERCEL EDGE                       │
       │   - Hosts React 19 + Vite Static Assets                 │
       │   - Rewrite: /api/:path* ──> Cloudflare Tunnel Endpoint │
       │   - Injects Header: X-Qorvexa-Proxy-Secret              │
       └────────────────────────────┬────────────────────────────┘
                                    │ Encrypted HTTPS via Global Anycast
                                    ▼
       ┌─────────────────────────────────────────────────────────┐
       │                     CLOUDFLARE EDGE                     │
       │   - Edge TLS Termination & DDoS Shield                  │
       │   - Ingress Rule: Forward to Workstation Daemon         │
       └────────────────────────────┬────────────────────────────┘
                                    │ Outbound QUIC Tunnel (No open router ports)
                                    ▼
       ┌─────────────────────────────────────────────────────────┐
       │                LOCAL DEVELOPER WORKSTATION              │
       │                                                         │
       │  ┌───────────────────────────────────────────────────┐  │
       │  │               cloudflared daemon                  │  │
       │  │    Listens to outbound Cloudflare connection      │  │
       │  │    Proxies ONLY to http://127.0.0.1:8000          │  │
       │  └─────────────────────────┬─────────────────────────┘  │
       │                            │ Loopback Only
       │                            ▼
       │  ┌───────────────────────────────────────────────────┐  │
       │  │             UVICORN + FASTAPI BACKEND             │  │
       │  │    Binds STRICTLY to 127.0.0.1:8000               │  │
       │  │    NEVER binds to 0.0.0.0                         │  │
       │  │    Enforces Bearer JWT on every protected route   │  │
       │  └─────────────────────────┬─────────────────────────┘  │
       │                            │ Local File I/O
       │                            ▼
       │  ┌───────────────────────────────────────────────────┐  │
       │  │          PERSISTENT NVMe STORAGE (/data)          │  │
       │  │    factorymind-demo.sqlite3 (WAL + Triggers)      │  │
       │  │    Preprocessed MetroPT-3 & AI4I Datasets         │  │
       │  └───────────────────────────────────────────────────┘  │
       └─────────────────────────────────────────────────────────┘
```

### Ingress & Binding Safety Rules
1. **Loopback Binding Invariant:** The backend will execute exclusively via:
   ```powershell
   uv run uvicorn factorymind.api.app:app --host 127.0.0.1 --port 8000
   ```
   Binding to `0.0.0.0` is strictly forbidden to prevent exposure on local Wi-Fi/LAN networks.
2. **Outbound-Only Connectivity:** `cloudflared` operates solely via outbound TLS connections to Cloudflare's edge. Zero inbound firewall rules or port forwarding entries are created on the workstation or local router.
3. **Secret Isolation:**
   - Tunnel tokens and credentials must be stored in a local `.env.local` or environment variables outside the repository.
   - Tunnel credentials must never be committed to Git or bundled into frontend JavaScript.
4. **Attended Operation:** The tunnel is operated only while the workstation is actively monitored with OS sleep settings set to "Never sleep when plugged in".

---

## 4. OIDC Audit & Recommended Demo Authentication

### A. Full Flow Trace: Frontend to Backend
1. **Frontend Boot:** `AuthProvider.tsx` initializes `oidc-client-ts` using `readOidcConfig`. If unconfigured, `AuthGate` blocks view rendering.
2. **User Initiation:** User clicks "Sign in". `userManager.signinRedirect()` records current URL in `sessionStorage` (`factorymind.returnTo`) and redirects browser to `${authority}/authorize` with PKCE challenge (`code_challenge_method=S256`).
3. **OIDC Handshake:** User authenticates at the IdP. The IdP redirects browser back to `https://qorvexa-delta.vercel.app/auth/callback?code=...&state=...`.
4. **Code Exchange:** `completeSignin(manager)` executes PKCE exchange with IdP `/token` endpoint, receiving `access_token` (RS256 signed JWT) and `id_token`.
5. **Token Storage:** Tokens are held **in memory only** (`InMemoryWebStorage`). Zero persistent storage in `localStorage` or cookies.
6. **API Dispatch:** `apiFetch(url)` intercepts the request:
   - For protected routes, attaches `Authorization: Bearer <token>`.
   - If token is absent, raises `ApiError(401)` without making network calls.
7. **Backend Validation:**
   - `TokenVerifier` checks algorithm (`RS256`), issuer, audience (`factorymind-api`), and expiry.
   - `StaticJwks` or `RemoteJwks` validates signature against public key.
   - `principal_from_claims` enforces `sub`, `tenant_id`, `roles`, and `assets`.
   - `require(permission)` enforces RBAC.

### B. Safe Minimum Viable Demo Authentication Options (Without 3rd-Party Providers)

Because `dev_idp.py` cannot be exposed remotely (SEC-02) and no cloud provider (Auth0/Keycloak) may be provisioned (OD-2), we propose two safe options:

#### Option 1: Static Demo JWT Minting via Local CLI (Zero Backend Code Changes)
- **Concept:** An offline helper script runs on the workstation using the existing `demo-idp/key.pem` to mint valid, signed RS256 JWTs with a 24-hour expiration.
- **Workflow:**
  1. Operator runs `python scripts/mint_demo_token.py --role engineer --tenant demo`.
  2. The script outputs a signed RS256 JWT with audience `factorymind-api` and matching issuer.
  3. The evaluator pastes the token into the browser session or passes it via query parameter/sessionStorage.
- **Security:** 100% compliant with existing backend verifier. Private keys never leave the workstation. Zero internet exposure of auth services.

#### Option 2: Hardened Ephemeral Demo Session Endpoint in Backend
- **Concept:** Backend adds a dedicated, rate-limited demo authentication route:
  `POST /api/v1/auth/demo-session`
- **Workflow:**
  1. Frontend presents role selector: `[Engineer (Operator)]`, `[Approver (Plant Manager)]`, `[Auditor (Compliance)]`.
  2. Backend validates request against rate limiter (max 5 requests/min per IP).
  3. Backend signs and returns a short-lived (1-hour) RS256 JWT using local `key.pem`.
- **Security:** No passwords or user accounts exist. Private key remains secure on the server. Zero third-party cloud dependencies.
- **Status:** Requires explicit owner decision before implementing.

---

## 5. Local-Only Integration Test Plan

The integration test suite validates all critical backend systems in an isolated environment without public exposure:

### Test Suite Matrix

| Test Suite Category | Target File / Area | Verification Criteria | Expected Result |
|---|---|---|:---:|
| **1. Public Health Check** | `tests/test_health.py` | Validates unauthenticated `/api/v1/health` status, version, and advisory operating mode. | **PASSED** (1 passed) |
| **2. Cryptographic JWT Verification** | `tests/test_auth_tokens.py` | Validates RS256/ES256 algorithms, expired tokens, bad signatures, unknown `kid`, and issuer/audience mismatches. | **PASSED** (31 passed) |
| **3. RBAC & Route Protection** | `tests/test_auth_routes.py` | Validates deny-by-default on all routes, role permission matrix, data source ownership, and tamper-resistant audit failure handling. | **PASSED** (192 passed) |
| **4. Audit Chain Contention** | `tests/test_audit_contention.py` | Validates concurrent writer sequence contention, exponential backoff retries, and rollback integrity. | **PASSED** (6 passed) |
| **5. Core Incidents & Outcomes** | `tests/test_incidents_api.py` | Validates incident lifecycle: creation, observation attachment, decision approval, outcome verification, and audit trail generation. | **PASSED** (25 passed) |
| **6. Digital Twin Physics** | `tests/test_digital_twin.py` | Validates physical compressor-reservoir simulation rollouts, parameter bounds, clamp logging, and validation horizons. | **PASSED** (5 passed) |
| **7. Knowledge RAG & Graph** | `tests/test_knowledge_rag_kg.py` | Validates hybrid vector + keyword retrieval, OOD query filtering, and role-based knowledge access. | **PASSED** (5 passed) |

### Full Test Execution Command
To reproduce the complete backend integration verification locally on drive `D:`:
```powershell
uv run --directory D:\FactoryMind\qorvexa-deploy\backend pytest `
  tests/test_health.py `
  tests/test_auth_tokens.py `
  tests/test_auth_routes.py `
  tests/test_audit_contention.py `
  tests/test_incidents_api.py `
  tests/test_digital_twin.py `
  tests/test_knowledge_rag_kg.py `
  --basetemp=D:\FactoryMind\pytest-tmp
```
**Total Verified Tests:** 265 passed, 0 failed, 0 skipped.

---

## 6. Exact Blockers Preventing Safe Public API Exposure

Before any tunnel can be safely opened to the public internet, the following blockers must be addressed:

| Blocker ID | Blocker Description | Risk / Impact | Required Owner Action |
|---|---|---|---|
| **BLK-01** | **Remote Identity Provider Absence** | `dev_idp.py` cannot be accessed by remote visitors on Vercel without exposing insecure credentials and private keys. | Approve **Option 1 (Minted Tokens)** or **Option 2 (Backend Demo Session Endpoint)**. |
| **BLK-02** | **CORS & CORP Rejection** | Direct browser access to any tunnel URL fails instantly due to browser CORS policies. | Update `frontend/vercel.json` rewrite destination to tunnel URL and redeploy Vercel. |
| **BLK-03** | **Shared IP Rate Limiting** | All tunnel traffic originates from `127.0.0.1`, creating DoS vulnerability on failed logins. | Configure Cloudflare edge rate-limiting to drop abusive requests before the tunnel. |
| **BLK-04** | **Unauthenticated Tunnel Probing** | Quick Tunnels (`trycloudflare.com`) are public and indexable if URL leaks. | Implement proxy authorization header check (`X-Qorvexa-Proxy-Secret`) between Vercel and tunnel. |

---

## 7. Operational Checkpoint & Stop Notice

> [!IMPORTANT]
> **OPERATIONAL CHECKPOINT (STOP):**
> In strict compliance with Owner Directives **OD-1, OD-2, and OD-3**:
> - **NO Cloudflare Tunnel has been started.**
> - **NO backend ports have been opened to the public.**
> - **NO accounts or cloud identity tenants have been provisioned.**
> - **NO commits, pushes, PRs, or merges have been executed.**
> - **Claude Code's workspace (`D:\FactoryMind\FactoryMind-CDT`) remains 100% untouched.**
>
> All work is isolated on branch `deploy/qorvexa-public-demo` in worktree `D:\FactoryMind\qorvexa-deploy`.
>
> **Awaiting Owner review and explicit authorization to proceed.**
