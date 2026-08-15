# PD vs RAG — descriptive benchmark: okb-pub-v1

> Descriptive comparison only: frozen questions and pre-written gold evidence, equal per-passage evidence budgets, identical answer contract — but no blinding, no statistical tests, and no general superiority claims.

- spec: `spec.toml` · 26 question(s)
- model: `anthropic:claude-sonnet-5` · embedder: `ollama:nomic-embed-text`
- revisions: handbook=`rev-426d78496f5a`, ripgrep=`rev-d47e22347f12`
- okf-core 0.3.3 · generator v3 · prompts {'concept-description': '1', 'concept-plan': '1', 'answer-question': '1'}

## Summary

| metric | pd | rag |
|---|---|---|
| answered | 26 | 26 |
| abstained | 0 | 0 |
| gold-evidence hit | 16/26 | 19/26 |
| gold file retrieved directly | 0/26 | 19/26 |
| abstention appropriate | 22/26 | 22/26 |
| trust warnings surfaced | 206 | 0 |
| median tokens | 5426 | 406 |
| median latency (ms) | 2360 | 2914 |
| mean tool calls | 12.8 | 1.0 |
| budget exhausted | 0 | 0 |
| est. cost (USD, list price) | $0.1018 | $0.1257 |

## Per-question

| question | class | condition | abstained | gold hit | retrieved | tokens | tools | cited |
|---|---|---|---|---|---|---|---|---|
| q01 | lookup | pd | no | yes | no | 7267 | 15 | `concepts/trello.md`, `concepts/term-extensions.md`, `concepts/account-manager.md`, `concepts/digital-council.md` |
| q01 | lookup | rag | no | yes | yes | 356 | 1 | `references/source-207.md`, `references/source-066.md` |
| q02 | lookup | pd | no | yes | no | 6283 | 11 | `concepts/security-incidents.md`, `concepts/authority-to-use-atu-process.md`, `concepts/github.md`, `concepts/account-manager.md` |
| q02 | lookup | rag | no | yes | yes | 443 | 1 | `references/source-099.md` |
| q03 | lookup | pd | no | yes | no | 6380 | 11 | `concepts/transit-benefit.md`, `concepts/benefits.md`, `concepts/chicago.md`, `concepts/going-out-of-office.md` |
| q03 | lookup | rag | no | yes | yes | 410 | 1 | `references/source-079.md`, `references/source-103.md` |
| q04 | lookup | pd | no | no | no | 5192 | 9 | `concepts/git-signing.md`, `concepts/account-manager.md`, `concepts/github.md`, `concepts/edit-in-github.md` |
| q04 | lookup | rag | no | yes | yes | 368 | 1 | `references/source-096.md`, `references/source-125.md`, `references/source-111.md`, `references/source-109.md` |
| q05 | synthesis | pd | no | yes | no | 8550 | 15 | `concepts/password-requirements.md`, `concepts/reasonable-accommodations.md`, `concepts/how-to-log-in.md`, `concepts/equipment.md` |
| q05 | synthesis | rag | no | yes | yes | 389 | 1 | `references/source-096.md`, `references/source-109.md`, `references/source-125.md`, `references/source-105.md` |
| q06 | synthesis | pd | no | no | no | 5360 | 9 | `concepts/handbook-implementation-plan-q2-fy-2025-issue-backlog.md`, `concepts/accessibility.md`, `concepts/github.md`, `concepts/gsa-internal-tools.md` |
| q06 | synthesis | rag | no | yes | yes | 493 | 1 | `references/source-066.md`, `references/source-134.md`, `references/source-057.md` |
| q07 | multi-hop | pd | no | no | no | 8532 | 15 | `concepts/paid-parental-leave.md`, `concepts/fmla.md`, `concepts/tock.md`, `concepts/leave.md` |
| q07 | multi-hop | rag | no | yes | yes | 413 | 1 | `references/source-227.md`, `references/source-225.md`, `references/source-206.md` |
| q08 | multi-hop | pd | no | no | no | 7624 | 13 | `concepts/mural.md`, `concepts/how-we-relate-to-partners.md`, `concepts/gsa-internal-tools.md`, `concepts/pra-for-user-research.md` |
| q08 | multi-hop | rag | no | yes | yes | 516 | 1 | `references/source-066.md`, `references/source-187.md` |
| q09 | multi-hop | pd | no | yes | no | 8444 | 16 | `concepts/olu.md`, `concepts/tock.md`, `concepts/tock-2.md`, `concepts/training.md` |
| q09 | multi-hop | rag | no | yes | yes | 384 | 1 | `references/source-218.md`, `references/source-053.md`, `references/source-054.md` |
| q10 | trust | pd | no | yes | no | 8053 | 15 | `concepts/july-13-travel-guidance.md`, `concepts/hosting-non-government-speakers.md`, `concepts/conferences-events-training.md`, `concepts/meetings-and-meeting-tools.md` |
| q10 | trust | rag | no | yes | yes | 401 | 1 | `references/source-224.md`, `references/source-239.md`, `references/source-133.md`, `references/source-212.md` |
| q11 | trust | pd | no | yes | no | 8556 | 13 | `concepts/003-tts-cybersecurity-advisor.md`, `concepts/hiring-authorities.md`, `concepts/roles-and-responsibilities.md`, `concepts/collaboration.md` |
| q11 | trust | rag | no | yes | yes | 478 | 1 | `references/source-155.md` |
| q12 | unanswerable | pd | no | no | no | 6362 | 11 | `concepts/security-incidents.md`, `concepts/san-francisco.md`, `concepts/glossary-2.md`, `concepts/account-manager.md` |
| q12 | unanswerable | rag | no | no | no | 402 | 1 | `references/source-063.md`, `references/source-013.md`, `references/source-025.md`, `references/source-018.md` |
| q13 | unanswerable | pd | no | no | no | 7457 | 15 | `concepts/security-policy.md`, `concepts/index-2.md`, `concepts/bug-bounty.md`, `concepts/code-of-conduct.md` |
| q13 | unanswerable | rag | no | no | no | 462 | 1 | `references/source-103.md`, `references/source-083.md`, `references/source-082.md` |
| q14 | lookup | pd | no | yes | no | 6198 | 18 | `concepts/faq.md`, `concepts/readme.md`, `concepts/guide.md`, `concepts/readme-7.md` |
| q14 | lookup | rag | no | yes | yes | 471 | 1 | `references/source-006.md`, `references/source-007.md` |
| q15 | lookup | pd | no | yes | no | 4509 | 11 | `concepts/changelog.md`, `concepts/readme.md`, `concepts/faq.md`, `concepts/guide.md` |
| q15 | lookup | rag | no | yes | yes | 400 | 1 | `references/source-003.md` |
| q16 | lookup | pd | no | yes | no | 2634 | 11 | `concepts/ai-policy.md`, `concepts/contributing.md`, `concepts/sherlock-nul.md` |
| q16 | lookup | rag | no | yes | yes | 337 | 1 | `references/source-002.md`, `references/source-004.md`, `references/source-005.md` |
| q17 | lookup | pd | no | yes | no | 4793 | 12 | `concepts/ai-policy.md`, `concepts/readme.md`, `concepts/faq.md`, `concepts/guide.md` |
| q17 | lookup | rag | no | yes | yes | 454 | 1 | `references/source-005.md`, `references/source-003.md` |
| q18 | synthesis | pd | no | yes | no | 5333 | 14 | `concepts/ai-policy.md`, `concepts/readme.md`, `concepts/faq.md`, `concepts/guide.md` |
| q18 | synthesis | rag | no | no | no | 401 | 1 | `references/source-005.md`, `references/source-006.md` |
| q19 | synthesis | pd | no | yes | no | 5493 | 14 | `concepts/faq.md`, `concepts/readme.md`, `concepts/readme-7.md`, `concepts/guide.md` |
| q19 | synthesis | rag | no | yes | yes | 518 | 1 | `references/source-005.md`, `references/source-007.md` |
| q20 | multi-hop | pd | no | yes | no | 5223 | 14 | `concepts/release-checklist.md`, `concepts/readme.md`, `concepts/readme-9.md`, `concepts/faq.md` |
| q20 | multi-hop | rag | no | yes | yes | 304 | 1 | `references/source-008.md`, `references/source-003.md` |
| q21 | multi-hop | pd | no | no | no | 3928 | 9 | `concepts/faq.md`, `concepts/readme.md`, `concepts/guide.md`, `concepts/changelog.md` |
| q21 | multi-hop | rag | no | no | no | 391 | 1 | `references/source-013.md`, `references/source-003.md`, `references/source-007.md`, `references/source-008.md` |
| q22 | multi-hop | pd | no | yes | no | 5230 | 14 | `concepts/readme-9.md`, `concepts/readme.md`, `concepts/changelog.md`, `concepts/readme-11.md` |
| q22 | multi-hop | rag | no | yes | yes | 395 | 1 | `references/source-003.md`, `references/source-016.md`, `references/source-006.md` |
| q23 | trust | pd | no | yes | no | 3459 | 11 | `concepts/changelog.md`, `concepts/faq.md`, `concepts/guide.md`, `concepts/readme-10.md` |
| q23 | trust | rag | no | yes | yes | 418 | 1 | `references/source-003.md`, `references/source-005.md` |
| q24 | trust | pd | no | no | no | 5196 | 13 | `concepts/readme.md`, `concepts/readme-2.md`, `concepts/faq.md`, `concepts/guide.md` |
| q24 | trust | rag | no | no | no | 400 | 1 | `references/source-005.md`, `references/source-007.md`, `references/source-003.md` |
| q25 | unanswerable | pd | no | no | no | 4474 | 11 | `concepts/readme.md`, `concepts/faq.md`, `concepts/readme-14.md`, `concepts/guide.md` |
| q25 | unanswerable | rag | no | no | no | 513 | 1 | `references/source-007.md`, `references/source-005.md` |
| q26 | unanswerable | pd | no | no | no | 5043 | 13 | `concepts/readme.md`, `concepts/faq.md`, `concepts/guide.md`, `concepts/readme-10.md` |
| q26 | unanswerable | rag | no | no | no | 508 | 1 | `references/source-005.md`, `references/source-003.md`, `references/source-008.md`, `references/source-007.md` |
