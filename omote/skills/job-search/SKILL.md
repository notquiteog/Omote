---
name: job-search
description: Find jobs by role and city, build truthful resumes and cover letters, prepare applications and track verified outcomes.
---

Use `career` when available to retain the search, resume variants and application
records across chats in this project. Ask for consequential missing details
together: role types, city/region, remote/hybrid/onsite preference, commute radius,
employment type and salary currency/range. Reuse supplied experience and documents.
Do not assume eligibility, work authorization, relocation or citizenship from names.

Search current web sources with the requested roles and city. Open the employer's
actual listing or authorized job board and verify location, seniority, employment
type, salary when published, requirements, application URL and whether it remains
open. Distinguish remote eligibility regions from office locations. Record source
URLs and relevant retrieval dates in notes. Compare concrete matches to the stated
criteria; explain unknowns and mismatches. Deduplicate the same listing rather than
counting tracking links as new opportunities. Do not promise interviews or outcomes.

Build a base resume from user-provided facts, then save tailored variants separately.
Rephrase and reorder evidence to fit a real listing; never invent credentials,
employers, dates, metrics, projects or screening answers. Ask about missing facts
that materially change the application. Offer a cover letter when useful. The
career exporter creates single-column DOCX/PDF/HTML/TXT; export the requested formats,
inspect layout and selectable text, and retain editable copies. Sources in a resume
record are provenance notes and are not printed in the delivered resume.

Prepare the actual application form using observed browser controls. `browser_upload`
attaches explicitly selected documents and may send them immediately; its message
approval is required. For custom hidden uploaders, use the human handoff instead of
inventing a selector. The user should complete account login, CAPTCHA and sensitive
identity questions through `browser_hand_over`. Ask the user for attestations or
optional demographic disclosures; do not infer them. Review recipient/employer,
role, document versions, screening answers and any fees before final submission.

Submit with `browser_click` and `intent: message` (or `purchase` if money is involved),
using the application's confirmation policy. A prepared form is `ready`, not
`submitted`. After submission, inspect an actual confirmation page or application
status, record the evidence, and only then mark submitted. If a response is lost,
check history/status before retrying to avoid duplicates. Follow-up messages and
recurring searches need the user's authorization; use scheduling only when asked.

Career records are local project data, separate from vector memory. Do not copy
resumes or sensitive identity answers into memory automatically. Report what was
actually completed and what remains blocked by accounts, missing facts or site access.
