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

- **Cascade-delete on COPPA consent revocation now actually deletes** child data atomically, with an audit-log trail. Implementation: `core/age_verification.py:revoke_parental_consent`. Tests: `tests/test_coppa_consent_revoke_cascade.py`. Privacy Policy § 5.2 has been updated to state that revocation deletion is irreversible. The same statement now also appears in Privacy Policy § 3.1 (Withdrawal of Consent), Privacy Policy § 6.3 (Right to Delete, with a clear distinction between parent-initiated account deletion and consent-revocation cascade), and ToS § 11.1.
- **Retention-period reconciliation between Privacy Policy and DPA**: Privacy Policy § 5.1 and DPA § 3.2 now consistently state that the 180-day window is rolling-from-message-creation, with the executed DPA controlling for Enterprise tier if it conflicts.
- **False or unverifiable claims removed/corrected**:
  - "Multi-factor authentication available" (Privacy Policy § 4.1) — was false; MFA is not implemented. Replaced with "MFA is on the post-launch roadmap and not currently available."
  - DPA § 4.1 "Security training for all staff" and "Background checks for employees with data access" — were false; operator has no employees. Replaced with an honest description of solo-founder controls and a commitment to amend the DPA before the first employee with student-data access is hired.
  - DPA § 8.2 "SOC 2 Type II (if available)" and "ISO 27001 (if available)" — these conditional claims read as misleading at procurement. Replaced with an explicit "not currently held" statement plus a list of compensating controls.
- **Wrong source-code URL in ToS § 6.1 fixed**: was `github.com/tmartin2113/snflwr-ai` (a fork), now correctly cites `github.com/snflwr-ai/snflwr.ai`.
- **GDPR DPO claim removed** (Privacy Policy § 13). The previous text claimed a DPO at `dpo@snflwr.ai` without one actually being appointed, which would have created a GDPR Art. 37 obligation. The policy now states no DPO is appointed under the current processing profile and commits to appointing one before EU/UK processing begins at a scale that triggers Art. 37.
- **All three documents now carry a "DRAFT — NOT IN EFFECT" banner** because operator placeholders remain. The banner cites COPPA § 312.4(d)(1) for the privacy-policy address/phone requirement and points readers at this checklist.

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

## Pre-publication blockers (operator must resolve before the docs can be presented to anyone)

These are operator actions, not lawyer questions — but they must happen before the documents come off "DRAFT" status. They block publication on their own:

1. **Register a business entity.** The current docs reference "snflwr.ai" as the operating party without naming the legal entity. COPPA § 312.4(d)(1) requires the operator's name and contact info; an LLC or corporation is the standard answer for SaaS.
2. **Get a mailing address.** Required by COPPA § 312.4(d)(1) in the privacy policy. Common K-12 pattern: a PO Box separate from the founder's home address.
3. **Get a phone number.** Required for the COPPA Compliance Officer per § 312.4(d)(1). A Google Voice or business VoIP line is fine.
4. **Pick a governing-law state.** Once entity is registered, populate ToS § 13.1 and DPA § 13. If the entity is incorporated in Delaware, Delaware law is the standard choice; otherwise the operator's principal place of business.
5. **Sign the Student Privacy Pledge** (https://studentprivacypledge.org). Privacy Policy § 9.2 currently states this will be signed before the policy enters effect; honor that commitment.
6. **Name actual subprocessors in DPA § 3.3 and Privacy Policy § 7.2.** SendGrid is referenced in `docs/guides/SENDGRID_SETUP.md`; if that's the SMTP provider, name it explicitly. Same for hosting.
7. **Decide GDPR scope.** If EU/UK schools are not in the next 12 months' target, consider removing the GDPR sections entirely rather than carrying placeholders.
8. **Decide CCPA scope.** Privacy Policy's Acknowledgments section cites CCPA compliance, but the body of the policy lacks CCPA-specific required disclosures (right-to-opt-out, "Do Not Sell My Personal Information" link). Either add the disclosures or remove the CCPA acknowledgment.

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
