import ast
import io
import os
import tokenize
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEADER = [
    "# v0.2.16",
    '# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }',
]


def read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
        return fh.read()


class DeployFile(unittest.TestCase):
    def setUp(self):
        self.deploy = read("contract_deploy.py")
        self.source = read("contract.py")

    def test_header_is_exactly_two_lines_then_code(self):
        lines = self.deploy.split("\n")
        self.assertEqual(lines[:2], HEADER)
        self.assertTrue(lines[2].startswith("from genlayer import"))

    def test_source_starts_with_the_same_header(self):
        self.assertEqual(self.source.split("\n")[:2], HEADER)

    def test_only_the_two_header_comments_exist(self):
        comments = [t.string for t in tokenize.generate_tokens(io.StringIO(self.deploy).readline)
                    if t.type == tokenize.COMMENT]
        self.assertEqual(comments, HEADER)

    def test_no_docstrings_remain(self):
        tree = ast.parse(self.deploy)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
                first = node.body[0] if node.body else None
                is_doc = isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str)
                self.assertFalse(is_doc, getattr(node, "name", "module"))

    def test_code_is_identical_to_the_source_apart_from_docstrings(self):
        def strip(tree):
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
                    if node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant) \
                            and isinstance(node.body[0].value.value, str):
                        node.body = node.body[1:] or [ast.Pass()]
            return ast.dump(tree)
        self.assertEqual(strip(ast.parse(self.source)), strip(ast.parse(self.deploy)))

    def test_deploy_file_is_up_to_date(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("make_deploy", os.path.join(ROOT, "tools", "make_deploy.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertEqual(mod.build(self.source), self.deploy)

    def test_testnet_file_is_up_to_date_and_only_shortens_windows(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("make_deploy2", os.path.join(ROOT, "tools", "make_deploy.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        testnet = read("contract_testnet_deploy.py")
        self.assertEqual(mod.build(self.source, True), testnet)
        self.assertEqual(testnet.split("\n")[:2], HEADER)
        self.assertIn("class TokenHandoffTestnet(gl.Contract):", testnet)
        self.assertIn("APPEAL_WINDOW_SECONDS = 600", testnet)

    def test_tool_never_uses_regular_expressions(self):
        tool = read(os.path.join("tools", "make_deploy.py"))
        tree = ast.parse(tool)
        imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        self.assertNotIn("re", imported)

    def test_compiles_for_python_311(self):
        ast.parse(self.deploy, feature_version=(3, 11))


class ContractShape(unittest.TestCase):
    def setUp(self):
        self.source = read("contract.py")
        self.tree = ast.parse(self.source)

    def test_storage_is_declared_at_class_level_and_init_assigns_no_containers(self):
        cls = [n for n in self.tree.body if isinstance(n, ast.ClassDef) and n.name == "TokenHandoff"][0]
        init = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "__init__"][0]
        for node in ast.walk(init):
            if isinstance(node, ast.Assign):
                self.assertNotIsInstance(node.value, (ast.Dict, ast.List, ast.Set))
        annotated = {n.target.id for n in cls.body if isinstance(n, ast.AnnAssign)}
        self.assertTrue({"trades", "jurors", "pending_withdrawals", "transfer_claims", "trade_count"} <= annotated)

    def test_nondet_functions_do_not_capture_self(self):
        for name in ("_leader_delivery", "_validate_delivery", "_judge_frozen", "_validate_frozen"):
            fn = [n for n in self.tree.body if isinstance(n, ast.FunctionDef) and n.name == name]
            self.assertEqual(len(fn), 1, name)
            used = {n.id for n in ast.walk(fn[0]) if isinstance(n, ast.Name)}
            self.assertNotIn("self", used)

    def test_run_nondet_unsafe_gets_two_positional_functions(self):
        calls = [n for n in ast.walk(self.tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "run_nondet_unsafe"]
        self.assertEqual(len(calls), 2)
        for call in calls:
            self.assertEqual(len(call.args), 2)
            self.assertEqual(call.keywords, [])

    def test_only_public_writes_that_must_be_open_lack_a_sender_check(self):
        permissionless = {"freeze_proof", "resolve_delivery", "finalize_ruling", "expire_no_proof",
                          "expire_stalled_ruling", "expire_stuck_appeal", "draw_jury", "finalize_jury"}
        cls = [n for n in self.tree.body if isinstance(n, ast.ClassDef) and n.name == "TokenHandoff"][0]
        for fn in cls.body:
            if not isinstance(fn, ast.FunctionDef):
                continue
            decos = ast.unparse(ast.Module(body=[], type_ignores=[])) + " ".join(ast.unparse(d) for d in fn.decorator_list)
            if "gl.public.write" not in decos:
                continue
            body = ast.unparse(fn)
            if fn.name in permissionless:
                continue
            self.assertIn("sender_address", body, fn.name)

    def test_no_sensitive_clock_other_than_datetime_now(self):
        imported = {a.name for n in ast.walk(self.tree) if isinstance(n, ast.Import) for a in n.names}
        self.assertNotIn("time", imported)
        self.assertNotIn("message_raw", self.source)

    def test_emit_transfer_uses_value_only(self):
        self.assertIn("emit_transfer(value=amount)", self.source)

    def test_every_public_write_is_in_the_documented_set(self):
        cls = [n for n in self.tree.body if isinstance(n, ast.ClassDef) and n.name == "TokenHandoff"][0]
        writes = {fn.name for fn in cls.body if isinstance(fn, ast.FunctionDef)
                  and any("gl.public.write" in ast.unparse(d) for d in fn.decorator_list)}
        self.assertEqual(writes, {
            "create_trade", "accept_trade", "cancel_trade", "submit_proof", "freeze_proof", "resolve_delivery",
            "finalize_ruling", "expire_no_proof", "expire_stalled_ruling", "expire_stuck_appeal",
            "register_juror", "unstake_juror", "appeal", "draw_jury", "commit_vote", "reveal_vote",
            "finalize_jury", "withdraw"})


if __name__ == "__main__":
    unittest.main()
