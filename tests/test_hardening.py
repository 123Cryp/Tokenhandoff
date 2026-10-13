"""Regression tests for the second review pass: rendered-JSON wrappers,
ERC-1155 batch and split deliveries, strict proof links and hosts,
consensus-safe freezing of unreadable pages and the drand beacon format."""
import json
import unittest
from unittest.mock import patch

from _bootstrap import (m, gl, make_contract, set_caller, call_payable, iso, open_trade, frozen_trade, get,
                        pass_deadline, serve, llm, tx_url, instance_url, transfer_item, tx_doc, instance_doc,
                        holders_doc, delivered_urls, new_trade, BUYER, SELLER, STRANGER, HOP, NFT, TX1, TX2, TX3)

UserError = gl.vm.UserError


def anchors(**over):
    a = {"buyer": BUYER, "seller": SELLER, "nft_contract": NFT, "token_id": "7", "token_standard": "ERC-721",
         "token_quantity": 1, "allow_resale": False, "created_at": iso(-3600), "deadline": iso(3600)}
    a.update(over)
    return a


def page(kind, doc, a=None, raw=None):
    a = a or anchors()
    facts = m._extract_facts(kind, raw if raw is not None else json.dumps(doc), a, {"contract": NFT, "id": "7"})
    return {"url": "u", "tx": facts, "content": json.dumps(facts, sort_keys=True)}


def batch_item(entries, frm=SELLER, to=BUYER, tx=TX1, li=4):
    return {"transaction_hash": tx, "log_index": li, "timestamp": iso(-60), "from": {"hash": frm},
            "to": {"hash": to}, "token": {"address_hash": NFT, "type": "ERC-1155"},
            "total": [{"token_id": i, "value": v} for i, v in entries]}


A1155 = dict(token_standard="ERC-1155", token_quantity=5)


class RenderedJson(unittest.TestCase):
    def test_browser_chrome_around_the_json_is_ignored(self):
        raw = "Pretty-print\n" + json.dumps(tx_doc(transfers=[transfer_item()]))
        facts = m._extract_facts("tx", raw, anchors(), {"contract": "", "id": ""})
        self.assertIsNotNone(facts)
        self.assertEqual(facts["hash"], TX1)

    def test_text_without_json_is_unreadable(self):
        self.assertIsNone(m._json_from_text("Just a moment... checking your browser"))
        self.assertIsNone(m._json_from_text("} nothing {"))
        self.assertIsNone(m._extract_facts("tx", "<html>{oops</html>", anchors(), {"contract": "", "id": ""}))

    def test_a_json_array_is_not_a_document(self):
        self.assertIsNone(m._extract_facts("tx", "[1, 2]", anchors(), {"contract": "", "id": ""}))


class Erc1155Batches(unittest.TestCase):
    def test_batch_transfer_with_the_anchored_id_delivers(self):
        pages = [page("transfers", {"items": [batch_item([("3", "9"), ("7", "5")])]}, anchors(**A1155)),
                 page("tx", tx_doc()), page("holders", holders_doc([(BUYER, "5")]))]
        r = m._assess(pages, anchors(**A1155))
        self.assertEqual(r["ceiling"], "ASSET_DELIVERED")
        self.assertEqual(r["claim_keys"], [f"{TX1}:4"])

    def test_batch_without_the_anchored_id_is_partial(self):
        pages = [page("transfers", {"items": [batch_item([("3", "5")])]}, anchors(**A1155)), page("tx", tx_doc())]
        self.assertEqual(m._assess(pages, anchors(**A1155))["ceiling"], "PARTIALLY_DELIVERED")

    def test_split_delivery_that_adds_up_is_a_delivery(self):
        items = [transfer_item(std="ERC-1155", value="2", tx=TX1, li=1),
                 transfer_item(std="ERC-1155", value="3", tx=TX2, li=6)]
        pages = [page("transfers", {"items": items}, anchors(**A1155)), page("tx", tx_doc(tx=TX1)),
                 page("tx", tx_doc(tx=TX2)), page("holders", holders_doc([(BUYER, "5")]))]
        r = m._assess(pages, anchors(**A1155))
        self.assertEqual(r["ceiling"], "ASSET_DELIVERED")
        self.assertEqual(r["claim_keys"], sorted([f"{TX1}:1", f"{TX2}:6"]))

    def test_split_delivery_short_of_the_quantity_is_partial(self):
        items = [transfer_item(std="ERC-1155", value="2", tx=TX1, li=1),
                 transfer_item(std="ERC-1155", value="2", tx=TX2, li=6)]
        pages = [page("transfers", {"items": items}, anchors(**A1155)), page("tx", tx_doc(tx=TX1)),
                 page("tx", tx_doc(tx=TX2))]
        r = m._assess(pages, anchors(**A1155))
        self.assertEqual(r["ceiling"], "PARTIALLY_DELIVERED")
        self.assertEqual(len(r["claim_keys"]), 2)

    def test_late_remainder_is_not_counted_as_delivered(self):
        items = [transfer_item(std="ERC-1155", value="2", tx=TX1, li=1),
                 transfer_item(std="ERC-1155", value="3", tx=TX2, li=6, ts=iso(7200))]
        pages = [page("transfers", {"items": items}, anchors(**A1155)), page("tx", tx_doc(tx=TX1)),
                 page("tx", tx_doc(tx=TX2, ts=iso(7200))), page("holders", holders_doc([(BUYER, "5")]))]
        self.assertEqual(m._assess(pages, anchors(**A1155))["ceiling"], "PARTIALLY_DELIVERED")

    def test_late_quantity_alone_is_not_delivered(self):
        items = [transfer_item(std="ERC-1155", value="5", tx=TX2, li=6, ts=iso(7200))]
        pages = [page("transfers", {"items": items}, anchors(**A1155)), page("tx", tx_doc(tx=TX2, ts=iso(7200)))]
        self.assertEqual(m._assess(pages, anchors(**A1155))["ceiling"], "NOT_DELIVERED")

    def test_conflicting_holders_links_are_undetermined(self):
        pages = [page("transfers", {"items": [transfer_item(std="ERC-1155", value="5")]}, anchors(**A1155)),
                 page("tx", tx_doc()), page("holders", holders_doc([(BUYER, "5")])),
                 page("holders", holders_doc([(BUYER, "0")]))]
        r = m._assess(pages, anchors(**A1155))
        self.assertEqual(r["ceiling"], "UNDETERMINED")
        self.assertIn("disagree", r["reason"])


class UnreadableFields(unittest.TestCase):
    def test_unreadable_token_id_is_not_a_partial_delivery(self):
        item = transfer_item()
        item["total"] = {"token_id": None}
        pages = [page("transfers", {"items": [item]}), page("tx", tx_doc()), page("instance", instance_doc())]
        r = m._assess(pages, anchors())
        self.assertEqual(r["ceiling"], "UNDETERMINED")
        self.assertIn("could not be read", r["reason"])

    def test_unconfirmed_history_from_before_the_trade_is_ignored(self):
        old = transfer_item(tx=TX3, li=1, ts=iso(-90000))
        pages = [page("transfers", {"items": [old]}), page("instance", instance_doc(owner=SELLER))]
        self.assertEqual(m._assess(pages, anchors())["ceiling"], "NOT_DELIVERED")


class ProofLinks(unittest.TestCase):
    def test_paged_or_filtered_listings_are_rejected_at_submission(self):
        c = make_contract()
        tid = open_trade(c)
        set_caller(BUYER)
        for url in (tx_url(suffix="/token-transfers") + "?block_number=1&index=2",
                    instance_url(suffix="/transfers") + "?type=ERC-721", tx_url() + "#owner"):
            with self.assertRaises(UserError):
                c.submit_proof(tid, [url])


class Hosts(unittest.TestCase):
    def test_malformed_hosts_and_bare_suffixes_are_rejected(self):
        for bad in ("co.uk", "bad_host.com", "a..com", "-a.com", "1.2.3.4", "exa mple.com", "*.blockscout.com"):
            c = make_contract()
            with self.assertRaises(UserError, msg=bad):
                new_trade(c, hosts=bad)

    def test_min_sources_cannot_exceed_the_domains_the_hosts_span(self):
        c = make_contract()
        with self.assertRaises(UserError):
            new_trade(c, min_sources=2, hosts="eth.blockscout.com,base.blockscout.com")
        self.assertTrue(new_trade(c, min_sources=2, hosts="eth.blockscout.com,explorer.example.org"))


class FreezingUnreadablePages(unittest.TestCase):
    def test_an_unreadable_page_freezes_empty_so_volatile_errors_cannot_split_consensus(self):
        docs = delivered_urls()
        a = anchors(created_at=iso(-3600))
        with patch.object(gl.nondet.web, "render", return_value="<html>error ray id 8f1c</html>"):
            one = m._gather_pages([tx_url()], a)
        with patch.object(gl.nondet.web, "render", return_value="<html>error ray id 77ab</html>"):
            two = m._gather_pages([tx_url()], a)
        self.assertEqual(one[0]["content"], "")
        self.assertEqual(m._manifest(one), m._manifest(two))
        self.assertNotEqual(one[0]["full_content_sha256"], two[0]["full_content_sha256"])
        self.assertTrue(docs)


class SplitDeliveryClaims(unittest.TestCase):
    def docs(self):
        items = [transfer_item(std="ERC-1155", value="2", tx=TX1, li=1),
                 transfer_item(std="ERC-1155", value="3", tx=TX2, li=6)]
        return {
            tx_url(suffix="/token-transfers"): {"items": items, "next_page_params": None},
            tx_url(): tx_doc(tx=TX1),
            tx_url(TX2): tx_doc(tx=TX2),
            instance_url(suffix="/holders"): holders_doc([(BUYER, "5")]),
        }

    def test_every_transfer_of_a_split_delivery_is_claimed(self):
        docs = self.docs()
        c = make_contract()
        tid = frozen_trade(c, list(docs.keys()), standard="ERC-1155", quantity=5)
        set_caller(STRANGER)
        with serve(docs), llm("ASSET_DELIVERED"):
            t = json.loads(c.resolve_delivery(tid))
        self.assertEqual(t["tier1_outcome"], "ASSET_DELIVERED")
        self.assertEqual(c.get_transfer_claim(f"{TX1}:1"), tid)
        self.assertEqual(c.get_transfer_claim(f"{TX2}:6"), tid)

    def test_a_split_delivery_cannot_reuse_a_transfer_claimed_elsewhere(self):
        docs = self.docs()
        c = make_contract()
        c.transfer_claims[f"{TX2}:6"] = "trade_99"
        tid = frozen_trade(c, list(docs.keys()), standard="ERC-1155", quantity=5)
        pass_deadline(c, tid)
        set_caller(STRANGER)
        with serve(docs), llm("ASSET_DELIVERED"):
            t = json.loads(c.resolve_delivery(tid))
        self.assertEqual(t["tier1_outcome"], "UNDETERMINED")
        self.assertEqual(t["tier1_ceiling"], "ASSET_DELIVERED")
        self.assertIn("already used", t["tier1_reasoning"])
        self.assertEqual(c.get_transfer_claim(f"{TX1}:1"), "")


class Deadlines(unittest.TestCase):
    def test_z_suffix_and_offsets_are_accepted(self):
        import datetime as dt
        c = make_contract()
        z = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        set_caller(BUYER)
        tid = call_payable(c, "create_trade", 1000, SELLER, "t", "c", NFT, "7", "ERC-721", 1, "eth.blockscout.com", z)
        self.assertTrue(get(c, tid)["deadline"].endswith("+00:00"))

    def test_garbage_deadline_is_a_clean_user_error(self):
        c = make_contract()
        set_caller(BUYER)
        with self.assertRaises(UserError):
            call_payable(c, "create_trade", 1000, SELLER, "t", "c", NFT, "7", "ERC-721", 1, "eth.blockscout.com", "tomorrow")


class Beacon(unittest.TestCase):
    def fetch(self, body, round_no=5):
        c = make_contract()
        with patch.object(gl.nondet.web, "render", return_value=body):
            return c._fetch_randomness_beacon(round_no)

    def test_wrapped_json_is_read(self):
        self.assertEqual(self.fetch("Pretty-print " + json.dumps({"round": 5, "randomness": "AB" * 32})), "ab" * 32)

    def test_wrong_round_missing_round_or_malformed_randomness_fail(self):
        for body in (json.dumps({"round": 6, "randomness": "ab" * 32}), json.dumps({"randomness": "ab" * 32}),
                     json.dumps({"round": 5, "randomness": "zz"}), "not json"):
            with self.assertRaises(UserError):
                self.fetch(body)


if __name__ == "__main__":
    unittest.main()
