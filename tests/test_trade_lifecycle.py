import json
import unittest

from _bootstrap import (m, gl, TokenHandoff, make_contract, set_caller, call_payable, iso, new_trade, open_trade, get, put,
                        mutate, tx_url, instance_url, BUYER, SELLER, STRANGER, NFT, HOST, TX1)

UserError = gl.vm.UserError


def create(c, **over):
    args = dict(party_b=SELLER, terms="Sell token 7", criteria="Token must reach buyer", contract=NFT, token_id="7",
                standard="ERC-721", quantity=1, hosts=HOST, deadline=iso(7200), resale=False, min_sources=1, value=1000)
    args.update(over)
    set_caller(BUYER)
    return call_payable(c, "create_trade", args["value"], args["party_b"], args["terms"], args["criteria"],
                        args["contract"], args["token_id"], args["standard"], args["quantity"], args["hosts"],
                        args["deadline"], args["resale"], args["min_sources"])


class CreateTrade(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()

    def test_records_every_anchor(self):
        tid = create(self.c, token_id="0x1F", resale=True, hosts="ETH.blockscout.com, base-sepolia.blockscout.com")
        t = get(self.c, tid)
        self.assertEqual((t["party_a"], t["party_b"], t["buyer"], t["seller"]), (BUYER, SELLER, BUYER, SELLER))
        self.assertEqual((t["nft_contract"], t["token_id"], t["token_standard"], t["token_quantity"]), (NFT, "31", "ERC-721", 1))
        self.assertTrue(t["allow_resale"])
        self.assertEqual(t["allowed_evidence_hosts"], ["eth.blockscout.com", "base-sepolia.blockscout.com"])
        self.assertEqual(t["status"], "created")
        self.assertEqual(self.c.total_trades(), 1)

    def test_contract_address_is_lowercased(self):
        tid = create(self.c, contract="0x" + "AB" * 20)
        self.assertEqual(get(self.c, tid)["nft_contract"], "0x" + "ab" * 20)

    def test_1155_with_quantity(self):
        t = get(self.c, create(self.c, standard="erc1155", quantity=5))
        self.assertEqual((t["token_standard"], t["token_quantity"]), ("ERC-1155", 5))

    def test_721_quantity_must_be_one(self):
        with self.assertRaises(UserError):
            create(self.c, quantity=2)

    def test_quantity_bounds(self):
        for q in (0, -1, 10 ** 30 + 1):
            with self.assertRaises(UserError):
                create(self.c, standard="ERC-1155", quantity=q)

    def test_bad_contract_address(self):
        for bad in ("", "0x12", "0x" + "0" * 40, "nope", "0x" + "g" * 40):
            with self.assertRaises(UserError):
                create(self.c, contract=bad)

    def test_bad_token_id(self):
        for bad in ("", "abc", "-1", "1.5", str(2 ** 256)):
            with self.assertRaises(UserError):
                create(self.c, token_id=bad)

    def test_bad_standard(self):
        for bad in ("ERC-20", "", "721 "):
            with self.assertRaises(UserError):
                create(self.c, standard=bad)

    def test_stake_floor(self):
        TokenHandoff.MIN_STAKE = m.u256(100)
        try:
            with self.assertRaises(UserError):
                create(self.c, value=99)
            create(self.c, value=100)
        finally:
            TokenHandoff.MIN_STAKE = m.u256(1)

    def test_seller_must_differ_from_buyer(self):
        with self.assertRaises(UserError):
            create(self.c, party_b=BUYER)

    def test_seller_must_be_an_address(self):
        with self.assertRaises(Exception):
            create(self.c, party_b="not-an-address")

    def test_deadline_bounds(self):
        for secs in (TokenHandoff.MIN_DEADLINE_LEAD_SECONDS // 2, 40 * 24 * 3600):
            with self.assertRaises(UserError):
                create(self.c, deadline=iso(secs))

    def test_empty_text_rejected(self):
        for field in ("terms", "criteria"):
            with self.assertRaises(UserError):
                create(self.c, **{field: "  "})

    def test_text_length_bounded(self):
        with self.assertRaises(UserError):
            create(self.c, terms="x" * 2001)

    def test_hosts_required_valid_and_bounded(self):
        for bad in ("", " , ", "no-dot", "a.com/x", "user@a.com", "a.com:443", ",".join(f"h{i}.example.com" for i in range(11))):
            with self.assertRaises(UserError):
                create(self.c, hosts=bad)

    def test_min_sources_bounds(self):
        for bad in (0, 11):
            with self.assertRaises(UserError):
                create(self.c, min_sources=bad)

    def test_ids_increment(self):
        self.assertEqual(create(self.c), "trade_0")
        self.assertEqual(create(self.c), "trade_1")


class AcceptCancel(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()
        self.tid = new_trade(self.c)

    def test_only_the_seller_can_accept(self):
        for who in (BUYER, STRANGER):
            set_caller(who)
            with self.assertRaises(UserError):
                call_payable(self.c, "accept_trade", 1000, self.tid)

    def test_stake_must_match_exactly(self):
        set_caller(SELLER)
        for v in (999, 1001, 0):
            with self.assertRaises(UserError):
                call_payable(self.c, "accept_trade", v, self.tid)

    def test_accept_moves_to_accepted_and_sets_the_proof_deadline(self):
        set_caller(SELLER)
        call_payable(self.c, "accept_trade", 1000, self.tid)
        t = get(self.c, self.tid)
        self.assertEqual(t["status"], "accepted")
        self.assertIn("evidence_deadline", t)

    def test_cannot_accept_twice(self):
        set_caller(SELLER)
        call_payable(self.c, "accept_trade", 1000, self.tid)
        with self.assertRaises(UserError):
            call_payable(self.c, "accept_trade", 1000, self.tid)

    def test_cannot_accept_after_deadline(self):
        mutate(self.c, self.tid, deadline=iso(-5))
        set_caller(SELLER)
        with self.assertRaises(UserError):
            call_payable(self.c, "accept_trade", 1000, self.tid)

    def test_unknown_trade(self):
        set_caller(SELLER)
        with self.assertRaises(UserError):
            call_payable(self.c, "accept_trade", 1000, "trade_99")

    def test_only_the_buyer_can_cancel(self):
        for who in (SELLER, STRANGER):
            set_caller(who)
            with self.assertRaises(UserError):
                self.c.cancel_trade(self.tid)

    def test_cancel_refunds_the_buyer_once(self):
        set_caller(BUYER)
        self.c.cancel_trade(self.tid)
        self.assertEqual(self.c.get_pending_withdrawal(BUYER), "1000")
        with self.assertRaises(UserError):
            self.c.cancel_trade(self.tid)

    def test_cannot_cancel_after_acceptance(self):
        set_caller(SELLER)
        call_payable(self.c, "accept_trade", 1000, self.tid)
        set_caller(BUYER)
        with self.assertRaises(UserError):
            self.c.cancel_trade(self.tid)

    def test_cannot_accept_a_cancelled_trade(self):
        set_caller(BUYER)
        self.c.cancel_trade(self.tid)
        set_caller(SELLER)
        with self.assertRaises(UserError):
            call_payable(self.c, "accept_trade", 1000, self.tid)


class SubmitProof(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()
        self.tid = open_trade(self.c)

    def submit(self, who, urls):
        set_caller(who)
        return self.c.submit_proof(self.tid, urls)

    def test_only_parties_submit(self):
        with self.assertRaises(UserError):
            self.submit(STRANGER, [tx_url()])

    def test_one_submission_per_party(self):
        self.submit(BUYER, [tx_url()])
        with self.assertRaises(UserError):
            self.submit(BUYER, [tx_url(suffix="/token-transfers")])

    def test_first_submission_does_not_freeze(self):
        self.submit(BUYER, [tx_url()])
        t = get(self.c, self.tid)
        self.assertEqual(t["status"], "accepted")
        self.assertIsNotNone(t["evidence_lock_at"])

    def test_second_submission_freezes(self):
        self.submit(BUYER, [tx_url()])
        self.submit(SELLER, [instance_url()])
        t = get(self.c, self.tid)
        self.assertEqual(t["status"], "proof_frozen")
        self.assertEqual(len(t["evidence_snapshot"]["source_urls"]), 2)

    def test_urls_are_deduplicated_and_sorted(self):
        self.submit(BUYER, [tx_url(), tx_url(), "  " + tx_url() + " "])
        self.assertEqual(get(self.c, self.tid)["evidence_submissions"][BUYER.lower()], [tx_url()])

    def test_http_rejected(self):
        with self.assertRaises(UserError):
            self.submit(BUYER, [tx_url().replace("https", "http")])

    def test_userinfo_rejected(self):
        with self.assertRaises(UserError):
            self.submit(BUYER, [f"https://{HOST}@evil.example/api/v2/transactions/{TX1}"])

    def test_lookalike_hosts_rejected(self):
        for host in ("noteth.blockscout.com", "eth.blockscout.com.evil.example", "blockscout.com"):
            with self.assertRaises(UserError):
                self.submit(BUYER, [f"https://{host}/api/v2/transactions/{TX1}"])

    def test_subdomain_of_allowed_host_is_accepted(self):
        self.submit(BUYER, [f"https://api.{HOST}/api/v2/transactions/{TX1}"])

    def test_non_api_links_rejected(self):
        with self.assertRaises(UserError):
            self.submit(BUYER, [f"https://{HOST}/tx/{TX1}"])

    def test_link_for_another_contract_rejected(self):
        with self.assertRaises(UserError):
            self.submit(BUYER, [instance_url(contract="0x" + "bb" * 20)])

    def test_link_for_another_token_id_rejected(self):
        with self.assertRaises(UserError):
            self.submit(BUYER, [instance_url(token_id="8")])

    def test_holders_link_rejected_for_721(self):
        with self.assertRaises(UserError):
            self.submit(BUYER, [instance_url(suffix="/holders")])

    def test_holders_link_accepted_for_1155(self):
        c = make_contract()
        tid = open_trade(c, standard="ERC-1155", quantity=3)
        set_caller(BUYER)
        c.submit_proof(tid, [instance_url(suffix="/holders")])

    def test_limits(self):
        urls = [tx_url(tx="0x%064x" % i) for i in range(6)]
        with self.assertRaises(UserError):
            self.submit(BUYER, urls)
        with self.assertRaises(UserError):
            self.submit(BUYER, [])
        with self.assertRaises(UserError):
            self.submit(BUYER, ["", "  "])
        with self.assertRaises(UserError):
            self.submit(BUYER, [tx_url() + "?" + "a" * 300])

    def test_one_bad_link_rejects_the_whole_submission(self):
        with self.assertRaises(UserError):
            self.submit(BUYER, [tx_url(), "https://evil.example/api/v2/transactions/" + TX1])
        self.assertEqual(get(self.c, self.tid)["evidence_submissions"], {})

    def test_not_while_created(self):
        tid = new_trade(self.c)
        set_caller(BUYER)
        with self.assertRaises(UserError):
            self.c.submit_proof(tid, [tx_url()])

    def test_window_closed(self):
        mutate(self.c, self.tid, evidence_deadline=iso(-5))
        with self.assertRaises(UserError):
            self.submit(BUYER, [tx_url()])

    def test_counterparty_window_closed(self):
        self.submit(BUYER, [tx_url()])
        mutate(self.c, self.tid, evidence_lock_at=iso(-5))
        with self.assertRaises(UserError):
            self.submit(SELLER, [tx_url(suffix="/token-transfers")])


class FreezeAndExpire(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()

    def test_freeze_needs_a_submission(self):
        tid = open_trade(self.c)
        set_caller(STRANGER)
        with self.assertRaises(UserError):
            self.c.freeze_proof(tid)

    def test_freeze_waits_for_the_grace_period(self):
        tid = open_trade(self.c)
        set_caller(BUYER)
        self.c.submit_proof(tid, [tx_url()])
        set_caller(STRANGER)
        with self.assertRaises(UserError):
            self.c.freeze_proof(tid)
        mutate(self.c, tid, evidence_lock_at=iso(-5))
        self.c.freeze_proof(tid)
        self.assertEqual(get(self.c, tid)["status"], "proof_frozen")

    def test_min_sources_counts_registrable_domains_not_hosts(self):
        tid = open_trade(self.c, min_sources=2, hosts="eth.blockscout.com,base-sepolia.blockscout.com,other-explorer.io")
        set_caller(BUYER)
        self.c.submit_proof(tid, [tx_url()])
        set_caller(SELLER)
        self.c.submit_proof(tid, [f"https://base-sepolia.blockscout.com/api/v2/transactions/{TX1}"])
        t = get(self.c, tid)
        self.assertEqual(t["status"], "accepted")
        mutate(self.c, tid, evidence_lock_at=iso(-5))
        set_caller(STRANGER)
        with self.assertRaises(UserError):
            self.c.freeze_proof(tid)

    def test_two_registrable_domains_satisfy_two_sources(self):
        tid = open_trade(self.c, min_sources=2, hosts="eth.blockscout.com,other-explorer.io")
        set_caller(BUYER)
        self.c.submit_proof(tid, [tx_url()])
        set_caller(SELLER)
        self.c.submit_proof(tid, [f"https://other-explorer.io/api/v2/transactions/{TX1}"])
        self.assertEqual(get(self.c, tid)["status"], "proof_frozen")

    def test_expire_no_proof_refunds_both(self):
        tid = open_trade(self.c)
        set_caller(STRANGER)
        with self.assertRaises(UserError):
            self.c.expire_no_proof(tid)
        mutate(self.c, tid, evidence_deadline=iso(-5))
        self.c.expire_no_proof(tid)
        t = get(self.c, tid)
        self.assertEqual((t["status"], t["final_outcome"]), ("finalized", "UNDETERMINED"))
        self.assertEqual(self.c.get_pending_withdrawal(BUYER), "1000")
        self.assertEqual(self.c.get_pending_withdrawal(SELLER), "1000")

    def test_expire_no_proof_freezes_lockable_submissions_instead(self):
        tid = open_trade(self.c)
        set_caller(BUYER)
        self.c.submit_proof(tid, [tx_url()])
        mutate(self.c, tid, evidence_deadline=iso(-5))
        set_caller(STRANGER)
        self.c.expire_no_proof(tid)
        self.assertEqual(get(self.c, tid)["status"], "proof_frozen")

    def test_expire_no_proof_refunds_when_sources_stay_below_the_minimum(self):
        tid = open_trade(self.c, min_sources=2, hosts="eth.blockscout.com,other-explorer.io")
        set_caller(BUYER)
        self.c.submit_proof(tid, [tx_url()])
        set_caller(SELLER)
        self.c.submit_proof(tid, [tx_url(suffix="/token-transfers")])
        self.assertEqual(get(self.c, tid)["status"], "accepted")
        mutate(self.c, tid, evidence_deadline=iso(-5))
        set_caller(STRANGER)
        self.c.expire_no_proof(tid)
        t = get(self.c, tid)
        self.assertEqual((t["status"], t["final_outcome"]), ("finalized", "UNDETERMINED"))
        self.assertEqual(self.c.get_pending_withdrawal(SELLER), "1000")

    def test_expire_no_proof_wrong_status(self):
        tid = new_trade(self.c)
        with self.assertRaises(UserError):
            self.c.expire_no_proof(tid)


if __name__ == "__main__":
    unittest.main()
