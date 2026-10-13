# TokenHandoff

A two-tier adjudication protocol for one narrow dispute: **did a specific, non-fungible asset reach the buyer in an over-the-counter NFT sale?** It is a single GenLayer Intelligent Contract. An automated ruling (Tier 1) is backed by a staked, commit-reveal jury appeal (Tier 2).

TokenHandoff reuses the proven mechanics of TradeAnchor (frozen evidence, deterministic validator consensus, staked jury drawn from a drand round, pull payments, expiry paths, one-use proof claims) and replaces the domain layer. TradeAnchor proves that a fungible payment arrived. TokenHandoff proves that one particular token was delivered, and that it is still where a delivery should leave it.

## How a sale is anchored

The buyer (party A) creates the trade and the seller (party B) accepts it. Both lock the same bond in GEN; the sale price itself is settled off-chain. `create_trade` records these anchors on-chain before any dispute exists:

| Anchor | Meaning |
| --- | --- |
| `nft_contract` | exact contract address of the collection |
| `token_id` | exact token id (decimal or `0x` hex, stored as a decimal string) |
| `token_standard`, `token_quantity` | `ERC-721` (quantity 1) or `ERC-1155` with a quantity |
| buyer, seller | the two real callers, never free text |
| `deadline_iso` | delivery must happen in `[trade creation, deadline]` |
| `allowed_evidence_hosts` | Blockscout explorer(s) of the chain the NFT lives on, e.g. `eth.blockscout.com`. The chain is identified by these hosts, so list explorers of that one chain only |
| `allow_resale` | when `false`, the buyer must still own the token at resolution |

## What counts as proof

Evidence is restricted to Blockscout v2 API links on the allowed hosts, exactly in the forms below, with no query string or fragment (a query could page or filter a listing to hide a transfer). Anything else is rejected when it is submitted.

| Link | Used for |
| --- | --- |
| `/api/v2/transactions/<hash>` | transaction success and time |
| `/api/v2/transactions/<hash>/token-transfers` | the transfers in that transaction |
| `/api/v2/tokens/<contract>/instances/<id>` | current owner (ERC-721) |
| `/api/v2/tokens/<contract>/instances/<id>/transfers` | transfer history of the token |
| `/api/v2/tokens/<contract>/instances/<id>/holders` | current holdings (ERC-1155) |

The contract parses the JSON in code. A delivery is `ASSET_DELIVERED` only if **every** check passes:

- `token.address_hash` equals the anchored contract exactly
- `total.token_id` equals the anchored id (string and integer forms compare equal)
- token type matches the anchored standard, and for ERC-1155 the seller-to-buyer transfers of the anchored id inside the window add up to exactly the anchored amount (a delivery may be split over several transfers, and batch transfers count)
- `from` is the seller and `to` is the buyer
- the parent transaction succeeded
- the transfer timestamp is inside `[trade creation, deadline]`
- the transfer (transaction hash + log index) has not settled another trade; for a split ERC-1155 delivery none of its transfers has
- the buyer still holds the token (instance owner, or holders for ERC-1155), unless the trade was created with `allow_resale=true`

## Outcomes

| Outcome | When |
| --- | --- |
| `ASSET_DELIVERED` | all checks above pass |
| `PARTIALLY_DELIVERED` | right contract, delivered seller to buyer in time, but a wrong token id or a total quantity other than the anchored one |
| `NOT_DELIVERED` | affirmative evidence: the delivery came after the deadline, or the seller still holds the token |
| `UNDETERMINED` | anything ambiguous, including a hop through a burner or escrow address, a wrapped or proxied token, a token that moved on, a truncated listing, a missing transaction record, or a transfer whose token id or amount cannot be read |

Settlement: delivered pays the seller both bonds, not delivered pays the buyer both bonds, partial splits them evenly and undetermined returns each bond (with equal bonds these two pay the same; the outcome label still records what happened, and a partial delivery still uses up its transfer).

## What is different from TradeAnchor

- **Non-fungible proof.** Contract, token id, standard and quantity are matched exactly; a worthless lookalike cannot stand in.
- **Current-owner check.** Delivery is not enough: the buyer must still hold the token, unless `allow_resale` was chosen up front. A transfer routed through a burner or escrow hop, a wrapped or proxied token, and a token that moved on all resolve `UNDETERMINED`.
- **Code backstop.** The model can only confirm the code's deterministic ceiling or answer `UNDETERMINED`. Any `ASSET_DELIVERED` verdict is forced down to `UNDETERMINED` unless every structured check passes, and the check is recomputed from the stored frozen pages after consensus.
- **No raw page text reaches the model.** The model sees only canonical parsed facts, so there is no free text to inject into.
- **One transfer, one trade.** Claims are keyed by `<tx hash>:<log index>`.
- **Evidence sources are counted by registrable domain**, so sibling hosts of one site are one source.
- **Explore view backed by a contract view.** `get_trades_page(offset, limit)` returns compact summaries, newest first.

## Lifecycle

```
created -> accepted -> proof_frozen -> ruled -> finalized (no appeal)
                                         |
                                         v (appeal + bond, within window)
                                    appeal_filed -> (window closes) draw_jury
                                         -> jury_voting (commit -> reveal) -> finalized
```

`cancelled` (before acceptance) and the expiry methods `expire_no_proof`, `expire_stalled_ruling` and `expire_stuck_appeal` are the other exits. Every path pays out or refunds; money only moves through `withdraw()`.

## Files

| File | Purpose |
| --- | --- |
| `contract.py` | the documented source |
| `contract_deploy.py` | the same code with docstrings and comments stripped through the AST, two header lines only. Deploy this one to GenLayer Studio |
| `contract_testnet_deploy.py` | the same code with short windows (minutes) and class `TokenHandoffTestnet`, for quick live jury tests |
| `tools/make_deploy.py` | regenerates both deploy files from `contract.py` (`--testnet` for the short-window file) |
| `index.html` | single-page frontend (genlayer-js from esm.sh), hash routes `#/`, `#/create`, `#/trade/<id>`, `#/jury`, `#/withdraw` |
| `ARCHITECTURE.md` | design, hardening notes, disclosed limitations |
| `tests/` | offline test suite and the SDK stub |

## Tests

307 offline unit tests against a self-written `genlayer` SDK stub in `tests/genlayer_stub/`. No `pip install` is needed:

```
python3 -m unittest discover -s tests -v
```

Run the same suite against the deploy files with `TH_CONTRACT`:

```
TH_CONTRACT="$PWD/contract_deploy.py" python3 -m unittest discover -s tests
```

The suite covers number and address normalisation, the Blockscout parsers, every branch of the delivery assessment (clean delivery, wrong id, wrong quantity, wrong contract, wrong parties, hops, wrapped tokens, moved tokens, resale, late and early transfers, failed transactions, truncated listings, ERC-1155 holdings, batch and split deliveries), the model backstop, validator agreement, replay across trades, the full lifecycle, settlement, expiry paths, the jury (selection replay, commit-reveal, slashing, verification) and the shape of the deploy files.

## Frontend

Open `index.html` once with `?contract=0x<deployed address>` (or paste the address on the Explore page). The address is remembered in that browser. The page uses `createClient({ chain: studionet, account })` only.

## Status

The contract, the deploy files, the frontend and the offline suite are complete.

| | |
| --- | --- |
| Deployed contract (GenLayer Studio) | `0x14a656c20bE9DeE12a9D6AfBAB669ADe259954B5` |
| Explorer | https://explorer-studio.genlayer.com/address/0x14a656c20bE9DeE12a9D6AfBAB669ADe259954B5 |

Live runs on this deployment (buyer `0xBD767E1958928ff1CAaE1218dbCb4b2f2ada35F6`, seller `0x8EDC6794937Ed1DC6476f88A51f1cC52b4fC571C`, 1 GEN bond each):

| Step | Call | Result | Tx |
| --- | --- | --- | --- |
| Create trade_0 (BAYC #1, ERC-721, `eth.blockscout.com`) | `create_trade` | SUCCESS | `0xf98974c4…1b13991a` |
| Seller accepts with the matching bond | `accept_trade` | SUCCESS | `0x29edc0d9…0cbb7d09` |
| Proof link on a host that is not allowed (`etherscan.io`) | `submit_proof` | ERROR, rejected as intended | `0x6f5263af…6488ff29` |
| Blockscout link with a query string (`?x=1`) | `submit_proof` | ERROR, rejected as intended | `0xd13e0f86…fe8ec12f` |
| Valid instance link, buyer | `submit_proof` | SUCCESS | `0xe734a926…33d32984` |
| Valid instance link, seller (both in, so the proof froze) | `submit_proof` | SUCCESS, status `proof_frozen` | `0xf7fb2050…645a2365` |
| Resolve before the deadline without a proven delivery | `resolve_delivery` | ERROR, trade left untouched in `proof_frozen` as intended | `0x3018c38e…c92ad376` |
| Create trade_1 | `create_trade` | SUCCESS | `0x6eed56fa…0894adde` |
| Seller tries to cancel | `cancel_trade` | ERROR, buyer-only as intended | `0xd222934e…3cc0c8e6` |
| Buyer cancels, 1 GEN credited | `cancel_trade` | SUCCESS, status `cancelled` | `0x8fc82ba0…3fa55beb` |
| Buyer withdraws | `withdraw` | SUCCESS, 1 GEN sent out (`0x5a14805b…94ccf3d1`) | `0x8f8829f9…b42daa6e` |
| Buyer withdraws again with nothing pending | `withdraw` | ERROR, as intended | `0x7d08e0ae…64cb0522` |
| Resolve trade_0 after the deadline | `resolve_delivery` | SUCCESS: validators fetched the frozen Blockscout instance link, parsed the owner in code and ruled `UNDETERMINED` (ceiling `UNDETERMINED`: "no affirmative evidence of delivery or of non-delivery"; the current owner of BAYC #1 is a third address, not the buyer). Status `ruled`, appeal window open until 2026-10-15 16:04 UTC | see explorer |

After these steps the contract holds exactly the 2 GEN of trade_0's two bonds. The `trade_0` ruling is the safe outcome for this evidence: with no seller-to-buyer transfer in the submitted links, the code ceiling cannot reach `ASSET_DELIVERED`, so the model could not have recorded it.

### Live jury run (Tier 2)

The same code with minute-scale windows (`contract_testnet_deploy.py`, class `TokenHandoffTestnet`) was deployed to Studio to run the full appeal path.

| | |
| --- | --- |
| Testnet contract (GenLayer Studio) | `0xF58f4d0d39c869BDa9ca109f20D899dbFB067DDd` |
| Explorer | https://explorer-studio.genlayer.com/address/0xF58f4d0d39c869BDa9ca109f20D899dbFB067DDd |

Buyer `0xBD767E1958928ff1CAaE1218dbCb4b2f2ada35F6`, seller `0x8EDC6794937Ed1DC6476f88A51f1cC52b4fC571C`, 5 GEN bond each, five jurors with a 1 GEN stake each.

| Step | Call | Result | Tx |
| --- | --- | --- | --- |
| Five jurors register (1 GEN each) | `register_juror` | SUCCESS x5 | `0x8de63fe1…358aa567`, `0x2513017c…79a3fa85`, `0xdd587acf…cc47e063`, `0xd90d04f4…39a81222`, `0xa8cd8e0f…77699a08` |
| Create trade_0 (BAYC #1, ERC-721) | `create_trade` | SUCCESS | `0xeb5138cb…d55dd9f2` |
| Seller accepts | `accept_trade` | SUCCESS | `0x283ed638…6edc42cd` |
| Both parties submit the instance link | `submit_proof` | SUCCESS x2, status `proof_frozen` | `0x100c2af1…5dbeba08`, `0xbabb4147…58c4929f` |
| Resolve after the deadline | `resolve_delivery` | SUCCESS, `UNDETERMINED`, status `ruled` | `0x0b9d4c2a…343d46a6` |
| Buyer appeals with a 1 GEN bond | `appeal` | SUCCESS, pool of 5 jurors frozen | `0xb06b3941…c055a7a4` |
| Draw after the appeal window, from a drand round that did not exist at appeal time | `draw_jury` | SUCCESS, 5 jurors selected | `0x00b9bd74…fe39beff` |
| Five commits | `commit_vote` | SUCCESS x5 | `0x67921a0f…9d9368b6`, `0xa65abdfb…6c173a69`, `0x173c769a…08023577`, `0xa7577371…ec538654`, `0xe80c4a85…e24c70de` |
| Five reveals (all `UNDETERMINED`) | `reveal_vote` | SUCCESS x5 | `0x83435c33…e4fc62cf`, `0xe466c21e…ed8af9a7`, `0x499ac838…c06e1701`, `0xe52693ea…293ffcc5`, `0xecab52de…d99ca0bd` |
| Finalize after the reveal window | `finalize_jury` | SUCCESS: `verification_passed` true, `final_outcome` `UNDETERMINED`, `payout_settled` true, status `finalized` | see explorer |

The jury majority matched an outcome the validators derived on their own from the frozen evidence, so it stood, nobody was slashed and the bonds were settled.

See `ARCHITECTURE.md` for the trust model and the limitations that are disclosed rather than hidden.

## License

MIT
