# Public release checklist — portfolio-data-quality

**Current status: NOT APPROVED. The repository is private.** Agent review on 2026-11-09 (at `895dc09` plus this change): the unticked items below wait for the owner.

This repository becomes public only after every item below has been verified and the owner has recorded explicit approval. A private repository is not automatically safe to publish: everything in its Git history becomes visible too. Deleting a file in a later commit does not remove it from history.

## Automated checks

- [x] `bash scripts/check-public-safety.sh --history` reports no findings (2026-11-09, with the private denylist loaded)
- [x] All commit authors and committers use GitHub noreply addresses (part of the scan above; no commit message carries a tool trailer)
- [x] The public-safety and test CI workflows are green on `main`

## Manual review

- [x] No secrets, tokens, passwords or credentials, anywhere in history (pattern search over every revision: the only matches are the scanner's own patterns)
- [x] No personal email addresses or identifying personal information (history searched for the machine's host name, the Windows username, user-profile paths and private folder names: no matches in text; the workbooks in history unzipped and searched too; workbook author fields are empty)
- [x] No real customer data; every dataset is produced by the committed generator and verified as synthetic (`tests/test_generator.py` regenerates the raw, reference, late-batch and decision files byte-identically)
- [ ] No employer or client proprietary code or data (no employer or client names found; the owner confirms that the imported baseline, tag `baseline`, is their own fictional training material)
- [x] No state databases (`*.sqlite`, `*.db`) or run output in history (no such file in any revision; `samples/` holds regenerated demo outputs on purpose, checked by `tests/test_samples.py`)
- [x] Screenshots show no usernames, machine names, file paths, other windows or taskbar details; image metadata stripped (all 9 PNGs checked by eye: sheet content only; no text, time or EXIF chunks)
- [x] Third-party components and their licenses reviewed and attributed (README, License: XlsxWriter BSD-2-Clause at run time; pytest and openpyxl MIT for tests; nothing vendored)
- [ ] A license for this repository chosen by the owner and added (MIT added in `LICENSE`; the owner confirms the choice)
- [x] Setup instructions tested from a fresh clone (2026-11-09, Python 3.12.10: README section 5 followed; an intermittent Windows failure found this way was fixed in `895dc09`; then 8 of 8 full runs passed, 207 tests each)
- [x] Every published metric traceable to a committed result (traced in case study 01 at M4; the dashboard redesign changed no figures; benchmarks labeled as measured at `cc46d1f`)
- [ ] Source code reviewed (by the owner)
- [x] Upwork and Fiverr texts kept outside this repository (not in any revision)

## Approval record

| Date | Commit reviewed | Reviewer | Decision |
|---|---|---|---|

Only the repository owner changes visibility. Automated agents never do.
