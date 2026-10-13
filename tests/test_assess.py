import json
import unittest

from _bootstrap import (m, BUYER, SELLER, HOP, NFT, OTHER_NFT, TX1, TX2, TX3, iso, transfer_item, tx_doc,
                        instance_doc, holders_doc)


def anchors(**over):
    a = {"buyer": BUYER, "seller": SELLER, "nft_contract": NFT, "token_id": "7", "token_standard": "ERC-721",
         "token_quantity": 1, "allow_resale": False, "created_at": iso(-3600), "deadline": iso(3600)}
    a.update(over)
    return a


def page(kind, doc, a=None):
    a = a or anchors()
    info = {"contract": NFT, "id": "7"}
    facts = m._extract_facts(kind, json.dumps(doc), a, info)
    return {"url": "u", "tx": facts, "content": json.dumps(facts, sort_keys=True)}


def transfers_page(*items, **kw):
    return page("transfers", {"items": list(items), "next_page_params": kw.get("more")})


def tx_page(tx=TX1, ok=True, ts=None):
    return page("tx", tx_doc(tx=tx, ok=ok, ts=ts))


def inst_page(owner=BUYER, token_id="7"):
    return page("instance", instance_doc(owner=owner, token_id=token_id))


def assess(pages, **over):
    return m._assess(pages, anchors(**over))


class Delivered(unittest.TestCase):
    def test_clean_delivery(self):
        r = assess([transfers_page(transfer_item()), tx_page(), inst_page()])
        self.assertEqual(r["ceiling"], "ASSET_DELIVERED")
        self.assertEqual(r["claim_keys"], [f"{TX1}:3"])

    def test_embedded_transfer_in_tx_page_is_enough(self):
        p = page("tx", tx_doc(transfers=[transfer_item()]))
        self.assertEqual(assess([p, inst_page()])["ceiling"], "ASSET_DELIVERED")

    def test_no_pages(self):
        self.assertEqual(assess([])["ceiling"], "UNDETERMINED")

    def test_unreadable_pages(self):
        self.assertEqual(assess([{"url": "u", "tx": None, "content": "x"}])["ceiling"], "UNDETERMINED")

    def test_token_id_string_and_int_equivalent(self):
        r = assess([transfers_page(transfer_item(token_id=7)), tx_page(), inst_page()])
        self.assertEqual(r["ceiling"], "ASSET_DELIVERED")

    def test_contract_is_matched_exactly(self):
        r = assess([transfers_page(transfer_item(token=OTHER_NFT)), tx_page(), inst_page()])
        self.assertNotEqual(r["ceiling"], "ASSET_DELIVERED")

    def test_wrong_seller_is_not_delivery(self):
        r = assess([transfers_page(transfer_item(frm=HOP)), tx_page(), inst_page()])
        self.assertEqual(r["ceiling"], "UNDETERMINED")

    def test_wrong_buyer_is_not_delivery(self):
        r = assess([transfers_page(transfer_item(to=HOP)), tx_page(), inst_page()])
        self.assertEqual(r["ceiling"], "UNDETERMINED")

    def test_standard_mismatch_is_undetermined(self):
        r = assess([transfers_page(transfer_item(std="ERC-1155", value="1")), tx_page(), inst_page()])
        self.assertEqual(r["ceiling"], "UNDETERMINED")

    def test_missing_standard_is_undetermined(self):
        item = transfer_item()
        item["token"] = {"address_hash": NFT}
        self.assertEqual(assess([transfers_page(item), tx_page(), inst_page()])["ceiling"], "UNDETERMINED")

    def test_failed_transaction_never_delivers(self):
        r = assess([transfers_page(transfer_item()), tx_page(ok=False), inst_page()])
        self.assertNotEqual(r["ceiling"], "ASSET_DELIVERED")

    def test_transfer_without_transaction_record_is_undetermined(self):
        r = assess([transfers_page(transfer_item()), inst_page()])
        self.assertEqual(r["ceiling"], "UNDETERMINED")
        self.assertIn("transaction record", r["reason"])

    def test_missing_log_index_is_undetermined(self):
        item = transfer_item()
        del item["log_index"]
        self.assertEqual(assess([transfers_page(item), tx_page(), inst_page()])["ceiling"], "UNDETERMINED")

    def test_conflicting_tx_pages_count_as_failed(self):
        r = assess([transfers_page(transfer_item()), tx_page(ok=True), page("tx", tx_doc(ok=False)), inst_page()])
        self.assertNotEqual(r["ceiling"], "ASSET_DELIVERED")

    def test_delivery_before_trade_creation_does_not_count(self):
        old = transfer_item(ts=iso(-7200))
        r = assess([transfers_page(old), tx_page(ts=iso(-7200)), inst_page()])
        self.assertNotEqual(r["ceiling"], "ASSET_DELIVERED")

    def test_delivery_on_both_window_edges_counts(self):
        a = anchors()
        edge = transfer_item(ts=a["created_at"])
        r = m._assess([page("transfers", {"items": [edge]}, a), page("tx", tx_doc(ts=a["created_at"]), a),
                       page("instance", instance_doc(), a)], a)
        self.assertEqual(r["ceiling"], "ASSET_DELIVERED")

    def test_two_deliveries_list_both_claim_keys(self):
        t2 = transfer_item(tx=TX2, li=9)
        r = assess([transfers_page(transfer_item(), t2), tx_page(), tx_page(tx=TX2), inst_page()])
        self.assertEqual(r["claim_keys"], sorted([f"{TX1}:3", f"{TX2}:9"]))

    def test_duplicate_listing_of_one_transfer_counts_once(self):
        r = assess([transfers_page(transfer_item()), page("tx", tx_doc(transfers=[transfer_item()])), inst_page()])
        self.assertEqual(r["claim_keys"], [f"{TX1}:3"])


class OwnerCheck(unittest.TestCase):
    def pages(self, owner_page):
        return [transfers_page(transfer_item()), tx_page()] + owner_page

    def test_missing_instance_link_is_undetermined(self):
        r = assess(self.pages([]))
        self.assertEqual(r["ceiling"], "UNDETERMINED")
        self.assertIn("owner", r["reason"])

    def test_owner_moved_on_is_undetermined(self):
        r = assess(self.pages([inst_page(owner=HOP)]))
        self.assertEqual(r["ceiling"], "UNDETERMINED")
        self.assertIn("no longer held", r["reason"])

    def test_owner_is_seller_again_is_undetermined(self):
        self.assertEqual(assess(self.pages([inst_page(owner=SELLER)]))["ceiling"], "UNDETERMINED")

    def test_owner_null_is_undetermined(self):
        self.assertEqual(assess(self.pages([inst_page(owner=None)]))["ceiling"], "UNDETERMINED")

    def test_allow_resale_skips_the_owner_check(self):
        r = assess(self.pages([inst_page(owner=HOP)]), allow_resale=True)
        self.assertEqual(r["ceiling"], "ASSET_DELIVERED")

    def test_allow_resale_does_not_need_an_instance_link(self):
        self.assertEqual(assess(self.pages([]), allow_resale=True)["ceiling"], "ASSET_DELIVERED")

    def test_conflicting_owner_pages_are_undetermined(self):
        r = assess(self.pages([inst_page(owner=BUYER), inst_page(owner=HOP)]))
        self.assertEqual(r["ceiling"], "UNDETERMINED")

    def test_instance_for_another_token_is_ignored(self):
        r = assess(self.pages([inst_page(owner=BUYER, token_id="8")]))
        self.assertEqual(r["ceiling"], "UNDETERMINED")

    def test_delivery_claim_keys_survive_an_undetermined_owner_result(self):
        r = assess(self.pages([inst_page(owner=HOP)]))
        self.assertEqual(r["claim_keys"], [f"{TX1}:3"])


class HopsAndWrapping(unittest.TestCase):
    def test_seller_to_burner_never_reaching_buyer(self):
        r = assess([transfers_page(transfer_item(to=HOP)), tx_page(), inst_page(owner=HOP)])
        self.assertEqual(r["ceiling"], "UNDETERMINED")
        self.assertIn("other than the buyer", r["reason"])

    def test_escrow_hop_that_reaches_the_buyer(self):
        hop_in = transfer_item(frm=HOP, to=BUYER, tx=TX2, li=1)
        hop_out = transfer_item(frm=SELLER, to=HOP)
        r = assess([transfers_page(hop_out, hop_in), tx_page(), tx_page(tx=TX2), inst_page(owner=BUYER)])
        self.assertEqual(r["ceiling"], "UNDETERMINED")

    def test_reaching_buyer_from_third_party_only(self):
        r = assess([transfers_page(transfer_item(frm=HOP)), tx_page(), inst_page()])
        self.assertEqual(r["ceiling"], "UNDETERMINED")

    def test_wrapped_token_same_id_other_contract(self):
        r = assess([transfers_page(transfer_item(token=OTHER_NFT)), tx_page(), inst_page(owner=BUYER)])
        self.assertEqual(r["ceiling"], "UNDETERMINED")
        self.assertIn("wrapped or proxied", r["reason"])

    def test_wrapped_with_other_id_other_contract_is_ignored(self):
        r = assess([transfers_page(transfer_item(token=OTHER_NFT, token_id="99")), tx_page(), inst_page(owner=SELLER)])
        self.assertEqual(r["ceiling"], "NOT_DELIVERED")

    def test_a_clean_delivery_wins_over_an_unrelated_earlier_hop(self):
        old = transfer_item(to=HOP, ts=iso(-100000), tx=TX3, li=1)
        r = assess([transfers_page(transfer_item(), old), tx_page(), tx_page(tx=TX3, ts=iso(-100000)), inst_page()])
        self.assertEqual(r["ceiling"], "ASSET_DELIVERED")

    def test_a_hop_before_trade_creation_is_ignored(self):
        old = transfer_item(to=HOP, ts=iso(-100000), tx=TX3, li=1)
        r = assess([transfers_page(old), tx_page(tx=TX3, ts=iso(-100000)), inst_page(owner=SELLER)])
        self.assertEqual(r["ceiling"], "NOT_DELIVERED")

    def test_hop_after_deadline_is_ignored(self):
        late = transfer_item(to=HOP, ts=iso(7200), tx=TX3, li=1)
        r = assess([transfers_page(late), tx_page(tx=TX3, ts=iso(7200)), inst_page(owner=SELLER)])
        self.assertEqual(r["ceiling"], "NOT_DELIVERED")

    def test_failed_hop_transaction_is_ignored(self):
        r = assess([transfers_page(transfer_item(to=HOP)), tx_page(ok=False), inst_page(owner=SELLER)])
        self.assertEqual(r["ceiling"], "NOT_DELIVERED")


class Partial(unittest.TestCase):
    def test_wrong_token_id(self):
        r = assess([transfers_page(transfer_item(token_id="8")), tx_page(), inst_page(token_id="8")])
        self.assertEqual(r["ceiling"], "PARTIALLY_DELIVERED")
        self.assertEqual(r["claim_keys"], [f"{TX1}:3"])

    def test_wrong_quantity_1155(self):
        a = dict(token_standard="ERC-1155", token_quantity=5)
        r = assess([transfers_page(transfer_item(std="ERC-1155", value="3")), tx_page()], **a)
        self.assertEqual(r["ceiling"], "PARTIALLY_DELIVERED")

    def test_partial_with_truncated_listing_is_undetermined(self):
        r = assess([transfers_page(transfer_item(token_id="8"), more={"p": 1}), tx_page()])
        self.assertEqual(r["ceiling"], "UNDETERMINED")

    def test_partial_needs_a_transaction_record(self):
        r = assess([transfers_page(transfer_item(token_id="8"))])
        self.assertEqual(r["ceiling"], "UNDETERMINED")

    def test_partial_outside_the_window_is_not_partial(self):
        r = assess([transfers_page(transfer_item(token_id="8", ts=iso(-9999))), tx_page(ts=iso(-9999))])
        self.assertNotEqual(r["ceiling"], "PARTIALLY_DELIVERED")


class Erc1155(unittest.TestCase):
    A = dict(token_standard="ERC-1155", token_quantity=5)

    def delivered(self, qty="5"):
        return [transfers_page(transfer_item(std="ERC-1155", value=qty)), tx_page()]

    def test_exact_quantity_with_holding(self):
        pages = self.delivered() + [page("holders", holders_doc([(BUYER, "5")]))]
        self.assertEqual(assess(pages, **self.A)["ceiling"], "ASSET_DELIVERED")

    def test_holding_less_than_quantity_is_undetermined(self):
        pages = self.delivered() + [page("holders", holders_doc([(BUYER, "2")]))]
        self.assertEqual(assess(pages, **self.A)["ceiling"], "UNDETERMINED")

    def test_missing_holders_link_is_undetermined(self):
        self.assertEqual(assess(self.delivered(), **self.A)["ceiling"], "UNDETERMINED")

    def test_resale_allowed_needs_no_holders(self):
        self.assertEqual(assess(self.delivered(), allow_resale=True, **self.A)["ceiling"], "ASSET_DELIVERED")

    def test_buyer_absent_from_holders_is_undetermined(self):
        pages = self.delivered() + [page("holders", holders_doc([(SELLER, "9")]))]
        self.assertEqual(assess(pages, **self.A)["ceiling"], "UNDETERMINED")

    def test_unreadable_1155_amount_is_undetermined_not_partial(self):
        item = transfer_item(std="ERC-1155")
        r = assess([transfers_page(item), tx_page()], **self.A)
        self.assertEqual(r["ceiling"], "UNDETERMINED")

    def test_not_delivered_when_seller_still_holds(self):
        pages = [page("holders", holders_doc([(SELLER, "5")]))]
        self.assertEqual(assess(pages, **self.A)["ceiling"], "NOT_DELIVERED")

    def test_truncated_holders_do_not_prove_non_delivery(self):
        pages = [page("holders", holders_doc([(SELLER, "5")], more=True))]
        self.assertEqual(assess(pages, **self.A)["ceiling"], "UNDETERMINED")


class NotDelivered(unittest.TestCase):
    def test_seller_still_owns_721(self):
        r = assess([inst_page(owner=SELLER)])
        self.assertEqual(r["ceiling"], "NOT_DELIVERED")
        self.assertEqual(r["claim_keys"], [])

    def test_late_delivery(self):
        late = transfer_item(ts=iso(7200))
        r = assess([transfers_page(late), tx_page(ts=iso(7200)), inst_page()])
        self.assertEqual(r["ceiling"], "NOT_DELIVERED")
        self.assertIn("after the deadline", r["reason"])

    def test_a_third_party_owner_is_not_affirmative(self):
        self.assertEqual(assess([inst_page(owner=HOP)])["ceiling"], "UNDETERMINED")

    def test_nothing_shown_is_not_non_delivery(self):
        self.assertEqual(assess([transfers_page(), tx_page()])["ceiling"], "UNDETERMINED")

    def test_truncated_listing_blocks_non_delivery(self):
        r = assess([transfers_page(more={"p": 1}), inst_page(owner=SELLER)])
        self.assertEqual(r["ceiling"], "UNDETERMINED")


class Gate(unittest.TestCase):
    def test_delivered_needs_the_ceiling(self):
        self.assertEqual(m._gate_outcome("ASSET_DELIVERED", "UNDETERMINED", False), "UNDETERMINED")
        self.assertEqual(m._gate_outcome("ASSET_DELIVERED", "PARTIALLY_DELIVERED", False), "UNDETERMINED")
        self.assertEqual(m._gate_outcome("ASSET_DELIVERED", "NOT_DELIVERED", False), "UNDETERMINED")
        self.assertEqual(m._gate_outcome("ASSET_DELIVERED", "ASSET_DELIVERED", False), "ASSET_DELIVERED")

    def test_model_cannot_go_below_into_a_different_verdict(self):
        self.assertEqual(m._gate_outcome("NOT_DELIVERED", "ASSET_DELIVERED", False), "UNDETERMINED")
        self.assertEqual(m._gate_outcome("PARTIALLY_DELIVERED", "NOT_DELIVERED", False), "UNDETERMINED")

    def test_model_may_veto(self):
        self.assertEqual(m._gate_outcome("UNDETERMINED", "ASSET_DELIVERED", False), "UNDETERMINED")

    def test_premature_non_delivery_is_blocked(self):
        self.assertEqual(m._gate_outcome("NOT_DELIVERED", "NOT_DELIVERED", True), "UNDETERMINED")
        self.assertEqual(m._gate_outcome("PARTIALLY_DELIVERED", "PARTIALLY_DELIVERED", True), "UNDETERMINED")
        self.assertEqual(m._gate_outcome("ASSET_DELIVERED", "ASSET_DELIVERED", True), "ASSET_DELIVERED")

    def test_invalid_outcomes(self):
        for bad in (None, "", "TRADE_COMPLETED", 5, ["ASSET_DELIVERED"]):
            self.assertEqual(m._gate_outcome(bad, "ASSET_DELIVERED", False), "UNDETERMINED")


if __name__ == "__main__":
    unittest.main()
