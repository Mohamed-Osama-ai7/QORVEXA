# QORVEXA — Licensing Transition & Historical IP Disclosure

**Document Status:** Official Licensing Disclosure & Legal Context  
**Effective Date:** November 23, 2026  
**Copyright Holder:** Mohamed Osama / QORVEXA  
**Current License:** Proprietary / Closed-Source / All Rights Reserved  

---

## 1. Executive Summary

QORVEXA is an advanced commercial Industrial Decision Intelligence Platform developed for mission-critical manufacturing, energy, and process optimization. Effective November 23, 2026, the project has formally transitioned its licensing model to:

**Proprietary / Closed-Source / All Rights Reserved.**

This document provides a transparent, factual legal-history record of earlier open-source licensing indicators, analyzes the scope of prior grants, establishes clear boundaries between historical commits and prospective development, and sets the Intellectual Property (IP) policy for all team contributors.

---

## 2. Historical Licensing Chronology

A forensic audit of the Git history of `https://github.com/Mohamed-Osama-ai7/QORVEXA` establishes the following facts:

1. **Commit `77cbeb012bafa37d768f35b5649fe9ff82b6881a` (Wed Nov 11 19:20:15 2026):**
   - The root file `LICENSE` was first created containing the standard MIT License text with the copyright attribution notice: `Copyright (c) 2026 FactoryMind CDT Contributors`.
   - Concurrently, `backend/pyproject.toml` declared `license = "MIT"`.
2. **Commit Window `77cbeb0` through `2335e58` (Nov 11, 2026 – Nov 23, 2026):**
   - Work conducted across this window inherited the root `LICENSE` file.
   - During this period, the project was renamed to **QORVEXA**, and the public `README.md` badge was updated to reflect `License: Proprietary`. However, the root `LICENSE` file and `backend/pyproject.toml` were left indicating MIT, creating an internal discrepancy.
   - No formal Git release tags (e.g., `v1.0.0`) or packaged binary distributions were published under MIT.
3. **Commit `chore(repo): finalize contributor readiness and governance` (Nov 23, 2026):**
   - Complete synchronization of root `LICENSE`, `backend/pyproject.toml`, `frontend/package.json`, and all project documentation to **Proprietary / All Rights Reserved**.

---

## 3. Legal Analysis: Non-Retroactivity of Open-Source Licenses

Under standard international copyright jurisprudence and established open-source legal doctrine:

- **Irrevocability of Historical Grants:** An open-source license grant (such as the MIT License) granted to individuals who obtained copies of the software during the period it was published under that license cannot be retroactively cancelled or revoked for those specific historical commits.
- **Accurate Legal Positioning:** QORVEXA does **not** assert or pretend that earlier MIT grants for commits `77cbeb0` through `2335e58` have been voided. Any recipient who legitimately acquired code from those specific commits retains the rights granted under the MIT license for that specific snapshot.
- **Prospective Proprietary Protection:** All commits, additions, modifications, algorithms, solver integrations, and architecture created from the current baseline onward are strictly proprietary. New feature branches, composite builds, and release artifacts are licensed exclusively under proprietary terms.

---

## 4. Contributor Authorship in the Historical Window

During the historical period (commits `77cbeb0` through `2335e58`), the Git commit log records contributions from:

- **Mohamed Osama / Mohamed-Osama-ai7** (Project Architect & Primary Author)
- **Ahmed Essam** (Workstream 3 Contributor — Knowledge & Retrieval)

No anonymous or untracked external contributions exist in the Git history. Both contributors are active team members aligned with QORVEXA's commercial trajectory.

---

## 5. Contributor Intellectual Property & Assignment Policy

To maintain the commercial integrity of QORVEXA, the following intellectual property principles are mandatory for all team members (Members 1 through 5):

1. **Right to Contribute:** Contributors must only submit code, models, documentation, or assets that are 100% their own original creation or for which they hold explicit, demonstrable authorization to contribute to a proprietary commercial product.
2. **No Implied Transfer:** No contributor should assume that a GitHub Pull Request, commit signature, or `.github/CODEOWNERS` entry automatically constitutes an open-source contribution, nor does it independently transfer IP rights without formal documentation.
3. **Written IP Assignment / CLA Requirement:** All contributions to QORVEXA must be covered by a formal written Contributor License Agreement (CLA) or Intellectual Property Assignment Agreement approved by Mohamed Osama (Project Owner).
4. **Third-Party Code Prohibitions:** Inserting third-party copyleft code (GPL, AGPL, LGPL) or proprietary proprietary code from employers/universities without explicit written consent is strictly prohibited.

---

## 6. Action Items Requiring Owner & Legal Review

The following legal tasks cannot be resolved purely through code updates and are documented as pending formal legal execution:

- [ ] **Action Item 1 (Legal Counsel Review):** Formalize a standard proprietary Contributor License Agreement (CLA) and Invention Assignment Agreement for execution by all human team members.
- [ ] **Action Item 2 (Trademark Filing):** Conduct trademark clearance and file registration for "QORVEXA" and "Industrial Decision Intelligence" in target jurisdictions (Egypt, EU, US).
- [ ] **Action Item 3 (Third-Party Dependency Audit):** Confirm commercial runtime compatibility of third-party dependencies (specifically HiGHS under MIT, SciPy under BSD-3-Clause, NumPy under BSD-3-Clause, and React under MIT) to ensure no copyleft contagion affects QORVEXA's proprietary core.
