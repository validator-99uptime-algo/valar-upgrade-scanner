# Valar Upgrade Scanner

Scan and classify **Valar validator contracts** on **Algorand mainnet** for the 4.3 protocol upgrade voting window.

**What it does**
- Finds delegator contracts **active in the voting window** `[V-10,000 … V]` (inclusive), where `V = upgrade-state.next-protocol-vote-before`.
- Uses their **delegator beneficiary addresses** as proposers and scans Indexer block headers once per validator (**last block wins**).
- Outputs two CSV sections:
  1) validators with ≥1 window-active delegator,
  2) validators with 0 window-active delegators (not eligible to vote).

**Status meanings**
- `UPGRADED` — last in-window header had `upgrade-approve=true`
- `NOT-UPGRADED` — at least one in-window header, but the last had no approve
- `UNKNOWN` — no in-window headers from any window-active delegator

**Output columns**
`validator_owner, validator_ad_app_id, status, delegators, total_yes, total_no, total_none, last_in_window_round, validator_state`

**Prerequisites**
- Python 3.10+ (tested on Ubuntu 24.04)
- A **local algod** node (read-only)
- An **Indexer** HTTPS endpoint (public or self-hosted)

**Install**
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

**Run**

```bash
export ALGOD_ADDRESS="http://localhost:8080"
export ALGOD_TOKEN="YOUR_ALGOD_TOKEN"
export INDEXER_URL="https://mainnet-idx.4160.nodely.dev"
# Optional: export NOTICEBOARD_APP_ID=2713948864
python src/valar_upgrade_scanner.py > report.csv
```

**Notes**

* The tool does **not** read node binary versions (not on-chain). It infers readiness from vote-window headers only.
* “Proposer” here is the **delegator beneficiary**, not the validator owner.
* If Valar rotates the Noticeboard app id, set `NOTICEBOARD_APP_ID` via env.

**Links**

* Valar projects: [https://github.com/ValarStaking/valar/tree/master/projects](https://github.com/ValarStaking/valar/tree/master/projects)
* Validator page: [https://stake.valar.solutions/stake?node\_runner=CMQ6VSWMFA2PPXOPKVRBBRJCF5W4QBXMS53LO66CY2MMN3XP25345HBVQA](https://stake.valar.solutions/stake?node_runner=CMQ6VSWMFA2PPXOPKVRBBRJCF5W4QBXMS53LO66CY2MMN3XP25345HBVQA)

**License**
MIT — see `LICENSE`.

**Attribution**

* GitHub: [https://github.com/validator-99uptime-algo](https://github.com/validator-99uptime-algo)
* X/Twitter: @algo99uptime

