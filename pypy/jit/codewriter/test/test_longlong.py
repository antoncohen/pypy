import py, sys

from pypy.rlib.rarithmetic import r_longlong
from pypy.objspace.flow.model import SpaceOperation, Variable, Constant
from pypy.translator.unsimplify import varoftype
from pypy.rpython.lltypesystem import lltype
from pypy.jit.codewriter.jtransform import Transformer, NotSupported
from pypy.jit.codewriter.effectinfo import EffectInfo
from pypy.jit.codewriter.test.test_jtransform import const


class FakeRTyper:
    pass

class FakeBuiltinCallControl:
    def guess_call_kind(self, op):
        return 'builtin'
    def getcalldescr(self, op, oopspecindex=None):
        assert oopspecindex is not None    # in this test
        return 'calldescr-%d' % oopspecindex
    def calldescr_canraise(self, calldescr):
        return False

class FakeCPU:
    def __init__(self):
        self.supports_longlong = []
        self.rtyper = FakeRTyper()


class TestLongLong:
    def setup_class(cls):
        if sys.maxint > 2147483647:
            py.test.skip("only for 32-bit platforms")

    def do_check(self, opname, oopspecindex, ARGS, RESULT):
        vlist = [varoftype(ARG) for ARG in ARGS]
        v_result = varoftype(RESULT)
        op = SpaceOperation(opname, vlist, v_result)
        tr = Transformer(FakeCPU(), FakeBuiltinCallControl())
        [op1] = tr.rewrite_operation(op)
        #
        def is_llf(TYPE):
            return (TYPE == lltype.SignedLongLong or
                    TYPE == lltype.UnsignedLongLong or
                    TYPE == lltype.Float)
        if is_llf(RESULT):
            assert op1.opname == 'residual_call_irf_f'
        else:
            assert op1.opname == 'residual_call_irf_i'
        gotindex = getattr(EffectInfo, 'OS_' + op1.args[0].value.upper())
        assert gotindex == oopspecindex
        assert op1.args[1] == 'calldescr-%d' % oopspecindex
        assert list(op1.args[2]) == [v for v in vlist
                                     if not is_llf(v.concretetype)]
        assert list(op1.args[3]) == []
        assert list(op1.args[4]) == [v for v in vlist
                                     if is_llf(v.concretetype)]
        assert op1.result == v_result

    def test_is_true(self):
        self.do_check('llong_is_true', EffectInfo.OS_LLONG_IS_TRUE,
                      [lltype.SignedLongLong], lltype.Bool)
        self.do_check('ullong_is_true', EffectInfo.OS_LLONG_IS_TRUE,
                      [lltype.SignedLongLong], lltype.Bool)

    def test_unary_op(self):
        for opname, oopspecindex in [
                ('llong_neg',      EffectInfo.OS_LLONG_NEG),
                ('llong_invert',   EffectInfo.OS_LLONG_INVERT),
                ('ullong_invert',  EffectInfo.OS_LLONG_INVERT),
                ]:
            if opname.startswith('u'):
                T = lltype.UnsignedLongLong
            else:
                T = lltype.SignedLongLong
            self.do_check(opname, oopspecindex, [T], T)

    def test_comparison(self):
        for opname, oopspecindex in [
                ('llong_lt',  EffectInfo.OS_LLONG_LT),
                ('llong_le',  EffectInfo.OS_LLONG_LE),
                ('llong_eq',  EffectInfo.OS_LLONG_EQ),
                ('llong_ne',  EffectInfo.OS_LLONG_NE),
                ('llong_gt',  EffectInfo.OS_LLONG_GT),
                ('llong_ge',  EffectInfo.OS_LLONG_GE),
                ('ullong_lt', EffectInfo.OS_LLONG_ULT),
                ('ullong_le', EffectInfo.OS_LLONG_ULE),
                ('ullong_eq', EffectInfo.OS_LLONG_EQ),
                ('ullong_ne', EffectInfo.OS_LLONG_NE),
                ('ullong_gt', EffectInfo.OS_LLONG_UGT),
                ('ullong_ge', EffectInfo.OS_LLONG_UGE),
                ]:
            if opname.startswith('u'):
                T = lltype.UnsignedLongLong
            else:
                T = lltype.SignedLongLong
            self.do_check(opname, oopspecindex, [T, T], lltype.Bool)

    def test_binary_op(self):
        for opname, oopspecindex in [
                ('llong_add',    EffectInfo.OS_LLONG_ADD),
                ('llong_sub',    EffectInfo.OS_LLONG_SUB),
                ('llong_mul',    EffectInfo.OS_LLONG_MUL),
                ('llong_and',    EffectInfo.OS_LLONG_AND),
                ('llong_or',     EffectInfo.OS_LLONG_OR),
                ('llong_xor',    EffectInfo.OS_LLONG_XOR),
                ('ullong_add',   EffectInfo.OS_LLONG_ADD),
                ('ullong_sub',   EffectInfo.OS_LLONG_SUB),
                ('ullong_mul',   EffectInfo.OS_LLONG_MUL),
                ('ullong_and',   EffectInfo.OS_LLONG_AND),
                ('ullong_or',    EffectInfo.OS_LLONG_OR),
                ('ullong_xor',   EffectInfo.OS_LLONG_XOR),
                ]:
            if opname.startswith('u'):
                T = lltype.UnsignedLongLong
            else:
                T = lltype.SignedLongLong
            self.do_check(opname, oopspecindex, [T, T], T)

    def test_shifts(self):
        for opname, oopspecindex in [
                ('llong_lshift',  EffectInfo.OS_LLONG_LSHIFT),
                ('llong_rshift',  EffectInfo.OS_LLONG_RSHIFT),
                ('ullong_lshift', EffectInfo.OS_LLONG_LSHIFT),
                ('ullong_rshift', EffectInfo.OS_LLONG_URSHIFT),
                ]:
            if opname.startswith('u'):
                T = lltype.UnsignedLongLong
            else:
                T = lltype.SignedLongLong
            self.do_check(opname, oopspecindex, [T, lltype.Signed], T)

    def test_casts(self):
        self.do_check('cast_int_to_longlong', EffectInfo.OS_LLONG_FROM_INT,
                      [lltype.Signed], lltype.SignedLongLong)
        self.do_check('truncate_longlong_to_int', EffectInfo.OS_LLONG_TO_INT,
                      [lltype.SignedLongLong], lltype.Signed)
        self.do_check('cast_float_to_longlong', EffectInfo.OS_LLONG_FROM_FLOAT,
                      [lltype.Float], lltype.SignedLongLong)
        self.do_check('cast_longlong_to_float', EffectInfo.OS_LLONG_TO_FLOAT,
                      [lltype.SignedLongLong], lltype.Float)

    def test_prebuilt_constant_32(self):
        c_x = const(r_longlong(-171))
        v_y = varoftype(lltype.SignedLongLong)
        v_z = varoftype(lltype.SignedLongLong)
        op = SpaceOperation('llong_add', [c_x, v_y], v_z)
        tr = Transformer(FakeCPU(), FakeBuiltinCallControl())
        oplist = tr.rewrite_operation(op)
        assert len(oplist) == 2
        assert oplist[0].opname == 'residual_call_irf_f'
        assert oplist[0].args[0].value == 'llong_from_int'
        assert oplist[0].args[1] == 'calldescr-84'
        assert list(oplist[0].args[2]) == [const(-171)]
        assert list(oplist[0].args[3]) == []
        assert list(oplist[0].args[4]) == []
        v_x = oplist[0].result
        assert isinstance(v_x, Variable)
        assert oplist[1].opname == 'residual_call_irf_f'
        assert oplist[1].args[0].value == 'llong_add'
        assert oplist[1].args[1] == 'calldescr-70'
        assert list(oplist[1].args[2]) == []
        assert list(oplist[1].args[3]) == []
        assert list(oplist[1].args[4]) == [v_x, v_y]
        assert oplist[1].result == v_z

    def test_prebuilt_constant_64(self):
        py.test.skip("in-progress")
        c_x = const(r_longlong(3000000000))
        v_y = varoftype(lltype.SignedLongLong)
        v_z = varoftype(lltype.SignedLongLong)
        op = SpaceOperation('llong_add', [c_x, v_y], v_z)
        tr = Transformer(FakeCPU(), FakeBuiltinCallControl())
        oplist = tr.rewrite_operation(op)
        xxx
