"""This implements pyjitpl's execution of operations.
"""

import py
from pypy.rpython.lltypesystem import lltype
from pypy.rpython.ootypesystem import ootype
from pypy.rpython.lltypesystem.lloperation import llop
from pypy.rlib.rarithmetic import ovfcheck, r_uint, intmask
from pypy.jit.metainterp.history import BoxInt, ConstInt, check_descr
from pypy.jit.metainterp.history import INT, REF, ConstFloat
from pypy.jit.metainterp.resoperation import rop


# Operations in the _ALWAYS_PURE part of the table of resoperation.py
# must return a ConstInt or ConstPtr.  Other operations must return
# a BoxInt or BoxPtr or None.

# ____________________________________________________________

def do_int_add(cpu, args, descr=None):
    return ConstInt(intmask(args[0].getint() + args[1].getint()))

def do_int_sub(cpu, args, descr=None):
    return ConstInt(intmask(args[0].getint() - args[1].getint()))

def do_int_mul(cpu, args, descr=None):
    return ConstInt(intmask(args[0].getint() * args[1].getint()))

def do_int_floordiv(cpu, args, descr=None):
    z = llop.int_floordiv(lltype.Signed, args[0].getint(), args[1].getint())
    return ConstInt(z)

def do_int_mod(cpu, args, descr=None):
    z = llop.int_mod(lltype.Signed, args[0].getint(), args[1].getint())
    return ConstInt(z)

def do_int_and(cpu, args, descr=None):
    return ConstInt(args[0].getint() & args[1].getint())

def do_int_or(cpu, args, descr=None):
    return ConstInt(args[0].getint() | args[1].getint())

def do_int_xor(cpu, args, descr=None):
    return ConstInt(args[0].getint() ^ args[1].getint())

def do_int_rshift(cpu, args, descr=None):
    return ConstInt(args[0].getint() >> args[1].getint())

def do_int_lshift(cpu, args, descr=None):
    return ConstInt(intmask(args[0].getint() << args[1].getint()))

def do_uint_rshift(cpu, args, descr=None):
    v = r_uint(args[0].getint()) >> r_uint(args[1].getint())
    return ConstInt(intmask(v))

# ----------

def do_int_lt(cpu, args, descr=None):
    return ConstInt(args[0].getint() < args[1].getint())

def do_int_le(cpu, args, descr=None):
    return ConstInt(args[0].getint() <= args[1].getint())

def do_int_eq(cpu, args, descr=None):
    return ConstInt(args[0].getint() == args[1].getint())

def do_int_ne(cpu, args, descr=None):
    return ConstInt(args[0].getint() != args[1].getint())

def do_int_gt(cpu, args, descr=None):
    return ConstInt(args[0].getint() > args[1].getint())

def do_int_ge(cpu, args, descr=None):
    return ConstInt(args[0].getint() >= args[1].getint())

def do_uint_lt(cpu, args, descr=None):
    return ConstInt(r_uint(args[0].getint()) < r_uint(args[1].getint()))

def do_uint_le(cpu, args, descr=None):
    return ConstInt(r_uint(args[0].getint()) <= r_uint(args[1].getint()))

def do_uint_gt(cpu, args, descr=None):
    return ConstInt(r_uint(args[0].getint()) > r_uint(args[1].getint()))

def do_uint_ge(cpu, args, descr=None):
    return ConstInt(r_uint(args[0].getint()) >= r_uint(args[1].getint()))

# ----------

def do_int_is_true(cpu, args, descr=None):
    return ConstInt(bool(args[0].getint()))

def do_int_neg(cpu, args, descr=None):
    return ConstInt(intmask(-args[0].getint()))

def do_int_invert(cpu, args, descr=None):
    return ConstInt(~args[0].getint())

def do_bool_not(cpu, args, descr=None):
    return ConstInt(not args[0].getint())

def do_same_as(cpu, args, descr=None):
    return args[0]

def do_oononnull(cpu, args, descr=None):
    tp = args[0].type
    if tp == INT:
        x = bool(args[0].getint())
    elif tp == REF:
        x = bool(args[0].getref_base())
    else:
        assert False
    return ConstInt(x)

def do_ooisnull(cpu, args, descr=None):
    tp = args[0].type
    if tp == INT:
        x = bool(args[0].getint())
    elif tp == REF:
        x = bool(args[0].getref_base())
    else:
        assert False
    return ConstInt(not x)

def do_oois(cpu, args, descr=None):
    tp = args[0].type
    assert tp == args[1].type
    if tp == INT:
        x = args[0].getint() == args[1].getint()
    elif tp == REF:
        x = args[0].getref_base() == args[1].getref_base()
    else:
        assert False
    return ConstInt(x)

def do_ooisnot(cpu, args, descr=None):
    tp = args[0].type
    assert tp == args[1].type
    if tp == INT:
        x = args[0].getint() != args[1].getint()
    elif tp == REF:
        x = args[0].getref_base() != args[1].getref_base()
    else:
        assert False
    return ConstInt(x)

def do_ooidentityhash(cpu, args, descr=None):
    obj = args[0].getref_base()
    return ConstInt(ootype.ooidentityhash(obj))


def do_subclassof(self, args, descr=None):
    assert len(args) == 2
    box1, box2 = args
    cls1 = box1.getref(ootype.Class)
    cls2 = box2.getref(ootype.Class)
    res = ootype.subclassof(cls1, cls2)
    return BoxInt(res)


# ----------
# the following operations just delegate to the cpu:

#   do_arraylen_gc
#   do_strlen
#   do_strgetitem
#   do_getarrayitem_gc
#   do_getfield_gc
#   do_getfield_raw
#   do_new
#   do_new_with_vtable
#   do_new_array
#   do_setarrayitem_gc
#   do_setfield_gc
#   do_setfield_raw
#   do_newstr
#   do_strsetitem
#   do_call

# ----------

def do_int_add_ovf(cpu, args, descr=None):
    x = args[0].getint()
    y = args[1].getint()
    try:
        z = ovfcheck(x + y)
    except OverflowError:
        ovf = True
        z = 0
    else:
        ovf = False
    cpu._overflow_flag = ovf
    return BoxInt(z)

def do_int_sub_ovf(cpu, args, descr=None):
    x = args[0].getint()
    y = args[1].getint()
    try:
        z = ovfcheck(x - y)
    except OverflowError:
        ovf = True
        z = 0
    else:
        ovf = False
    cpu._overflow_flag = ovf
    return BoxInt(z)

def do_int_mul_ovf(cpu, args, descr=None):
    x = args[0].getint()
    y = args[1].getint()
    try:
        z = ovfcheck(x * y)
    except OverflowError:
        ovf = True
        z = 0
    else:
        ovf = False
    cpu._overflow_flag = ovf
    return BoxInt(z)

# ----------

def do_float_neg(cpu, args, descr=None):
    return ConstFloat(-args[0].getfloat())

def do_float_abs(cpu, args, descr=None):
    return ConstFloat(abs(args[0].getfloat()))

def do_float_is_true(cpu, args, descr=None):
    return ConstInt(bool(args[0].getfloat()))

def do_float_add(cpu, args, descr=None):
    return ConstFloat(args[0].getfloat() + args[1].getfloat())

def do_float_sub(cpu, args, descr=None):
    return ConstFloat(args[0].getfloat() - args[1].getfloat())

def do_float_mul(cpu, args, descr=None):
    return ConstFloat(args[0].getfloat() * args[1].getfloat())

def do_float_truediv(cpu, args, descr=None):
    return ConstFloat(args[0].getfloat() / args[1].getfloat())

def do_float_lt(cpu, args, descr=None):
    return ConstInt(args[0].getfloat() < args[1].getfloat())

def do_float_le(cpu, args, descr=None):
    return ConstInt(args[0].getfloat() <= args[1].getfloat())

def do_float_eq(cpu, args, descr=None):
    return ConstInt(args[0].getfloat() == args[1].getfloat())

def do_float_ne(cpu, args, descr=None):
    return ConstInt(args[0].getfloat() != args[1].getfloat())

def do_float_gt(cpu, args, descr=None):
    return ConstInt(args[0].getfloat() > args[1].getfloat())

def do_float_ge(cpu, args, descr=None):
    return ConstInt(args[0].getfloat() >= args[1].getfloat())

def do_cast_float_to_int(cpu, args, descr=None):
    return ConstInt(int(args[0].getfloat()))

def do_cast_int_to_float(cpu, args, descr=None):
    return ConstFloat(float(args[0].getint()))

# ____________________________________________________________

def do_debug_merge_point(cpu, args, descr=None):
    from pypy.jit.metainterp.warmspot import get_stats
    loc = args[0]._get_str()
    get_stats().locations.append(loc)

# ____________________________________________________________


def make_execute_list(cpuclass):
    from pypy.jit.backend.model import AbstractCPU
    if 0:     # enable this to trace calls to do_xxx
        def wrap(fn):
            def myfn(*args):
                print '<<<', fn.__name__
                try:
                    return fn(*args)
                finally:
                    print fn.__name__, '>>>'
            return myfn
    else:
        def wrap(fn):
            return fn
    execute = [None] * (rop._LAST+1)
    for key, value in rop.__dict__.items():
        if not key.startswith('_'):
            if (rop._FINAL_FIRST <= value <= rop._FINAL_LAST or
                rop._GUARD_FIRST <= value <= rop._GUARD_LAST):
                continue
            if execute[value] is not None:
                raise Exception("duplicate entry for op number %d" % value)
            if key.endswith('_PURE'):
                key = key[:-5]
            name = 'do_' + key.lower()
            if hasattr(cpuclass, name):
                execute[value] = wrap(getattr(cpuclass, name))
            elif name in globals():
                execute[value] = wrap(globals()[name])
            else:
                assert hasattr(AbstractCPU, name), name
    cpuclass._execute_list = execute

def get_execute_function(cpu, opnum):
    # workaround for an annotation limitation: putting this code in
    # a specialize:memo function makes sure the following line is
    # constant-folded away.  Only works if opnum is a constant, of course.
    return cpu._execute_list[opnum]
get_execute_function._annspecialcase_ = 'specialize:memo'

def execute(cpu, opnum, argboxes, descr=None):
    check_descr(descr)
    func = get_execute_function(cpu, opnum)
    assert func is not None
    return func(cpu, argboxes, descr)
execute._annspecialcase_ = 'specialize:arg(1)'

def _execute_nonspec(cpu, opnum, argboxes, descr=None):
    check_descr(descr)
    func = cpu._execute_list[opnum]
    return func(cpu, argboxes, descr)
