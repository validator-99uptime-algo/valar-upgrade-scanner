#!/usr/bin/env python3
# src/valar_upgrade_scanner.py
# SPDX-License-Identifier: MIT
# Valar Upgrade Scanner — v0.1.0 (2025-09-21 UTC)
# © 2025 validator-99uptime-algo
# GitHub: https://github.com/validator-99uptime-algo/valar-upgrade-scanner
# X/Twitter: @algo99uptime
# Network: Algorand (mainnet) — uses local algod + Indexer
# Valar project: https://github.com/ValarStaking/valar/tree/master/projects
# Validator page: https://stake.valar.solutions/stake?node_runner=CMQ6VSWMFA2PPXOPKVRBBRJCF5W4QBXMS53LO66CY2MMN3XP25345HBVQA

"""
Valar Upgrade Scanner — Vote-Window Classification
==================================================

What this does
--------------
For each *Valar validator contract* (a.k.a. "validator ad"):
  • Finds delegator contracts that were **active during the upgrade voting window**
    [V-10_000, V] (inclusive), where V = next-protocol-vote-before.
  • Uses their **delegator beneficiary addresses** (the proposers) to scan block
    headers in the window, and applies **“last block wins”** to classify:
        UPGRADED      → the last in-window header had upgrade-approve = True
        NOT-UPGRADED  → at least one in-window header, but the last had no approve
        UNKNOWN       → no in-window headers from any window-active delegators
  • Outputs two CSV sections:
        (1) validators with ≥1 window-active delegator
        (2) validators with 0 window-active delegators (not eligible to vote)

Data sources
------------
  • Local algod (no database): reads Noticeboard-created apps and each app’s global state
  • Indexer: reads block headers for proposers in the voting window

Rules & definitions
-------------------
  • Voting window:         [V-10_000, V], where V = upgrade-state.next-protocol-vote-before
  • Window-active delegator: its [round_start, round_end] interval overlaps the window
      (round_end = 0/None is treated as "open-ended")
  • Proposer for classification: **delegator beneficiary address** (not val_owner)
  • Classifier: “last block wins” across all window-active delegator addresses

Output columns
--------------
validator_owner, validator_ad_app_id, status, delegators,
total_yes, total_no, total_none, last_in_window_round, validator_state

Environment (set in shell)
--------------------------
  ALGOD_ADDRESS   (e.g., http://localhost:8080)  [default: http://localhost:8080]
  ALGOD_TOKEN     (your node’s API token)        [required if node enforces token]
  INDEXER_URL     (e.g., https://mainnet-idx.4160.nodely.dev)  [required]
  NOTICEBOARD_APP_ID   (optional override, default set below)
  TIMEOUT_S       (optional, default 8.0)
  MAX_WORKERS     (optional, default 12)

Noticeboard
-----------
The default Valar Noticeboard app id is included as a constant (public info). If
Valar deploys a new Noticeboard, set NOTICEBOARD_APP_ID via env to the new id.

Attribution
-----------
  GitHub:  https://github.com/validator-99uptime-algo
  X/Twitter: @algo99uptime
  Validator page: https://stake.valar.solutions/stake?node_runner=CMQ6VSWMFA2PPXOPKVRBBRJCF5W4QBXMS53LO66CY2MMN3XP25345HBVQA

License
-------
MIT (see LICENSE in this repository)

Limitations / Notes
-------------------
  • algod binary version isn’t on-chain; this tool infers upgrade readiness from
    vote-window headers only.
  • Pre-switch vs post-switch behavior differs; this tool focuses on *pre-switch*
    voting window classification.
  • Be mindful of Indexer rate limits; adjust MAX_WORKERS and TIMEOUT_S accordingly.
"""

import os
import sys
import csv
import base64
import requests
from typing import Dict, List, Tuple, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from algosdk.v2client import algod
from algosdk.encoding import encode_address
from algosdk.logic import get_application_address

# ---------------------------
# Public constant + env override
# ---------------------------
DEFAULT_NOTICEBOARD_APP_ID = 2713948864  # Valar Noticeboard (public)
NOTICEBOARD_APP_ID = int(os.getenv("NOTICEBOARD_APP_ID", DEFAULT_NOTICEBOARD_APP_ID))

# Secrets / endpoints via env
ALGOD_ADDRESS = os.getenv("ALGOD_ADDRESS", "http://localhost:8080")
ALGOD_TOKEN   = os.getenv("ALGOD_TOKEN", "")
INDEXER_URL   = os.getenv("INDEXER_URL", "")  # required for header scans

# Tuning knobs
TIMEOUT_S   = float(os.getenv("TIMEOUT_S", "8.0"))
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "12"))

# Validator state enum → label (from Valar smart-contract constants)
STATE_LABEL = {
    0x00: "NONE",
    0x01: "CREATED",
    0x02: "TEMPLATE_LOAD",
    0x03: "TEMPLATE_LOADED",
    0x04: "SET",
    0x05: "READY",
    0x06: "NOT_READY",
    0x07: "NOT_LIVE",
}

# Reuse one Requests session for Indexer
S = requests.Session()

# ---------------------------
# algod client
# ---------------------------
def build_algod() -> algod.AlgodClient:
    # If the node requires a token and ALGOD_TOKEN is empty, requests will 401.
    return algod.AlgodClient(ALGOD_TOKEN, ALGOD_ADDRESS)

# ---------------------------
# Global state decoding helpers
# ---------------------------
def _u64(b: bytes, ofs: int = 0) -> int:
    return int.from_bytes(b[ofs:ofs+8], "big")

def _decode_u64_list(b: bytes) -> List[int]:
    # Valar packs arrays (P/T/W/S/del_app_list) as big-endian u64 slices.
    return [_u64(b, i) for i in range(0, len(b), 8) if i + 8 <= len(b)]

def decode_gs(gs) -> Dict[str, object]:
    """
    Decode app global-state (as returned by algod) into a dict of Python types:
    - uints as int
    - 32-byte addresses as base32 Algorand addresses
    - packed lists (P/T/W/S/del_app_list) as u64 arrays
    - single-byte 'state' stored as an int
    - everything else as hex
    """
    out: Dict[str, object] = {}
    for entry in gs or []:
        key = base64.b64decode(entry["key"]).decode()
        val = entry["value"]
        if val["type"] == 2:
            out[key] = val["uint"]
        else:
            raw = base64.b64decode(val["bytes"])
            if key in ("val_owner", "val_manager", "del_beneficiary", "del_manager") and len(raw) == 32:
                try:
                    out[key] = encode_address(raw)
                except Exception:
                    out[key] = raw.hex()
            elif key in ("P", "T", "W", "S", "del_app_list"):
                out[key] = _decode_u64_list(raw)
            elif key == "state" and len(raw) >= 1:
                out[key] = raw[0]  # single-byte enum
            else:
                out[key] = raw.hex()
    return out

# ---------------------------
# On-chain reads (algod)
# ---------------------------
def noticeboard_validator_ids(client: algod.AlgodClient, nb_app_id: int) -> List[int]:
    """
    The Noticeboard escrow address owns (created-apps) all validator ad apps.
    """
    nb_escrow = get_application_address(nb_app_id)
    created = client.account_info(nb_escrow).get("created-apps", [])
    return [app["id"] for app in created]

def get_validator_info(client: algod.AlgodClient, vid: int) -> Tuple[str, List[int], int]:
    """
    Returns (val_owner, delegator_app_ids, validator_state_byte)
    NOTE: val_owner is *not* the proposer; we classify using delegator beneficiaries.
    """
    gs = client.application_info(vid)["params"].get("global-state", [])
    d  = decode_gs(gs)
    owner = str(d.get("val_owner", ""))  # kept for CSV / reference
    del_list = list(d.get("del_app_list", [])) if isinstance(d.get("del_app_list", []), list) else []
    vstate = int(d.get("state", 0))
    return owner, del_list, vstate

def get_delegator_fields(client: algod.AlgodClient, did: int) -> Tuple[Optional[int], Optional[int], Optional[str]]:
    """
    Returns (round_start, round_end, delegator_beneficiary_address)
    """
    gs = client.application_info(did)["params"].get("global-state", [])
    d  = decode_gs(gs)
    rs = int(d["round_start"]) if "round_start" in d and d["round_start"] is not None else None
    re = int(d["round_end"])   if "round_end"   in d and d["round_end"]   is not None else None
    bene = str(d["del_beneficiary"]) if d.get("del_beneficiary") else None
    return rs, re, bene

# ---------------------------
# Indexer reads
# ---------------------------
def current_round_indexer() -> int:
    if not INDEXER_URL:
        raise RuntimeError("INDEXER_URL env is required")
    r = S.get(f"{INDEXER_URL}/v2/transactions", params={"limit": 1}, timeout=TIMEOUT_S)
    r.raise_for_status()
    return r.json()["current-round"]

def voting_window(cur_round: int) -> Tuple[int, int]:
    """
    Reads a recent header to get upgrade-state.next-protocol-vote-before (V),
    then returns [V-10_000, V] (inclusive).
    """
    r = S.get(f"{INDEXER_URL}/v2/blocks/{cur_round}", params={"header-only": "true"}, timeout=TIMEOUT_S)
    r.raise_for_status()
    blk = (r.json().get("block") or r.json())
    V = int((blk.get("upgrade-state", {}) or {}).get("next-protocol-vote-before") or 0)
    if not V:
        raise RuntimeError("vote-before not available from Indexer header")
    return V - 10_000, V

def last_in_window_for_addrs(addrs: List[str], ws: int, we: int) -> Tuple[Optional[int], Optional[bool]]:
    """
    Single batched scan for all proposer addresses in [ws, we].
    Returns (last_round, last_approve) where "last" is the highest round found.
    """
    if not addrs:
        return None, None
    next_tok = ""
    last_round: Optional[int] = None
    last_approve: Optional[bool] = None
    params = {"proposers": ",".join(addrs), "min-round": ws, "max-round": we, "limit": 1000}
    while True:
        if next_tok:
            params["next"] = next_tok
        r = S.get(f"{INDEXER_URL}/v2/block-headers", params=params, timeout=TIMEOUT_S)
        r.raise_for_status()
        data = r.json()
        for h in data.get("blocks", []):
            rnd = int(h["round"])
            appr = (h.get("upgrade-vote", {}) or {}).get("upgrade-approve") is True
            # Ascending order: overwrite to keep the last (highest) round.
            last_round = rnd
            last_approve = appr
        next_tok = data.get("next-token", "")
        if not next_tok:
            break
    return last_round, last_approve

# ---------------------------
# Core logic
# ---------------------------
def overlaps_window(rs: Optional[int], re: Optional[int], ws: int, we: int) -> bool:
    """
    Window-activity test: does [round_start, round_end] overlap [ws, we]?
    Treat missing/zero round_end as "open".
    """
    if rs is None:
        return False
    if rs > we:
        return False
    if re is None or re == 0:
        return True
    return int(re) >= ws

def classify(last_approve: Optional[bool], had_any: bool) -> str:
    """
    Classification with “last block wins” semantics.
    """
    if last_approve is True:
        return "UPGRADED"
    if had_any and last_approve is not True:
        return "NOT-UPGRADED"
    return "UNKNOWN"

def main():
    client = build_algod()

    # Determine the voting window from Indexer
    cur = current_round_indexer()
    ws, we = voting_window(cur)
    print(f"# VOTING_WINDOW [{ws},{we}]  (inclusive)")

    # Discover all validator ads from the Noticeboard escrow
    vids = noticeboard_validator_ids(client, NOTICEBOARD_APP_ID)

    main_rows: List[List[str]] = []
    zero_rows: List[List[str]] = []

    def work(vid: int):
        """
        Per-validator worker:
          1) read validator state (val_owner, del_app_list, validator_state)
          2) collect proposers = delegator beneficiaries ACTIVE IN WINDOW
          3) scan Indexer headers in [ws, we] across those proposers
          4) “last block wins” classification
        """
        try:
            owner, delids, vstate = get_validator_info(client, vid)
            vstate_label = STATE_LABEL.get(int(vstate), f"UNKNOWN_STATE_{vstate}")

            # Build proposer set only from delegators whose life overlaps the window
            proposers: List[str] = []
            for did in delids:
                try:
                    rs, re, bene = get_delegator_fields(client, did)
                    if bene and overlaps_window(rs, re, ws, we):
                        proposers.append(bene)
                except Exception:
                    continue

            if not proposers:
                # Not eligible to vote in the window → list separately
                return ("ZERO", [owner, str(vid), "UNKNOWN", "0", "0", "0", "0", "", vstate_label])

            # Single batched Indexer query: last header across all proposers
            last_round, last_approve = last_in_window_for_addrs(proposers, ws, we)
            had_any = (last_round is not None)
            status = classify(last_approve, had_any)

            # Minimal counts (diagnostic): whether any header was found and if it approved
            total_yes  = "1" if last_approve is True else "0"
            total_no   = "1" if (had_any and last_approve is not True) else "0"
            total_none = "0" if had_any else str(len(proposers))

            row = [owner, str(vid), status, str(len(proposers)), total_yes, total_no, total_none,
                   (str(last_round) if had_any else ""), vstate_label]
            return ("MAIN", row)

        except Exception:
            # Conservative fallback
            return ("MAIN", ["", str(vid), "UNKNOWN", "0", "0", "0", "0", "", "UNKNOWN_STATE"])

    # Parallelize across validators (Indexers typically handle this load, but tune MAX_WORKERS if needed)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = [ex.submit(work, vid) for vid in vids]
        for f in as_completed(futs):
            kind, row = f.result()
            (main_rows if kind == "MAIN" else zero_rows).append(row)

    # Stable ordering for CSV review
    main_rows.sort(key=lambda r: (r[0], int(r[1]) if r[1].isdigit() else 0))
    zero_rows.sort(key=lambda r: (r[0], int(r[1]) if r[1].isdigit() else 0))

    # CSV output
    w = csv.writer(sys.stdout, lineterminator="\n")
    w.writerow([
        "validator_owner","validator_ad_app_id","status","delegators",
        "total_yes","total_no","total_none","last_in_window_round","validator_state"
    ])
    for r in main_rows:
        w.writerow(r)

    print("\n# validators_with_no_window_active_delegators")
    w.writerow([
        "validator_owner","validator_ad_app_id","status","delegators",
        "total_yes","total_no","total_none","last_in_window_round","validator_state"
    ])
    for r in zero_rows:
        w.writerow(r)

# ---------------------------
# Entrypoint
# ---------------------------
if __name__ == "__main__":
    if not INDEXER_URL:
        print("ERROR: INDEXER_URL is required (e.g. export INDEXER_URL=https://mainnet-idx.4160.nodely.dev)", file=sys.stderr)
        sys.exit(2)
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)
