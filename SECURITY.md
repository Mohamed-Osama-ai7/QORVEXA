# Security Policy — QORVEXA

## 1. Supported Scope

Security updates and vulnerability remediation are actively maintained for:

| Branch / Release | Supported |
|---|---|
| `collaboration/qorvexa-current` | :white_check_mark: Supported |
| `main` | :white_check_mark: Supported |
| Older historical commits / feature branches | :x: Not Supported |

---

## 2. Reporting a Vulnerability

The QORVEXA engineering team takes security vulnerabilities seriously. We appreciate responsible disclosure.

### Responsible Disclosure Channel
- **Preferred Method:** Use **GitHub Private Vulnerability Reporting** directly on the repository:
  `https://github.com/Mohamed-Osama-ai7/QORVEXA/security/advisories/new`
- If private vulnerability reporting is not yet enabled on your view of the repository, contact the project owner directly through GitHub or open a confidential discussion request.
- **Do NOT** file public GitHub issues for exploitable vulnerabilities, credential leaks, or zero-day security flaws.

### What to Include in a Security Report
To expedite triage and remediation, include:
1. Subsystem and exact file paths affected.
2. Vulnerability classification (e.g., SSRF, arbitrary code execution, token bypass, SQL injection, unauthorized state mutation).
3. Detailed reproduction steps or proof-of-concept (PoC).
4. Potential operational or industrial impact.
5. Any proposed remediation or patches.

**Important:** Never include real credentials, private production tokens, or sensitive factory data in vulnerability reports.

---

## 3. Industrial Operational Safety Boundaries

QORVEXA is engineered with a strict, non-negotiable **Advisory-Only Safety Invariant**:

- **Zero Actuation / Zero Physical Write:** QORVEXA's backend, connectors, workers, and algorithms operate strictly in read-only / decision-advisory mode.
- **No Machine Control:** The platform contains zero endpoints, libraries, or protocols designed to write setpoints, flip coils, actuate valves, or control PLCs, DCS, or SCADA equipment.
- **Human Approval Barrier:** All operational recommendations, schedule adjustments, and model parameters require explicit, authenticated human engineering sign-off.
- **Prohibited Testing:** Security testing or fuzzing against live industrial equipment, factory networks, or physical systems is strictly prohibited. Security testing must be conducted exclusively in hermetic local or simulated environments.

---

## 4. Secret Management & Environment Hygiene

- **`.env.example` Only:** Only sanitized `.env.example` templates containing non-secret placeholders are permitted in version control.
- **No Hardcoded Credentials:** Real API keys, tokens, passwords, private keys (`.pem`), or JWT secrets must never be committed to source code or tests.
- **Deterministic Test Tokens:** All test suites must use synthetic mock keys, temporary ephemeral RSA/EC test pairs, or mock HMAC secrets explicitly marked as test-only.
- **Automated Scanning:** The repository is routinely audited for committed secrets, credentials, and dependency CVEs.

---

## 5. Dependency Vulnerability Management

Dependencies are strictly pinned and audited:
- **Python Backend:** Dependencies are managed via `uv` and pinned in `backend/uv.lock`. Security audits are performed against the Python Advisory Database.
- **React Frontend:** Dependencies are managed via `npm` and pinned in `frontend/package-lock.json`. Routine `npm audit` checks are executed.
