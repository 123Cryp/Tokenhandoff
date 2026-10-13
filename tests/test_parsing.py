import json
import unittest

from _bootstrap import (m, BUYER, SELLER, NFT, HOST, TX1, instance_url, tx_url, transfer_item, tx_doc,
                        instance_doc, holders_doc)

ANCHORS = {"buyer": BUYER, "seller": SELLER, "nft_contract": NFT, "token_id": "7"}


class NormUint(unittest.TestCase):
    def test_decimal_string_int_and_hex_agree(self):
        self.assertEqual(m._norm_uint("7"), "7")
        self.assertEqual(m._norm_uint(7), "7")
        self.assertEqual(m._norm_uint("0x7"), "7")
        self.assertEqual(m._norm_uint(" 0007 "), "7")

    def test_large_ids_are_exact(self):
        big = 2 ** 255 + 12345
        self.assertEqual(m._norm_uint(str(big)), str(big))
        self.assertEqual(m._norm_uint(big), str(big))
        self.assertEqual(m._norm_uint(hex(big)), str(big))

    def test_rejects_bad_values(self):
        for bad in (None, True, False, -1, "-1", "1.5", 1.5, "abc", "", "0x", "١٢٣", 2 ** 256, str(2 ** 256), [], {}):
            self.assertEqual(m._norm_uint(bad), "", repr(bad))

    def test_integral_float_only_when_safe(self):
        self.assertEqual(m._norm_uint(5.0), "5")
        self.assertEqual(m._norm_uint(1e30), "")

    def test_zero_is_valid(self):
        self.assertEqual(m._norm_uint(0), "0")
        self.assertEqual(m._norm_uint("0"), "0")


class Standards(unittest.TestCase):
    def test_variants(self):
        for raw in ("ERC-721", "erc721", "ERC_721", " erc-721 "):
            self.assertEqual(m._norm_standard(raw), "ERC-721")
        for raw in ("ERC-1155", "erc1155"):
            self.assertEqual(m._norm_standard(raw), "ERC-1155")

    def test_unknown(self):
        for raw in ("ERC-20", "", None, "721"):
            self.assertEqual(m._norm_standard(raw), "")


class Addresses(unittest.TestCase):
    def test_string_and_object(self):
        self.assertEqual(m._addr_of(NFT.upper().replace("0X", "0x")), NFT)
        self.assertEqual(m._addr_of({"hash": NFT}), NFT)
        self.assertEqual(m._addr_of({"address": {"hash": NFT}}), NFT)

    def test_invalid(self):
        for bad in (None, "", "0x12", "zz" * 21, {"hash": None}, 5, "0x" + "g" * 40):
            self.assertEqual(m._addr_of(bad), "")


class Timestamps(unittest.TestCase):
    def test_iso_z(self):
        self.assertEqual(m._norm_ts("2030-01-01T00:00:00Z"), "2030-01-01T00:00:00+00:00")

    def test_epoch_seconds_and_millis(self):
        self.assertEqual(m._norm_ts(1893456000), m._norm_ts(1893456000000))

    def test_bad(self):
        for bad in ("", "nope", None, True):
            self.assertEqual(m._norm_ts(bad), "")


class Registrable(unittest.TestCase):
    def test_domains(self):
        self.assertEqual(m._registrable("eth.blockscout.com"), "blockscout.com")
        self.assertEqual(m._registrable("base-sepolia.blockscout.com"), "blockscout.com")
        self.assertEqual(m._registrable("example.co.uk"), "example.co.uk")
        self.assertEqual(m._registrable("a.b.example.co.uk"), "example.co.uk")
        self.assertEqual(m._registrable("localhost"), "localhost")


class ProofInfo(unittest.TestCase):
    def kind(self, url):
        info = m._proof_info(url)
        return info["kind"] if info else None

    def test_recognised_endpoints(self):
        self.assertEqual(self.kind(tx_url()), "tx")
        self.assertEqual(self.kind(tx_url(suffix="/token-transfers")), "transfers")
        self.assertEqual(self.kind(instance_url()), "instance")
        self.assertEqual(self.kind(instance_url(suffix="/transfers")), "transfers")
        self.assertEqual(self.kind(instance_url(suffix="/holders")), "holders")

    def test_instance_info_carries_contract_and_id(self):
        info = m._proof_info(instance_url(token_id="0x7"))
        self.assertEqual((info["contract"], info["id"]), (NFT, "7"))

    def test_rejected_shapes(self):
        for url in (f"https://{HOST}/", f"https://{HOST}/api/v2/transactions/0x12",
                    f"https://{HOST}/api/v2/transactions/{TX1}/logs",
                    f"https://{HOST}/api/v2/tokens/{NFT}/instances",
                    f"https://{HOST}/api/v2/tokens/{NFT}/instances/abc",
                    f"https://{HOST}/api/v2/tokens/0x12/instances/7",
                    f"https://{HOST}/api/v1/transactions/{TX1}",
                    f"https://{HOST}/tx/{TX1}", "not a url", ""):
            self.assertIsNone(m._proof_info(url), url)

    def test_query_string_and_fragment_rejected(self):
        # A query could filter or page a listing to hide adverse transfers.
        self.assertIsNone(self.kind(tx_url(suffix="/token-transfers") + "?block_number=1&index=2"))
        self.assertIsNone(self.kind(tx_url(suffix="/token-transfers") + "?type=ERC-20"))
        self.assertIsNone(self.kind(tx_url() + "#x"))


class TransferFacts(unittest.TestCase):
    def facts(self, doc, kind="transfers"):
        return m._extract_facts(kind, json.dumps(doc), ANCHORS, {"contract": NFT, "id": "7"})

    def test_basic_transfer_fields(self):
        f = self.facts({"items": [transfer_item()]})
        tr = f["transfers"][0]
        self.assertEqual((tr["from"], tr["to"], tr["token"], tr["type"], tr["id"], tr["li"]),
                         (SELLER, BUYER, NFT, "ERC-721", "7", "3"))
        self.assertFalse(f["truncated"])

    def test_log_index_zero_is_kept(self):
        f = self.facts({"items": [transfer_item(li=0)]})
        self.assertEqual(f["transfers"][0]["li"], "0")

    def test_int_and_string_token_ids_match(self):
        a = self.facts({"items": [transfer_item(token_id=7)]})["transfers"][0]["id"]
        b = self.facts({"items": [transfer_item(token_id="7")]})["transfers"][0]["id"]
        self.assertEqual(a, b)

    def test_old_token_address_field(self):
        item = transfer_item()
        item["token"] = {"address": NFT, "type": "ERC-721"}
        self.assertEqual(self.facts({"items": [item]})["transfers"][0]["token"], NFT)

    def test_1155_value_kept(self):
        item = transfer_item(std="ERC-1155", value="5")
        self.assertEqual(self.facts({"items": [item]})["transfers"][0]["value"], "5")

    def test_unrelated_transfers_dropped(self):
        other = transfer_item(frm="0x" + "55" * 20, to="0x" + "66" * 20)
        self.assertEqual(self.facts({"items": [other]})["transfers"], [])

    def test_next_page_marks_truncated(self):
        self.assertTrue(self.facts({"items": [], "next_page_params": {"p": 1}})["truncated"])

    def test_cap_keeps_direct_transfer_first(self):
        noise = [transfer_item(frm=BUYER, to=HOST_ADDR, token=OTHER, li=i, tx="0x%064x" % (i + 100)) for i in range(40)]
        direct = transfer_item()
        f = self.facts({"items": noise + [direct]})
        self.assertEqual(len(f["transfers"]), m.MAX_FACT_TRANSFERS)
        self.assertEqual(f["transfers"][0]["to"], BUYER)
        self.assertFalse(f["truncated"])

    def test_dropping_a_relevant_transfer_marks_truncated(self):
        rel = [transfer_item(frm=SELLER, to=HOP_ADDR, li=i, tx="0x%064x" % (i + 1)) for i in range(40)]
        self.assertTrue(self.facts({"items": rel})["truncated"])

    def test_hostile_items_do_not_crash(self):
        f = self.facts({"items": [5, None, "x", {"total": 3, "token": 4, "from": 1}, transfer_item()]})
        self.assertEqual(len(f["transfers"]), 1)

    def test_items_not_a_list(self):
        self.assertIsNone(self.facts({"items": "nope"}))

    def test_non_json_and_non_dict(self):
        self.assertIsNone(m._extract_facts("transfers", "<html>", ANCHORS, {}))
        self.assertIsNone(m._extract_facts("transfers", "[1,2]", ANCHORS, {}))

    def test_size_stays_bounded_with_huge_values(self):
        items = [transfer_item(token_id=str(2 ** 255), std="ERC-1155", value=str(2 ** 255), li=i,
                               tx="0x%064x" % (i + 1)) for i in range(60)]
        f = self.facts({"items": items})
        self.assertLessEqual(len(json.dumps(f, sort_keys=True)), m.MAX_CONTENT_CHARS)


HOST_ADDR = "0x" + "77" * 20
HOP_ADDR = "0x" + "44" * 20
OTHER = "0x" + "99" * 20


class TxFacts(unittest.TestCase):
    def facts(self, doc):
        return m._extract_facts("tx", json.dumps(doc), ANCHORS, {})

    def test_success(self):
        f = self.facts(tx_doc(transfers=[transfer_item()]))
        self.assertTrue(f["ok"])
        self.assertEqual(f["hash"], TX1)
        self.assertEqual(f["transfers"][0]["tx"], TX1)

    def test_failed_status(self):
        self.assertFalse(self.facts(tx_doc(ok=False))["ok"])

    def test_confirmations_do_not_change_the_frozen_form(self):
        a = json.dumps(self.facts(tx_doc(ts="2030-01-01T00:00:00Z", confirmations=1)), sort_keys=True)
        b = json.dumps(self.facts(tx_doc(ts="2030-01-01T00:00:00Z", confirmations=99999)), sort_keys=True)
        self.assertEqual(a, b)

    def test_bad_hash_rejected(self):
        self.assertIsNone(self.facts(tx_doc(tx="0x12")))

    def test_embedded_transfers_inherit_tx_hash_and_time(self):
        item = transfer_item()
        del item["transaction_hash"]
        del item["timestamp"]
        f = self.facts(tx_doc(ts="2030-01-01T00:00:00Z", transfers=[item]))
        self.assertEqual(f["transfers"][0]["tx"], TX1)
        self.assertEqual(f["transfers"][0]["ts"], "2030-01-01T00:00:00+00:00")

    def test_overflow_marks_truncated(self):
        self.assertTrue(self.facts(tx_doc(token_transfers_overflow=True))["truncated"])


class InstanceAndHolders(unittest.TestCase):
    def test_instance_owner(self):
        f = m._extract_facts("instance", json.dumps(instance_doc()), ANCHORS, {"contract": NFT, "id": "7"})
        self.assertEqual((f["owner"], f["id"], f["contract"], f["type"]), (BUYER, "7", NFT, "ERC-721"))

    def test_instance_without_owner(self):
        f = m._extract_facts("instance", json.dumps(instance_doc(owner=None)), ANCHORS, {"contract": NFT, "id": "7"})
        self.assertEqual(f["owner"], "")

    def test_holders_only_keeps_parties(self):
        doc = holders_doc([(BUYER, "5"), (SELLER, "2"), ("0x" + "55" * 20, "9")])
        f = m._extract_facts("holders", json.dumps(doc), ANCHORS, {"contract": NFT, "id": "7"})
        self.assertEqual({h["a"]: h["v"] for h in f["holders"]}, {BUYER: "5", SELLER: "2"})

    def test_holders_truncated_flag(self):
        f = m._extract_facts("holders", json.dumps(holders_doc([(BUYER, "5")], more=True)), ANCHORS,
                             {"contract": NFT, "id": "7"})
        self.assertTrue(f["truncated"])


class GatherPages(unittest.TestCase):
    def test_unparseable_page_has_no_facts_and_is_bounded(self):
        from _bootstrap import serve
        url = tx_url()
        with serve({url: "<html>" + "x" * 5000}):
            page = m._gather_pages([url], {**ANCHORS})[0]
        self.assertIsNone(page["tx"])
        self.assertLessEqual(len(page["content"]), 1500)

    def test_fetch_failure_is_empty_not_an_exception(self):
        from _bootstrap import serve
        with serve({}):
            page = m._gather_pages([tx_url()], ANCHORS)[0]
        self.assertIsNone(page["tx"])
        self.assertEqual(page["content"], "")

    def test_frozen_content_is_the_sorted_facts(self):
        from _bootstrap import serve
        url = tx_url()
        with serve({url: tx_doc(transfers=[transfer_item()])}):
            page = m._gather_pages([url], ANCHORS)[0]
        self.assertEqual(page["content"], json.dumps(page["tx"], sort_keys=True))


if __name__ == "__main__":
    unittest.main()
