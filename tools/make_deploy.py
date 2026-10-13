"""Builds contract_deploy.py from contract.py.

GenLayer Studio rejects deploy files with long comment or docstring blocks, so
the deploy file keeps only the two header lines and the code. Stripping is done
with the AST (docstrings removed, comments dropped by unparse), never with
regular expressions.

Usage:
    python3 tools/make_deploy.py            # writes contract_deploy.py
    python3 tools/make_deploy.py --testnet  # also writes contract_testnet_deploy.py
                                            # (short windows, class TokenHandoffTestnet)
"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "contract.py")
TARGET = os.path.join(ROOT, "contract_deploy.py")
TESTNET_TARGET = os.path.join(ROOT, "contract_testnet_deploy.py")
TESTNET_CONSTANTS = {
    "MIN_DEADLINE_LEAD_SECONDS": 30,
    "EVIDENCE_WINDOW_SECONDS": 5 * 60,
    "APPEAL_WINDOW_SECONDS": 10 * 60,
    "COMMIT_WINDOW_SECONDS": 3 * 60,
    "REVEAL_WINDOW_SECONDS": 3 * 60,
    "EVIDENCE_GRACE_SECONDS": 2 * 60,
    "RESOLUTION_TIMEOUT_SECONDS": 20 * 60,
}

HEADER = (
    "# v0.2.16",
    '# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }',
)


def strip_docstrings(tree: ast.AST) -> ast.AST:
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                node.body = body[1:] or [ast.Pass()]
    return tree


def to_testnet(tree: ast.AST) -> ast.AST:
    """Short windows for quick live testing; same logic, class renamed."""
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "TokenHandoff":
            node.name = "TokenHandoffTestnet"
            for stmt in node.body:
                if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 \
                        and isinstance(stmt.targets[0], ast.Name) and stmt.targets[0].id in TESTNET_CONSTANTS:
                    stmt.value = ast.Constant(TESTNET_CONSTANTS[stmt.targets[0].id])
    return tree


def build(source_text: str, testnet: bool = False) -> str:
    lines = source_text.split("\n")
    if tuple(lines[:2]) != HEADER:
        raise SystemExit("contract.py must start with the two exact header lines")
    tree = strip_docstrings(ast.parse(source_text))
    if testnet:
        tree = to_testnet(tree)
    ast.fix_missing_locations(tree)
    body = ast.unparse(tree)
    compile(body, "contract_deploy.py", "exec")
    return "\n".join(HEADER) + "\n" + body + "\n"


def main() -> int:
    with open(SOURCE, encoding="utf-8") as fh:
        source = fh.read()
    jobs = [(TARGET, False)]
    if "--testnet" in sys.argv[1:]:
        jobs.append((TESTNET_TARGET, True))
    for target, testnet in jobs:
        out = build(source, testnet)
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(out)
        print(f"wrote {target} ({len(out.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
