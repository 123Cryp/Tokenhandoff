# TokenHandoff — Architecture

## 1. Relationship to TradeAnchor

TokenHandoff keeps TradeAnchor's adjudication core unchanged in behaviour because that core is already hardened: a frozen evidence snapshot, deterministic validator consensus compared in code, a staked commit-reveal jury drawn from a future drand round, an independent Tier-2 check of the jury's majority, pull-payment withdrawals, permissionless recovery paths and one-use proof claims.

What changes is the domain layer. TradeAnchor decides whether a fungible payment arrived at an address. TokenHandoff decides whether one specific non-fungible token was delivered, and whether it is still held by the buyer. Names are changed to match the domain (`submit_proof`, `freeze_proof`, `resolve_delivery`, `finalize_ruling`, `expire_no_proof`, `expire_stalled_ruling`; statuses `accepted`, `proof_frozen`, `ruled`, `jury_voting`; outcomes `ASSET_DELIVERED`, `PARTIALLY_DELIVERED`, `NOT_DELIVERED`, `UNDETERMINED`).

## 2. The domain layer

### Anchors

`create_trade` records, before any dispute: NFT contract, token id (normalised to a decimal string), token standard, quantity (1 for ERC-721), buyer (`party_a`, the caller), seller (`party_b`), deadline, allowed Blockscout hosts, `allow_resale` and `min_evidence_sources`. Both parties lock the same bond. The sale price is off-chain; the bonds make each side accountable for its claim.

### Evidence links

`submit_proof` accepts only Blockscout v2 API links (`_proof_info`): a transaction, a transaction's token-transfers, a token instance, an instance's transfers, or (ERC-1155 only) an instance's holders. A link that names a different contract or token id than the trade is rejected at submission, as are links with a query string or fragment (a query could page or filter a listing so that an adverse transfer is out of view), non-https links, links with embedded credentials and hosts that are not an allowed host or a subdomain of one (`notblockscout.com` and `blockscout.com.evil.example` do not match `blockscout.com`).

Allowed hosts are validated as hostnames when the trade is created: lowercase letter, digit and hyphen labels, at least two labels, no IP addresses and no bare public suffix such as `co.uk`. `min_evidence_sources` may not exceed the number of distinct registrable domains the allowed hosts span, so a trade cannot be created that could never freeze its proof. The hosts are also what identifies the chain: the contract address and token id are matched exactly, but the same address can exist on several chains, so the buyer should list explorers of the NFT's own chain only.

### Structured facts

Each fetched document is parsed as JSON; when a rendered page carries text around the document (for example a browser's "Pretty-print" toggle) the span from the first `{` to the last `}` is parsed instead. It is then reduced to canonical, bounded facts (`_extract_facts`): addresses, token ids and amounts as normalised strings, token type, success flag, timestamp and log index. Only transfers that involve the buyer or the seller are kept, ranked so that a direct seller-to-buyer transfer of the anchored contract is kept first, capped at 16 per page. If a relevant transfer had to be dropped, or the API reports another page, the page is marked `truncated`. Token ids are parsed integer-safe: `7`, `"7"` and `"0x7"` are the same id, and ids up to 2^256 - 1 are exact. An ERC-1155 batch transfer, which Blockscout reports with a list of `total` entries, becomes one record per entry, all sharing the transfer's transaction hash and log index. Volatile fields such as `confirmations` are never kept, so they cannot split validator consensus. A page that does not parse into facts is frozen as empty content: raw page text is never judged, and a volatile error page (a challenge page with a ray id, say) would otherwise make validators disagree.

### The assessment

`_assess` is a pure function of the frozen facts. It returns a **ceiling**, the strongest outcome the evidence supports, plus a reason and the claim keys:

| Ceiling | Conditions |
| --- | --- |
| `ASSET_DELIVERED` | a transfer of exactly the anchored contract, token id and standard (for ERC-1155: transfers whose amounts add up to exactly the anchored quantity), from the seller to the buyer, in a transaction record that shows success, with a timestamp and log index, inside `[created_at, deadline]`; and unless `allow_resale`, the buyer is the current owner (ERC-721 instance link) or holds at least the quantity (ERC-1155 holders link) |
| `PARTIALLY_DELIVERED` | a readable transfer from the seller to the buyer on the right contract but with a different token id, or (ERC-1155) in-window amounts that add up to a different quantity; no anomaly and no truncation |
| `NOT_DELIVERED` | the matching delivery (for ERC-1155 the full quantity) happened only after the deadline, or the seller is shown still holding the token (ERC-721 owner, or ERC-1155 holders on a complete page) |
| `UNDETERMINED` | everything else: a hop (token left the seller for another address, or reached the buyer from another address), a wrapped or proxied token (same token id delivered through a different contract), a token type that differs from the anchored standard, a matching transfer without a confirmed transaction record, timestamp or log index, a seller-to-buyer transfer of the anchored contract whose token id or amount cannot be read, a truncated listing, a missing or conflicting owner or holders link, a buyer who no longer holds the token, or no affirmative evidence either way |

Transfers dated before the trade was created are ignored entirely, so old history of the token returned by an instance-transfers link (with or without a transaction record) does not poison a trade; hops and wrapped transfers are only flagged inside `[created_at, deadline]`.

### The backstop

The model's verdict goes through `_gate_outcome`: it stands only if it equals the ceiling or is `UNDETERMINED`; before the deadline only `ASSET_DELIVERED` can stand. So the model can veto (for example when the free-text delivery criteria add a condition the findings do not show) but can never upgrade or choose a different verdict than the code supports. After validator consensus, `resolve_delivery` recomputes the assessment from the frozen pages it is about to store and applies the gate again, so a leader result that disagrees with its own stored pages is downgraded. Tier 2 applies the same ceiling to the frozen facts. The model never receives raw page text, only the canonical facts, so there is no free text to inject into.

### One transfer, one trade

A delivery or partial delivery is claimed by `<tx hash>:<log index>` in `transfer_claims`. For ERC-721 the first delivering transfer not claimed elsewhere is recorded; for ERC-1155 every transfer that makes up the delivered quantity must be unclaimed and all of them are recorded. A second trade that depends on a claimed transfer resolves `UNDETERMINED`, at Tier 1 and again when a jury majority is verified. A claim stays even if a jury later overturns the trade that made it.

## 3. Evidence and consensus

- Validators re-run the fetch, the parsing and the model call inside a custom `gl.vm.run_nondet_unsafe` equivalence (no model comparator). The leader and validator bodies are module-level functions that capture no contract state. Exceptions are caught inside them and returned as plain `{"error": ...}` values; two failed runs agree with each other and the call reverts without recording anything, so a transient model or network failure cannot settle a trade.
- Consensus-bound fields: `outcome`, `ceiling`, `reason`, `claim_keys`, which pages were readable, and the evidence manifest `[url, excerpt_sha256, facts]` per page. The leader's stored `frozen` pages must be exactly the pages its manifest describes. Only the model's free-text reasoning may differ.
- Integrity-bound only: `evidence_content_root` also commits to `full_content_sha256`, the hash of each whole fetched document. It is for audit and is not a consensus field, because live API documents carry values that change between fetches.
- Tier 2 first re-hashes the stored content against the root, then judges that stored content and never re-fetches, so a host changing after Tier 1 cannot change a jury verdict.
- Evidence sources are counted by registrable domain (`_registrable`, last two labels, or three under a small list of well-known two-part suffixes), so `eth.blockscout.com` and `base-sepolia.blockscout.com` count as one source for `min_evidence_sources`. Both hosts can still be allowed for submission.

## 4. Hardening notes

- **Caller authorization on every sensitive write.** `accept_trade` is seller-only, `cancel_trade` buyer-only, `submit_proof` and `appeal` party-only, juror functions act on the caller's own record, `commit_vote` and `reveal_vote` need a selected juror, `withdraw` pays only the caller's own balance. The permissionless methods (`freeze_proof`, `resolve_delivery`, `finalize_ruling`, `expire_*`, `draw_jury`, `finalize_jury`) only move a trade forward along rules fixed by stored state.
- **A recovery path for every stuck state.** `created`: the buyer cancels. `accepted`: `expire_no_proof` freezes proof that can be frozen, otherwise refunds both bonds. `proof_frozen`: `expire_stalled_ruling` refunds both bonds after 7 days. `ruled`: `finalize_ruling`. `appeal_filed` and `jury_voting`: `expire_stuck_appeal` keeps the Tier-1 outcome, refunds the bond and unlocks jurors.
- **Premature resolution.** Before the deadline only a proven delivery can be recorded; anything else reverts and the trade stays `proof_frozen`, so the side that has not performed cannot void the trade early.
- **Two-sided evidence.** The first submission does not freeze anything; the counterparty gets a grace period. One bad link rejects the whole submission.
- **Bounded inputs.** Text (2000 characters), URL length (300), links per party (5), hosts (10), quantity (10^30), token id (uint256), facts per page (16), content per page (12000 characters), list page size (25).
- **Time.** Every deadline check goes through `_now_utc()`, which reads `datetime.now()`. Inside GenVM this clock is pinned to the transaction timestamp and identical for every validator. It measures transaction time, not real-world time.
- **Pull payments.** Payouts only credit `pending_withdrawals`; `withdraw()` zeroes the ledger before the transfer, and an EOA transfer takes only `value=`.
- **Storage.** Storage types are class-level annotations; `__init__` assigns no containers.

Settlement (each side stakes the same amount; `pot` is both stakes)

- `ASSET_DELIVERED`: the seller receives the pot.
- `NOT_DELIVERED`: the buyer receives the pot.
- `PARTIALLY_DELIVERED`: the pot is split evenly; it is two equal stakes, so each side gets back exactly its own stake.
- `UNDETERMINED`: each side gets its own stake back.

## 5. Jury

- Commit hashes are `sha256("<vote>:<salt>:<juror address, lowercase>:<trade_id>")`, 64 hex characters, so a copied commit can never be revealed by the copier. Salts are capped at 256 characters.
- The juror pool is fixed as of trade acceptance (no late registration or stake top-ups), frozen when the appeal is filed (at most the 100 heaviest eligible jurors), and held so those jurors cannot unstake until the draw. Parties are excluded.
- The jury is drawn by `draw_jury` after the appeal window, from the drand round that follows the end of the window. That round did not exist when the appeal was filed. Selection uses integer arithmetic only and is replayable with `verify_jury_selection`.
- After reveal, a bare majority is not trusted. Validators derive their own outcome from the frozen evidence, without seeing the Tier-1 reasoning or the jury's candidate. The majority stands only if that outcome equals it; otherwise the Tier-1 outcome holds, the bond is refunded and nobody is slashed for disagreeing. Non-revealers are always slashed and the slash is redistributed, never stranded. If the verification itself cannot run, `finalize_jury` reverts and slashes nobody.
- Consequence of the backstop: the jury can confirm an outcome only if it equals the deterministic ceiling or is `UNDETERMINED`. It cannot rule against the structured facts.

## 6. Known limitations (disclosed, not hidden)

Scope
- The contract does not move the NFT or the payment and does not verify the off-chain price. It adjudicates delivery and settles the two bonds.
- Only the Blockscout v2 JSON shapes listed above are parsed. Other explorers and HTML pages are rejected at submission.

Evidence
- Trust is placed in the Blockscout instance operator and its indexer. A wrong, reorganised or lagging index is not detected, and confirmations are not compared because the counter is volatile.
- Listings are not paginated. A truncated page cannot prove non-delivery or partial delivery and resolves `UNDETERMINED` unless a clean delivery is already in view.
- ERC-1155 has no single owner. The holder check reads only the buyer's and seller's entries on the holders page the party submitted; if the page is truncated or the buyer is absent from it, the result is `UNDETERMINED`.
- The current owner is read at resolution time and frozen with the evidence. It is not re-checked afterwards, so a buyer who sells on right after resolution is not penalised. If the owner changes between two validators' fetches, they disagree and the call can be retried.
- Hop and wrapper detection only sees transfers that appear in the submitted links and involve the buyer or the seller. A hop through addresses that never appear in the evidence cannot be detected, which is why a delivery without an owner link resolves `UNDETERMINED` unless resale is allowed.
- With `allow_resale=true` the owner is not checked at all; that is the buyer's explicit choice.
- With `allow_resale=false` the buyer can move the token away before resolution and so turn a real delivery into `UNDETERMINED` (each bond returned). The buyer gains nothing from it beyond avoiding the loss of their own bond, and the seller is not penalised; it is the price of catching clawbacks and hops through the owner check.
- The chain is identified only by the allowed hosts. If the buyer allows explorers of two chains, a token at the same contract address on the other chain would be accepted.
- Either party can add a link that shows a suspicious transfer inside the window, which turns the result into `UNDETERMINED` (both bonds returned). This is conservative by design.
- The registrable-domain heuristic uses a short built-in list of two-part suffixes. An uncommon suffix is treated as `example.<tld>`.

Model
- The model is a veto, not an oracle. Free-text delivery criteria can only lower the result to `UNDETERMINED`; they can never raise it or choose a different verdict.
- Live behaviour of the model call under real validator consensus must be verified on GenLayer Studio. The offline suite stubs the model.

Jury and economics (shared with TradeAnchor)
- Building the eligible pool reads every juror record, so a very large registry makes `appeal` more expensive; the snapshot is capped at the 100 heaviest jurors.
- `MIN_JUROR_STAKE` is small and refundable when no case is open, so a funded attacker could register several addresses. Weights are capped and parties are excluded, but this is not a full Sybil defence.
- The randomness beacon comes from the public drand endpoint through the validators' web fetch, not an on-chain VRF. The response must name the expected round and carry a 64-hex randomness value.
- A claimed transfer stays claimed even if a jury later overturns the trade that claimed it.
- The Studio runtime keeps GEN attached to a payable call that reverts. The frontend always sends the exact required amount, but a manual call with a wrong value can strand that GEN.
- All deadline windows are measured in transaction time.
