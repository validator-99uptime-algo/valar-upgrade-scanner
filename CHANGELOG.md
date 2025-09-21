# Changelog

All notable changes to this project will be documented here.

## v0.1.0 — 2025-09-21
- Initial public release of the Algorand Valar upgrade scanner.
- Classifies validator ads in the voting window [V−10k..V] via delegator beneficiary addresses (last block wins).
- Outputs CSV with `validator_state`, plus a separate section for validators with no window-active delegators.
- Includes sample CSV and usage instructions in the README.
