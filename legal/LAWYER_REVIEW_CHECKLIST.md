# Privacy Counsel Review Checklist

This document is a hand-off for K-12 ed-tech privacy counsel. It exists so the reviewing attorney can spend their time on the things that need a lawyer rather than rediscovering the document set.

**Not legal advice.** Items 1–12 below were surfaced by a non-lawyer audit against published K-12 ed-tech norms (SDPC NDPA, FERPA 34 CFR Part 99, COPPA 16 CFR Part 312, and the state statutes named below). Treat them as research starting points, not conclusions.

## Document set in scope

| File | Purpose | Last meaningful change |
|---|---|---|
| `legal/PRIVACY_POLICY.md` | Parent-facing privacy policy | `git log -1 --format=%cs legal/PRIVACY_POLICY.md` |
| `legal/TERMS_OF_SERVICE.md` | Parent + school ToS | `git log -1 --format=%cs legal/TERMS_OF_SERVICE.md` |
| `legal/DATA_PROCESSING_AGREEMENT.md` | School-side DPA template | `git log -1 --format=%cs legal/DATA_PROCESSING_AGREEMENT.md` |
| `docs/compliance/COPPA_CONSENT_MECHANISM.md` | Operator's COPPA consent flow doc | Background only |
| `docs/compliance/FERPA_COPPA_COMPLIANCE_AUDIT.md` | Operator's compliance self-audit | Background only |

## Engineering changes already made (so the lawyer doesn't have to flag them)

- **Cascade-delete on COPPA consent revocation now actually deletes** child data atomically, with an audit-log trail. Implementation: `core/age_verification.py:revoke_parental_consent`. Tests: `tests/test_coppa_consent_revoke_cascade.py`. Privacy Policy § 5.2 has been updated to state that revocation deletion is irreversible.
- **Retention-period reconciliation between Privacy Policy and DPA**: Privacy Policy now states the 180-day window is rolling-from-message-creation and that the DPA's school-set value controls for Enterprise tier when it conflicts.

## Items for counsel review

1. **Retention clash carry-overs.** Privacy Policy § 5.1 ("180 days after creation, rolling") and DPA § 3.2 ("Educational Content: 180 days") and DPA § 9 ("Active student data: While student enrolled" / "Graduated/transferred: 180 days default"). Confirm:
   - Is a 180-day rolling window during enrollment permissible under FERPA 34 CFR § 99.31 for an institution's own educational records?
   - Does the post-graduation 180-day default conflict with state-specific requirements (NY Ed Law § 2-d sets longer minimums for some categories)?

2. **FERPA "school official with legitimate educational interest" designation.** DPA § 2.1 describes snflwr.ai as Processor with the school as Controller, but does not include a 34 CFR § 99.3 designation letter. Confirm whether the processor-only model is sufficient for schools in your target states, or whether a separate school-official designation exhibit should be added (suggested Appendix D).

3. **Arbitration carve-outs.** ToS § 12.2 mandates arbitration; § 12.3 waives class actions. Confirm:
   - Carve-out for FERPA, COPPA, and state student-data-privacy statute claims.
   - Enforceability of the class-action waiver in California (McGill rule) and Illinois (BIPA-adjacent precedent).
   - Injunctive-relief exception coverage for FERPA injunctions.

4. **GDPR SCCs.** Privacy Policy § 13 references GDPR + DPO; DPA § 9.1 references SCCs but the Appendix C placeholder is empty. If EU schools are out of scope for the foreseeable future, the document should say so explicitly rather than leaving placeholders that imply readiness. Otherwise, populate with the current Module 2 / Module 3 SCC text.

5. **Sub-processor disclosure.** Privacy Policy § 7.2 and DPA § 3.3 list "[Email Provider]" and "[Hosting Provider]" as placeholders. Decide:
   - Name actual subprocessors (SendGrid is referenced as the SMTP provider in `docs/guides/SENDGRID_SETUP.md`).
   - Confirm subprocessor agreements include FERPA-compatible terms or HIPAA-BAA-equivalent language where applicable.
   - Set a notification cadence for adding new subprocessors (default 30-day notice + objection right is in DPA § 3.3 — confirm this is contemporary practice).

6. **Compliance certification language.** DPA § 8.2 reads "SOC 2 Type II (if available)" and "ISO 27001 (if available)." Schools will ask for these at procurement. Either:
   - Commit to a timeline and remove the conditional, or
   - Replace with stronger compensating commitments (e.g., scheduled third-party pen-test results, increased audit rights).

7. **NDPA Exhibit B alignment.** Privacy Policy § 2.2 ("DO NOT sell, lease, or trade") and DPA § 1 are close to SDPC NDPA Exhibit B § 1 (Purpose Limitation) but lack the explicit law-enforcement carve-out and the "necessary for service delivery" definition. Add or confirm not-applicable.

8. **State-specific riders.** No state addenda are present. The five states most commonly required for K-12 deployments are:
   - **California**: SOPIPA + AB 1584 + CA-specific COPPA + (for 2026+ deployments) AB 2273.
   - **New York**: Ed Law § 2-d.
   - **Illinois**: SOPPA (105 ILCS 85).
   - **Texas**: SB 820 / TX student data privacy chapter.
   - **Connecticut**: § 10-234aa.

   Decide which to add now versus on-demand-by-customer.

9. **Breach-mitigation cost allocation.** DPA § 6 names notification timelines but is silent on credit monitoring, identity-theft insurance, legal-fee responsibility, and settlement costs. Schools will ask. Decide:
   - snflwr.ai pays breach-notification costs (mailing, AG filings).
   - School responsible for remediation services at school's discretion.
   - Or accept unlimited liability for breaches.

10. **Law-enforcement disclosure process.** Privacy Policy § 10 and DPA § 6 describe operator-to-parent and operator-to-regulator notification but not how snflwr.ai responds to a subpoena or warrant directed at student data. Add:
    - Subpoena receipt → 24-hour notification to affected school(s), if not legally prohibited.
    - School directs response (operator does not produce student data without school direction unless legally required).
    - Operator commits to good-faith objection to overbroad requests per COPPA § 312.4(b) and FERPA § 99.31(a)(9).

11. **Consent withdrawal recovery window.** Privacy Policy § 5.2 originally stated "30-day grace period for recovery" but, with the cascade-delete change shipped today, this no longer applies to consent revocation specifically. The policy has been edited to say revocation deletion is irreversible. Confirm:
    - The irreversibility is consistent with the school's expectations under their DPA.
    - Operator's audit-log retention of revocation metadata (parent ID, profile ID, timestamp, reason) is the right minimum and does not itself contain PII subject to further obligations.

12. **School Official Designation exhibit.** Add (or confirm not needed) a template letter the school signs designating snflwr.ai as a school official with a specific scope (curriculum support / tutoring) and a "legitimate educational interest" rationale. Many districts require this as a prerequisite for FERPA-compliant data sharing.

## Estimated lawyer time

A first pass on the items above is approximately 4–6 hours for an experienced K-12 ed-tech privacy attorney. Plan ~2 weeks of calendar time for the revision cycle, longer if state riders are in scope.

## Lawyer prep packet

Before the review meeting, hand the attorney:

- This checklist.
- The three primary documents (`PRIVACY_POLICY.md`, `TERMS_OF_SERVICE.md`, `DATA_PROCESSING_AGREEMENT.md`).
- `docs/compliance/FERPA_COPPA_COMPLIANCE_AUDIT.md` for context on the self-audit.
- The cascade-delete change (`core/age_verification.py:revoke_parental_consent` plus the COPPA test file) — this is the most consequential recent change to the compliance posture.
- A list of US states you currently have customers in or plan to enter within 6 months.

Track the attorney's response in a follow-up doc (do not silently edit this checklist — the engineering team needs to know what was deferred vs. accepted).
