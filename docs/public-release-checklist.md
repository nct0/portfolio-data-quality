# Public release checklist — portfolio-data-quality

**Current status: NOT APPROVED. The repository is private.**

This repository becomes public only after every item below has been verified and the owner has recorded explicit approval. A private repository is not automatically safe to publish: everything in its Git history becomes visible too. Deleting a file in a later commit does not remove it from history.

## Automated checks

- [ ] `bash scripts/check-public-safety.sh --history` reports no findings (with the private denylist loaded)
- [ ] All commit authors and committers use GitHub noreply addresses (part of the scan above)
- [ ] The public-safety and test CI workflows are green on `main`

## Manual review

- [ ] No secrets, tokens, passwords or credentials, anywhere in history
- [ ] No personal email addresses or identifying personal information (history searched for the machine's host name, the Windows username, user-profile paths and private folder names)
- [ ] No real customer data; every dataset is produced by the committed generator and verified as synthetic
- [ ] No employer or client proprietary code or data
- [ ] No state databases (`*.sqlite`, `*.db`) or run output in history
- [ ] Screenshots show no usernames, machine names, file paths, other windows or taskbar details; image metadata stripped
- [ ] Third-party components and their licenses reviewed and attributed
- [ ] A license for this repository chosen by the owner and added (MIT proposed, as for the other portfolios)
- [ ] Setup instructions tested from a fresh clone
- [ ] Every published metric traceable to a committed result
- [ ] Source code reviewed (by the owner)
- [ ] Upwork and Fiverr texts kept outside this repository

## Approval record

| Date | Commit reviewed | Reviewer | Decision |
|---|---|---|---|

Only the repository owner changes visibility. Automated agents never do.
