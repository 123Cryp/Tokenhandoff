"""Minimal offline stand-in for the `genlayer` SDK (test-only, not deployed).

It reproduces just enough of the SDK surface to import and exercise
contract.py in plain Python: storage containers, the contract base class,
decorators, the message context, EVM value transfers and the non-deterministic
helpers. Web access and model calls raise unless a test patches them. It does
not simulate real consensus, real networking or real models.
"""

__all__ = ["gl", "TreeMap", "u256", "DynArray", "i256", "bigint", "Address"]


class _Sub:
    def __class_getitem__(cls, item):
        return cls


class TreeMap(_Sub, dict):
    pass


class DynArray(_Sub, list):
    pass


class u256(int):
    pass


class i256(int):
    pass


class bigint(int):
    pass


class Address:
    def __init__(self, value):
        text = str(value)
        if not text.startswith("0x") or len(text) != 42:
            raise ValueError(f"invalid address: {text!r}")
        int(text[2:], 16)
        self._value = text

    def __str__(self):
        return self._value

    def __repr__(self):
        return f"Address({self._value!r})"

    def __eq__(self, other):
        return str(self).lower() == str(other).lower()

    def __hash__(self):
        return hash(str(self).lower())


class UserError(Exception):
    pass


class _Vm:
    UserError = UserError

    class Return:
        def __init__(self, calldata):
            self.calldata = calldata

    @staticmethod
    def run_nondet_unsafe(leader_fn, validator_fn, /):
        result = leader_fn()
        if not validator_fn(_Vm.Return(result)):
            raise UserError("validator rejected leader result")
        return result


class _Write:
    def __call__(self, fn):
        return fn

    @staticmethod
    def payable(fn):
        return fn


class _Public:
    write = _Write()

    @staticmethod
    def view(fn):
        return fn


class _Web:
    @staticmethod
    def render(url, mode="text"):
        raise NotImplementedError("gl.nondet.web.render must be patched in tests")


class _Nondet:
    web = _Web()

    @staticmethod
    def exec_prompt(prompt, response_format="text"):
        raise NotImplementedError("gl.nondet.exec_prompt must be patched in tests")


class _Eq:
    @staticmethod
    def strict_eq(fn):
        return fn()


class _Message:
    sender_address = None
    value = u256(10 ** 18)


class _Contract:
    def __new__(cls, *args, **kwargs):
        instance = super().__new__(cls)
        for klass in reversed(cls.__mro__):
            for name, annotation in vars(klass).get("__annotations__", {}).items():
                if isinstance(annotation, type) and issubclass(annotation, (TreeMap, DynArray)):
                    setattr(instance, name, annotation())
        instance.balance = u256(0)
        return instance

    def __init__(self, *args, **kwargs):
        pass


class _Proxy:
    def __init__(self, address):
        self.address = address

    def emit_transfer(self, *, value):
        _Evm.transfers.append({"to": str(self.address), "value": u256(value)})


class _Evm:
    transfers = []

    @staticmethod
    def contract_interface(cls):
        return _Proxy


class _GL:
    Contract = _Contract
    public = _Public()
    nondet = _Nondet()
    eq_principle = _Eq()
    vm = _Vm()
    message = _Message()
    evm = _Evm()


gl = _GL()
