"""Shared test bootstrap: wires the offline SDK stub, loads the contract file
named by TH_CONTRACT (default contract.py; CI also runs contract_deploy.py)
and provides helpers."""
import datetime as _dt
import hashlib as _h
import importlib.util
import json as _json
import os
import sys
from unittest.mock import patch as _patch

_THIS = os.path.dirname(os.path.abspath(__file__))
_STUB = os.path.join(_THIS, "genlayer_stub")
if _STUB not in sys.path:
    sys.path.insert(0, _STUB)

CONTRACT_PATH = os.environ.get("TH_CONTRACT") or os.path.join(os.path.dirname(_THIS), "contract.py")
_spec = importlib.util.spec_from_file_location("tokenhandoff_contract", CONTRACT_PATH)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)

TokenHandoff = getattr(m, "TokenHandoff", None) or m.TokenHandoffTestnet
gl = m.gl
Address = m.Address
u256 = m.u256

TokenHandoff.MIN_STAKE = u256(1)
TokenHandoff.MIN_JUROR_STAKE = u256(1)
TokenHandoff.MIN_APPEAL_BOND = u256(1)

BUYER = "0x" + "11" * 20
SELLER = "0x" + "22" * 20
STRANGER = "0x" + "33" * 20
HOP = "0x" + "44" * 20
NFT = "0x" + "aa" * 20
OTHER_NFT = "0x" + "bb" * 20
HOST = "eth.blockscout.com"
JURORS = ["0x" + hex(0xa0 + i)[2:].rjust(2, "0") * 20 for i in range(12)]
TX1 = "0x" + "e1" * 32
TX2 = "0x" + "e2" * 32
TX3 = "0x" + "e3" * 32


def make_contract():
    return TokenHandoff()


def set_caller(address):
    gl.message.sender_address = Address(address)


def call_payable(contract, method, value, *args, **kwargs):
    gl.message.value = u256(value)
    contract.balance = contract.balance + u256(value)
    try:
        return getattr(contract, method)(*args, **kwargs)
    except Exception:
        contract.balance = contract.balance - u256(value)
        raise
    finally:
        gl.message.value = u256(10 ** 18)


def reset_transfers():
    gl.evm.transfers.clear()


def iso(delta_seconds):
    return (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=delta_seconds)).isoformat()


def tx_url(tx=TX1, suffix=""):
    return f"https://{HOST}/api/v2/transactions/{tx}{suffix}"


def instance_url(contract=NFT, token_id="7", suffix=""):
    return f"https://{HOST}/api/v2/tokens/{contract}/instances/{token_id}{suffix}"


def transfer_item(frm=SELLER, to=BUYER, token=NFT, token_id="7", std="ERC-721", value=None, tx=TX1, li=3,
                  ts=None, **over):
    total = {"token_id": token_id}
    if value is not None:
        total["value"] = value
    item = {
        "transaction_hash": tx, "log_index": li, "timestamp": ts or iso(-60),
        "from": {"hash": frm}, "to": {"hash": to},
        "token": {"address_hash": token, "type": std}, "total": total,
    }
    item.update(over)
    return item


def tx_doc(tx=TX1, ok=True, ts=None, transfers=None, **over):
    doc = {"hash": tx, "status": "ok" if ok else "error", "result": "success" if ok else "reverted",
           "timestamp": ts or iso(-60), "token_transfers": transfers or [], "confirmations": 5}
    doc.update(over)
    return doc


def instance_doc(owner=BUYER, token=NFT, token_id="7", std="ERC-721"):
    return {"id": token_id, "owner": {"hash": owner} if owner else None,
            "token": {"address_hash": token, "type": std}}


def holders_doc(entries, more=False):
    return {"items": [{"address": {"hash": a}, "value": v, "token_id": "7"} for a, v in entries],
            "next_page_params": {"x": 1} if more else None}


def serve(mapping):
    """Patch web.render so each URL returns the JSON of mapping[url]."""
    def render(url, mode="text"):
        if url not in mapping:
            raise RuntimeError("unreachable: " + url)
        value = mapping[url]
        return value if isinstance(value, str) else _json.dumps(value)
    return _patch.object(gl.nondet.web, "render", side_effect=render)


def llm(outcome="ASSET_DELIVERED", reasoning="ok"):
    return _patch.object(gl.nondet, "exec_prompt",
                         side_effect=lambda p, response_format="json": {"outcome": outcome, "reasoning": reasoning})


def new_trade(c, *, value=1000, standard="ERC-721", quantity=1, resale=False, token_id="7", min_sources=1,
              deadline=7200, hosts=HOST):
    set_caller(BUYER)
    return call_payable(c, "create_trade", value, SELLER, "Sell token 7 for 1 ETH", "Token must reach buyer",
                        NFT, token_id, standard, quantity, hosts, iso(deadline), resale, min_sources)


def open_trade(c, **kw):
    tid = new_trade(c, **kw)
    set_caller(SELLER)
    call_payable(c, "accept_trade", kw.get("value", 1000), tid)
    return tid


def get(c, tid):
    return _json.loads(c.get_trade(tid))


def put(c, tid, trade):
    c.trades[tid] = _json.dumps(trade, sort_keys=True)


def mutate(c, tid, **fields):
    t = get(c, tid)
    t.update(fields)
    put(c, tid, t)


def frozen_trade(c, urls, **kw):
    """Open trade with proof from the buyer frozen (grace passed)."""
    tid = open_trade(c, **kw)
    set_created(c, tid, 3600)
    set_caller(BUYER)
    c.submit_proof(tid, urls)
    t = get(c, tid)
    t["evidence_lock_at"] = iso(-5)
    put(c, tid, t)
    set_caller(STRANGER)
    c.freeze_proof(tid)
    return tid


def pass_deadline(c, tid):
    t = get(c, tid)
    t["deadline"] = iso(-5)
    put(c, tid, t)


def set_created(c, tid, seconds_ago):
    t = get(c, tid)
    t["created_at"] = iso(-seconds_ago)
    put(c, tid, t)


def age_jurors(c):
    for key in list(c.jurors.keys()):
        rec = _json.loads(c.jurors[key])
        rec["registered_at"] = "2000-01-01T00:00:00+00:00"
        rec["stake_updated_at"] = "2000-01-01T00:00:00+00:00"
        c.jurors[key] = _json.dumps(rec, sort_keys=True)


def draw_jury(c, tid, beacon="ab" * 32):
    t = get(c, tid)
    now = _dt.datetime.now(_dt.timezone.utc)
    t["appeal_deadline"] = (now - _dt.timedelta(seconds=300)).isoformat()
    t["appeal"]["draw_round"] = int((now.timestamp() - 240 - 1595431050) // 30 + 1)
    put(c, tid, t)
    with _patch.object(gl.nondet.web, "render",
                       return_value=_json.dumps({"round": t["appeal"]["draw_round"], "randomness": beacon})):
        c.draw_jury(tid)


def delivered_urls():
    """Proof links + served documents for a clean ERC-721 delivery."""
    docs = {
        tx_url(suffix="/token-transfers"): {"items": [transfer_item()], "next_page_params": None},
        tx_url(): tx_doc(),
        instance_url(): instance_doc(owner=BUYER),
    }
    return docs


def sha(text):
    return _h.sha256(text.encode()).hexdigest()
