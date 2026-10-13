import json
import unittest

from _bootstrap import (m, gl, make_contract, set_caller, call_payable, reset_transfers, iso, new_trade, open_trade,
                        get, put, mutate, BUYER, SELLER, STRANGER)

UserError = gl.vm.UserError


def ruled(c, outcome, stake=1000):
    tid = open_trade(c, value=stake)
    t = get(c, tid)
    t.update(status="ruled", tier1_outcome=outcome, appeal_deadline=iso(-5), tier1_resolved_at=iso(-100))
    put(c, tid, t)
    return tid


class SettlementTable(unittest.TestCase):
    def settle(self, outcome):
        c = make_contract()
        tid = ruled(c, outcome)
        set_caller(STRANGER)
        c.finalize_ruling(tid)
        return c, tid

    def balances(self, c):
        return int(c.get_pending_withdrawal(BUYER)), int(c.get_pending_withdrawal(SELLER))

    def test_delivered_pays_the_seller_the_pot(self):
        c, _ = self.settle("ASSET_DELIVERED")
        self.assertEqual(self.balances(c), (0, 2000))

    def test_not_delivered_pays_the_buyer_the_pot(self):
        c, _ = self.settle("NOT_DELIVERED")
        self.assertEqual(self.balances(c), (2000, 0))

    def test_partial_splits_evenly(self):
        c, _ = self.settle("PARTIALLY_DELIVERED")
        self.assertEqual(self.balances(c), (1000, 1000))

    def test_partial_odd_pot_loses_nothing(self):
        c = make_contract()
        tid = ruled(c, "PARTIALLY_DELIVERED", stake=1001)
        c.finalize_ruling(tid)
        self.assertEqual(sum(self.balances(c)), 2002)

    def test_undetermined_returns_each_stake(self):
        c, _ = self.settle("UNDETERMINED")
        self.assertEqual(self.balances(c), (1000, 1000))

    def test_settles_exactly_once(self):
        c, tid = self.settle("ASSET_DELIVERED")
        with self.assertRaises(UserError):
            c.finalize_ruling(tid)
        self.assertEqual(self.balances(c), (0, 2000))

    def test_cannot_finalize_inside_the_appeal_window(self):
        c = make_contract()
        tid = ruled(c, "ASSET_DELIVERED")
        mutate(c, tid, appeal_deadline=iso(3600))
        with self.assertRaises(UserError):
            c.finalize_ruling(tid)

    def test_cannot_finalize_before_ruling(self):
        c = make_contract()
        tid = open_trade(c)
        with self.assertRaises(UserError):
            c.finalize_ruling(tid)

    def test_final_outcome_and_status_recorded(self):
        c, tid = self.settle("NOT_DELIVERED")
        t = get(c, tid)
        self.assertEqual((t["status"], t["final_outcome"], t["payout_settled"]), ("finalized", "NOT_DELIVERED", True))


class Withdraw(unittest.TestCase):
    def setUp(self):
        reset_transfers()
        self.c = make_contract()
        tid = ruled(self.c, "ASSET_DELIVERED")
        self.c.finalize_ruling(tid)

    def test_pays_out_and_zeroes_the_ledger(self):
        set_caller(SELLER)
        self.c.withdraw()
        self.assertEqual(gl.evm.transfers, [{"to": SELLER, "value": m.u256(2000)}])
        self.assertEqual(self.c.get_pending_withdrawal(SELLER), "0")

    def test_second_withdraw_fails(self):
        set_caller(SELLER)
        self.c.withdraw()
        with self.assertRaises(UserError):
            self.c.withdraw()
        self.assertEqual(len(gl.evm.transfers), 1)

    def test_nothing_to_withdraw(self):
        set_caller(STRANGER)
        with self.assertRaises(UserError):
            self.c.withdraw()

    def test_a_caller_only_gets_their_own_balance(self):
        set_caller(BUYER)
        with self.assertRaises(UserError):
            self.c.withdraw()
        self.assertEqual(self.c.get_pending_withdrawal(SELLER), "2000")


class Views(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()

    def page(self, offset=0, limit=10):
        return json.loads(self.c.get_trades_page(offset, limit))

    def test_empty(self):
        self.assertEqual(self.page(), {"offset": 0, "total": 0, "trades": []})

    def test_newest_first(self):
        for _ in range(3):
            new_trade(self.c)
        ids = [t["trade_id"] for t in self.page()["trades"]]
        self.assertEqual(ids, ["trade_2", "trade_1", "trade_0"])

    def test_offset_and_limit(self):
        for _ in range(5):
            new_trade(self.c)
        p = self.page(offset=1, limit=2)
        self.assertEqual([t["trade_id"] for t in p["trades"]], ["trade_3", "trade_2"])
        self.assertEqual(p["total"], 5)

    def test_offset_past_the_end(self):
        new_trade(self.c)
        self.assertEqual(self.page(offset=9)["trades"], [])

    def test_limit_is_capped_and_floored(self):
        for _ in range(3):
            new_trade(self.c)
        self.assertEqual(len(self.page(limit=0)["trades"]), 1)
        self.assertEqual(len(self.page(limit=10 ** 6)["trades"]), 3)

    def test_summary_fields(self):
        new_trade(self.c, resale=True)
        row = self.page()["trades"][0]
        for key in ("trade_id", "status", "buyer", "seller", "nft_contract", "token_id", "token_standard",
                    "token_quantity", "allow_resale", "stake_amount", "deadline", "outcome", "terms"):
            self.assertIn(key, row)
        self.assertEqual((row["buyer"], row["seller"], row["allow_resale"]), (BUYER, SELLER, True))

    def test_summary_shows_the_final_outcome_over_the_automated_one(self):
        tid = ruled(self.c, "ASSET_DELIVERED")
        self.c.finalize_ruling(tid)
        self.assertEqual(self.page()["trades"][0]["outcome"], "ASSET_DELIVERED")

    def test_get_trade_unknown(self):
        with self.assertRaises(UserError):
            self.c.get_trade("nope")

    def test_total_trades(self):
        new_trade(self.c)
        self.assertEqual(self.c.total_trades(), 1)

    def test_unknown_juror_and_balance_defaults(self):
        self.assertEqual(json.loads(self.c.get_juror(STRANGER))["stake"], "0")
        self.assertEqual(self.c.get_pending_withdrawal(STRANGER), "0")
        self.assertEqual(self.c.get_transfer_claim("0xabc:1"), "")

    def test_contract_balance_tracks_escrow(self):
        new_trade(self.c, value=1234)
        self.assertEqual(self.c.get_contract_balance(), "1234")


if __name__ == "__main__":
    unittest.main()
