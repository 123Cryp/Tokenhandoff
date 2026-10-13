import json
import unittest
from unittest.mock import patch

from _bootstrap import (m, gl, TokenHandoff, make_contract, set_caller, call_payable, iso, frozen_trade, get, put, mutate,
                        serve, llm, age_jurors, draw_jury, pass_deadline, delivered_urls, sha, instance_url,
                        tx_url, transfer_item, tx_doc, instance_doc, BUYER, SELLER, STRANGER, HOP, JURORS, TX1)

UserError = gl.vm.UserError
BOND = 200  # 20% of the 1000 stake


class JuryBase(unittest.TestCase):
    docs = None

    def setUp(self):
        self.docs = delivered_urls()
        self.c = make_contract()
        self.tid = frozen_trade(self.c, list(self.docs.keys()))
        set_caller(STRANGER)
        with serve(self.docs), llm("ASSET_DELIVERED"):
            self.c.resolve_delivery(self.tid)
        for addr in JURORS[:6]:
            set_caller(addr)
            call_payable(self.c, "register_juror", 5000)
        age_jurors(self.c)

    def t(self):
        return get(self.c, self.tid)

    def appeal(self, who=SELLER, value=BOND):
        set_caller(who)
        return call_payable(self.c, "appeal", value, self.tid)

    def to_voting(self, who=SELLER):
        self.appeal(who)
        draw_jury(self.c, self.tid)
        return self.t()["jury"]["selected_jurors"]

    def commit(self, juror, vote, salt="s"):
        set_caller(juror)
        self.c.commit_vote(self.tid, sha(f"{vote}:{salt}:{juror.lower()}:{self.tid}"))

    def open_reveal(self):
        j = self.t()
        j["jury"]["commit_deadline"] = iso(-10)
        put(self.c, self.tid, j)

    def reveal(self, juror, vote, salt="s"):
        set_caller(juror)
        self.c.reveal_vote(self.tid, vote, salt)

    def close_reveal(self):
        j = self.t()
        j["jury"]["commit_deadline"] = iso(-20)
        j["jury"]["reveal_deadline"] = iso(-10)
        put(self.c, self.tid, j)

    def run_vote(self, votes, verdict):
        selected = self.t()["jury"]["selected_jurors"]
        for juror, vote in zip(selected, votes):
            if vote is not None:
                self.commit(juror, vote)
        self.open_reveal()
        for juror, vote in zip(selected, votes):
            if vote is not None:
                self.reveal(juror, vote)
        self.close_reveal()
        set_caller(STRANGER)
        with llm(verdict):
            self.c.finalize_jury(self.tid)
        return selected


class JurorRegistry(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()

    def test_register_and_top_up(self):
        set_caller(JURORS[0])
        call_payable(self.c, "register_juror", 100)
        call_payable(self.c, "register_juror", 50)
        self.assertEqual(json.loads(self.c.get_juror(JURORS[0]))["stake"], "150")

    def test_floor(self):
        TokenHandoff.MIN_JUROR_STAKE = m.u256(10)
        try:
            set_caller(JURORS[0])
            with self.assertRaises(UserError):
                call_payable(self.c, "register_juror", 9)
        finally:
            TokenHandoff.MIN_JUROR_STAKE = m.u256(1)

    def test_unstake_returns_to_the_ledger(self):
        set_caller(JURORS[0])
        call_payable(self.c, "register_juror", 100)
        self.c.unstake_juror("40")
        self.assertEqual(self.c.get_pending_withdrawal(JURORS[0]), "40")
        self.assertEqual(json.loads(self.c.get_juror(JURORS[0]))["stake"], "60")

    def test_cannot_unstake_more_than_staked_or_zero(self):
        set_caller(JURORS[0])
        call_payable(self.c, "register_juror", 100)
        for bad in ("101", "0", "-5"):
            with self.assertRaises(UserError):
                self.c.unstake_juror(bad)

    def test_unknown_juror_cannot_unstake(self):
        set_caller(JURORS[1])
        with self.assertRaises(UserError):
            self.c.unstake_juror("1")


class Appeal(JuryBase):
    def test_either_party_can_appeal_with_the_exact_bond(self):
        self.appeal(BUYER)
        t = self.t()
        self.assertEqual((t["status"], t["appeal"]["appellant"], t["appeal"]["bond_amount"]), ("appeal_filed", BUYER, str(BOND)))

    def test_strangers_cannot_appeal(self):
        with self.assertRaises(UserError):
            self.appeal(STRANGER)

    def test_wrong_bond(self):
        for v in (BOND - 1, BOND + 1, 0):
            with self.assertRaises(UserError):
                self.appeal(SELLER, v)

    def test_window_closed(self):
        mutate(self.c, self.tid, appeal_deadline=iso(-5))
        with self.assertRaises(UserError):
            self.appeal()

    def test_needs_five_jurors(self):
        c = make_contract()
        tid = frozen_trade(c, list(self.docs.keys()))
        set_caller(STRANGER)
        with serve(self.docs), llm("ASSET_DELIVERED"):
            c.resolve_delivery(tid)
        for addr in JURORS[:4]:
            set_caller(addr)
            call_payable(c, "register_juror", 100)
        age_jurors(c)
        set_caller(SELLER)
        with self.assertRaises(UserError):
            call_payable(c, "appeal", BOND, tid)

    def test_parties_are_not_eligible_jurors(self):
        set_caller(BUYER)
        call_payable(self.c, "register_juror", 5000)
        age_jurors(self.c)
        self.assertEqual(self.c.count_eligible_jurors(self.tid), 6)

    def test_jurors_registered_after_acceptance_are_not_eligible(self):
        set_caller(JURORS[8])
        call_payable(self.c, "register_juror", 5000)
        self.assertEqual(self.c.count_eligible_jurors(self.tid), 6)

    def test_only_one_appeal(self):
        self.appeal()
        with self.assertRaises(UserError):
            self.appeal(BUYER)

    def test_pool_members_cannot_unstake_while_the_appeal_is_pending(self):
        self.appeal()
        set_caller(JURORS[0])
        with self.assertRaises(UserError):
            self.c.unstake_juror("1")


class Draw(JuryBase):
    def test_cannot_draw_before_the_randomness_round(self):
        self.appeal()
        set_caller(STRANGER)
        with patch.object(gl.nondet.web, "render", return_value=json.dumps({"round": 1, "randomness": "x"})):
            with self.assertRaises(UserError):
                self.c.draw_jury(self.tid)

    def test_draw_selects_five_distinct_jurors_and_is_replayable(self):
        selected = self.to_voting()
        self.assertEqual(len(selected), 5)
        self.assertEqual(len(set(selected)), 5)
        self.assertTrue(json.loads(self.c.verify_jury_selection(self.tid))["match"])
        self.assertEqual(self.t()["status"], "jury_voting")

    def test_selection_depends_on_the_beacon(self):
        outcomes = set()
        for beacon in ("a", "b", "c", "d", "e", "f"):
            pool = [[a, 1] for a in JURORS[:10]]
            sel, _ = self.c._select_from_pool("trade_0", beacon, pool)
            outcomes.add(tuple(sel))
        self.assertGreater(len(outcomes), 1)

    def test_bad_beacon_response_reverts(self):
        self.appeal()
        t = self.t()
        t["appeal_deadline"] = iso(-300)
        t["appeal"]["draw_round"] = 1
        put(self.c, self.tid, t)
        set_caller(STRANGER)
        with patch.object(gl.nondet.web, "render", return_value="not json"):
            with self.assertRaises(UserError):
                self.c.draw_jury(self.tid)

    def test_selected_jurors_are_locked(self):
        selected = self.to_voting()
        set_caller(selected[0])
        with self.assertRaises(UserError):
            self.c.unstake_juror("1")

    def test_draw_needs_a_filed_appeal(self):
        with self.assertRaises(UserError):
            self.c.draw_jury(self.tid)

    def test_verify_selection_without_a_jury(self):
        with self.assertRaises(UserError):
            self.c.verify_jury_selection(self.tid)


class Voting(JuryBase):
    def setUp(self):
        super().setUp()
        self.selected = self.to_voting()

    def test_unselected_cannot_commit(self):
        outsider = [a for a in JURORS[:6] if a.lower() not in self.selected][0]
        set_caller(outsider)
        with self.assertRaises(UserError):
            self.c.commit_vote(self.tid, sha("x"))

    def test_commit_must_be_a_sha256_hex(self):
        set_caller(self.selected[0])
        for bad in ("abc", "z" * 64, ""):
            with self.assertRaises(UserError):
                self.c.commit_vote(self.tid, bad)

    def test_one_commit_per_juror(self):
        self.commit(self.selected[0], "NOT_DELIVERED")
        with self.assertRaises(UserError):
            self.commit(self.selected[0], "NOT_DELIVERED")

    def test_commit_window_closed(self):
        self.open_reveal()
        with self.assertRaises(UserError):
            self.commit(self.selected[0], "NOT_DELIVERED")

    def test_reveal_not_open_during_commit_window(self):
        self.commit(self.selected[0], "NOT_DELIVERED")
        with self.assertRaises(UserError):
            self.reveal(self.selected[0], "NOT_DELIVERED")

    def test_reveal_must_match_the_commit(self):
        self.commit(self.selected[0], "NOT_DELIVERED")
        self.open_reveal()
        with self.assertRaises(UserError):
            self.reveal(self.selected[0], "UNDETERMINED")
        with self.assertRaises(UserError):
            self.reveal(self.selected[0], "NOT_DELIVERED", salt="other")

    def test_a_copied_commit_cannot_be_revealed_by_the_copier(self):
        self.commit(self.selected[0], "NOT_DELIVERED")
        copied = self.t()["jury"]["commits"][self.selected[0]]
        set_caller(self.selected[1])
        self.c.commit_vote(self.tid, copied)
        self.open_reveal()
        with self.assertRaises(UserError):
            self.reveal(self.selected[1], "NOT_DELIVERED")

    def test_invalid_vote_and_old_vocabulary(self):
        self.commit(self.selected[0], "TRADE_COMPLETED")
        self.open_reveal()
        with self.assertRaises(UserError):
            self.reveal(self.selected[0], "TRADE_COMPLETED")

    def test_no_commit_no_reveal_and_no_double_reveal(self):
        self.open_reveal()
        with self.assertRaises(UserError):
            self.reveal(self.selected[0], "UNDETERMINED")

    def test_double_reveal(self):
        self.commit(self.selected[0], "UNDETERMINED")
        self.open_reveal()
        self.reveal(self.selected[0], "UNDETERMINED")
        with self.assertRaises(UserError):
            self.reveal(self.selected[0], "UNDETERMINED")

    def test_reveal_window_closed(self):
        self.commit(self.selected[0], "UNDETERMINED")
        self.close_reveal()
        with self.assertRaises(UserError):
            self.reveal(self.selected[0], "UNDETERMINED")

    def test_salt_is_bounded(self):
        self.commit(self.selected[0], "UNDETERMINED", salt="s" * 257)
        self.open_reveal()
        with self.assertRaises(UserError):
            self.reveal(self.selected[0], "UNDETERMINED", salt="s" * 257)

    def test_finalize_before_the_reveal_window_closes(self):
        set_caller(STRANGER)
        with self.assertRaises(UserError):
            self.c.finalize_jury(self.tid)


class Finalize(JuryBase):
    def setUp(self):
        super().setUp()
        self.to_voting(SELLER)

    def pending(self, addr):
        return int(self.c.get_pending_withdrawal(addr))

    def test_verified_majority_overturns_and_settles(self):
        self.run_vote(["UNDETERMINED"] * 4 + ["ASSET_DELIVERED"], "UNDETERMINED")
        t = self.t()
        self.assertEqual((t["status"], t["final_outcome"], t["jury"]["verification_passed"]), ("finalized", "UNDETERMINED", True))
        self.assertEqual(self.pending(BUYER), 1000)
        self.assertGreaterEqual(self.pending(SELLER), 1000 + BOND)

    def test_slashed_minority_pays_the_majority(self):
        selected = self.run_vote(["UNDETERMINED"] * 4 + ["ASSET_DELIVERED"], "UNDETERMINED")
        minority = json.loads(self.c.get_juror(selected[4]))
        self.assertEqual(minority["stake"], str(5000 - 500))
        self.assertEqual(minority["minority_votes"], 1)
        self.assertLess(minority["reputation"], 0)
        majority = json.loads(self.c.get_juror(selected[0]))
        self.assertEqual(majority["correct_consensus_votes"], 1)
        self.assertGreater(self.pending(selected[0]), 0)

    def test_jurors_are_unlocked_afterwards(self):
        selected = self.run_vote(["UNDETERMINED"] * 5, "UNDETERMINED")
        for a in selected:
            self.assertEqual(json.loads(self.c.get_juror(a))["locked_cases"], 0)

    def test_majority_the_validators_do_not_reproduce_falls_back_to_tier1(self):
        selected = self.run_vote(["NOT_DELIVERED"] * 5, "ASSET_DELIVERED")
        t = self.t()
        self.assertEqual(t["final_outcome"], "ASSET_DELIVERED")
        self.assertFalse(t["jury"]["verification_passed"])
        self.assertEqual(self.pending(SELLER), 2000 + BOND)
        for a in selected:
            self.assertEqual(json.loads(self.c.get_juror(a))["stake"], "5000")

    def test_appellant_that_loses_forfeits_the_bond_to_the_majority(self):
        selected = self.run_vote(["ASSET_DELIVERED"] * 5, "ASSET_DELIVERED")
        t = self.t()
        self.assertEqual(t["final_outcome"], "ASSET_DELIVERED")
        self.assertEqual(self.pending(SELLER), 2000)
        self.assertEqual(sum(self.pending(a) for a in selected), BOND)

    def test_tie_or_too_few_reveals_hold_tier1_and_refund_the_bond(self):
        selected = self.run_vote(["UNDETERMINED", "UNDETERMINED", None, None, None], "UNDETERMINED")
        t = self.t()
        self.assertEqual(t["final_outcome"], "ASSET_DELIVERED")
        self.assertIsNone(t["jury"]["provisional_outcome"])
        self.assertEqual(self.pending(SELLER), 2000 + BOND)

    def test_non_revealers_are_slashed_and_the_slash_is_redistributed(self):
        selected = self.run_vote(["UNDETERMINED", "UNDETERMINED", None, None, None], "UNDETERMINED")
        silent = json.loads(self.c.get_juror(selected[4]))
        self.assertEqual(silent["stake"], str(5000 - 750))
        self.assertEqual(silent["non_reveals"], 1)
        self.assertEqual(self.pending(selected[0]) + self.pending(selected[1]), 3 * 750)

    def test_nobody_revealed_slash_goes_to_the_appellant(self):
        self.run_vote([None] * 5, "UNDETERMINED")
        self.assertEqual(self.pending(SELLER), 2000 + BOND + 5 * 750)

    def test_tier2_cannot_confirm_a_delivery_the_frozen_facts_do_not_support(self):
        # Tier 1 says UNDETERMINED (the buyer no longer holds the token); the jury votes ASSET_DELIVERED
        docs = {tx_url(suffix="/token-transfers"): {"items": [transfer_item()], "next_page_params": None},
                tx_url(): tx_doc(), instance_url(): instance_doc(owner=HOP)}
        c = make_contract()
        tid = frozen_trade(c, list(docs.keys()))
        pass_deadline(c, tid)
        set_caller(STRANGER)
        with serve(docs), llm("ASSET_DELIVERED"):
            c.resolve_delivery(tid)
        self.assertEqual(get(c, tid)["tier1_outcome"], "UNDETERMINED")
        for addr in JURORS[:6]:
            set_caller(addr)
            call_payable(c, "register_juror", 5000)
        age_jurors(c)
        set_caller(BUYER)
        call_payable(c, "appeal", BOND, tid)
        draw_jury(c, tid)
        selected = get(c, tid)["jury"]["selected_jurors"]
        for a in selected:
            set_caller(a)
            c.commit_vote(tid, sha(f"ASSET_DELIVERED:s:{a.lower()}:{tid}"))
        t = get(c, tid)
        t["jury"]["commit_deadline"] = iso(-10)
        put(c, tid, t)
        for a in selected:
            set_caller(a)
            c.reveal_vote(tid, "ASSET_DELIVERED", "s")
        t = get(c, tid)
        t["jury"]["reveal_deadline"] = iso(-5)
        put(c, tid, t)
        set_caller(STRANGER)
        with llm("ASSET_DELIVERED"):
            c.finalize_jury(tid)
        t = get(c, tid)
        self.assertFalse(t["jury"]["verification_passed"])
        self.assertEqual(t["final_outcome"], "UNDETERMINED")

    def test_tampered_frozen_evidence_fails_verification(self):
        t = self.t()
        t["evidence_content"][0]["content"] = "tampered"
        put(self.c, self.tid, t)
        self.run_vote(["UNDETERMINED"] * 5, "UNDETERMINED")
        self.assertFalse(self.t()["jury"]["verification_passed"])
        self.assertEqual(self.t()["final_outcome"], "ASSET_DELIVERED")

    def test_verification_failure_reverts_without_slashing(self):
        selected = self.t()["jury"]["selected_jurors"]
        for a in selected:
            self.commit(a, "UNDETERMINED")
        self.open_reveal()
        for a in selected:
            self.reveal(a, "UNDETERMINED")
        self.close_reveal()
        set_caller(STRANGER)
        with patch.object(gl.nondet, "exec_prompt", side_effect=RuntimeError("down")):
            with self.assertRaises(UserError):
                self.c.finalize_jury(self.tid)
        self.assertEqual(self.t()["status"], "jury_voting")
        self.assertEqual(json.loads(self.c.get_juror(selected[0]))["stake"], "5000")

    def test_a_delivery_majority_cannot_reuse_a_transfer_claimed_elsewhere(self):
        self.c.transfer_claims[f"{TX1}:3"] = "trade_other"
        self.run_vote(["ASSET_DELIVERED"] * 5, "ASSET_DELIVERED")
        self.assertFalse(self.t()["jury"]["verification_passed"])

    def test_verification_prompt_hides_tier1_reasoning(self):
        t = self.t()
        t["tier1_reasoning"] = "TIER1-SECRET-MARKER"
        put(self.c, self.tid, t)
        seen = []
        selected = self.t()["jury"]["selected_jurors"]
        for a in selected:
            self.commit(a, "UNDETERMINED")
        self.open_reveal()
        for a in selected:
            self.reveal(a, "UNDETERMINED")
        self.close_reveal()
        set_caller(STRANGER)
        with patch.object(gl.nondet, "exec_prompt",
                          side_effect=lambda p, response_format="json": seen.append(p) or {"outcome": "UNDETERMINED"}):
            self.c.finalize_jury(self.tid)
        self.assertTrue(seen)
        for prompt in seen:
            self.assertNotIn("TIER1-SECRET-MARKER", prompt)
            self.assertNotIn("UNDETERMINED", prompt.split("STRUCTURED FINDINGS")[0])


class StuckAppeal(JuryBase):
    def test_recovery_of_an_appeal_that_is_never_drawn(self):
        self.appeal()
        set_caller(STRANGER)
        with self.assertRaises(UserError):
            self.c.expire_stuck_appeal(self.tid)
        mutate(self.c, self.tid, appeal_deadline=iso(-9 * 86400))
        self.c.expire_stuck_appeal(self.tid)
        t = self.t()
        self.assertEqual((t["status"], t["final_outcome"]), ("finalized", "ASSET_DELIVERED"))
        self.assertEqual(int(self.c.get_pending_withdrawal(SELLER)), 2000 + BOND)
        set_caller(JURORS[0])
        self.c.unstake_juror("1")

    def test_recovery_of_a_jury_round_that_cannot_finalize(self):
        selected = self.to_voting()
        t = self.t()
        t["jury"]["reveal_deadline"] = iso(-9 * 86400)
        put(self.c, self.tid, t)
        set_caller(STRANGER)
        self.c.expire_stuck_appeal(self.tid)
        self.assertEqual(self.t()["final_outcome"], "ASSET_DELIVERED")
        for a in selected:
            self.assertEqual(json.loads(self.c.get_juror(a))["locked_cases"], 0)

    def test_wrong_status(self):
        set_caller(STRANGER)
        with self.assertRaises(UserError):
            self.c.expire_stuck_appeal(self.tid)


if __name__ == "__main__":
    unittest.main()
