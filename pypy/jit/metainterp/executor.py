"""This implements pyjitpl's execution of operations.
"""

import py
from pypy.rlib.rarithmetic import ovfcheck, r_uint, intmask
from pypy.jit.metainterp.history import BoxInt, ConstInt
from pypy.jit.metainterp.resoperation import rop


# Operations in the _ALWAYS_PURE part of the table of resoperation.py
# must return a ConstInt or ConstPtr.  Other operations must return
# a BoxInt or BoxPtr or None.

# ____________________________________________________________

def do_int_add(cpu, args, descr=None):
    return ConstInt(args[0].getint() + args[1].getint())

def do_int_sub(cpu, args, descr=None):
    return ConstInt(args[0].getint() - args[1].getint())

def do_int_mul(cpu, args, descr=None):
    return ConstInt(args[0].getint() * args[1].getint())

def do_int_floordiv(cpu, args, descr=None):
    return ConstInt(args[0].getint() // args[1].getint())

def do_int_mod(cpu, args, descr=None):
    return ConstInt(args[0].getint() % args[1].getint())

def do_int_and(cpu, args, descr=None):
    return ConstInt(args[0].getint() & args[1].getint())

def do_int_or(cpu, args, descr=None):
    return ConstInt(args[0].getint() | args[1].getint())

def do_int_xor(cpu, args, descr=None):
    return ConstInt(args[0].getint() ^ args[1].getint())

def do_int_rshift(cpu, args, descr=None):
    return ConstInt(args[0].getint() >> args[1].getint())

def do_int_lshift(cpu, args, descr=None):
    return ConstInt(args[0].getint() << args[1].getint())

do_uint_add = do_int_add
do_uint_sub = do_int_sub
do_uint_mul = do_int_mul
do_uint_lshift = do_int_lshift

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

do_uint_eq = do_int_eq
do_uint_ne = do_int_ne

def do_uint_gt(cpu, args, descr=None):
    return ConstInt(r_uint(args[0].getint()) > r_uint(args[1].getint()))

def do_uint_ge(cpu, args, descr=None):
    return ConstInt(r_uint(args[0].getint()) >= r_uint(args[1].getint()))

# ----------

def do_int_is_true(cpu, args, descr=None):
    return ConstInt(bool(args[0].getint()))

do_uint_is_true = do_int_is_true

def do_int_neg(cpu, args, descr=None):
    return ConstInt(-args[0].getint())

def do_int_invert(cpu, args, descr=None):
    return ConstInt(~args[0].getint())

def do_bool_not(cpu, args, descr=None):
    return ConstInt(not args[0].getint())

def do_oononnull(cpu, args, descr=None):
    return ConstInt(bool(args[0].getptr_base()))

def do_ooisnull(cpu, args, descr=None):
    return ConstInt(not args[0].getptr_base())

def do_oois(cpu, args, descr=None):
    return ConstInt(args[0].getptr_base() == args[1].getptr_base())

def do_ooisnot(cpu, args, descr=None):
    return ConstInt(args[0].getptr_base() != args[1].getptr_base())

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
        cpu.set_overflow_error()
        z = 0
    return BoxInt(z)

def do_int_sub_ovf(cpu, args, descr=None):
    x = args[0].getint()
    y = args[1].getint()
    try:
        z = ovfcheck(x - y)
    except OverflowError:
        cpu.set_overflow_error()
        z = 0
    return BoxInt(z)

def do_int_mul_ovf(cpu, args, descr=None):
    x = args[0].getint()
    y = args[1].getint()
    try:
        z = ovfcheck(x * y)
    except OverflowError:
        cpu.set_overflow_error()
        z = 0
    return BoxInt(z)

def do_int_neg_ovf(cpu, args, descr=None):
    x = args[0].getint()
    try:
        z = ovfcheck(-x)
    except OverflowError:
        cpu.set_overflow_error()
        z = 0
    return BoxInt(z)

def do_int_mod_ovf(cpu, args, descr=None):
    x = args[0].getint()
    y = args[1].getint()
    try:
        z = ovfcheck(x % y)
    except OverflowError:
        cpu.set_overflow_error()
        z = 0
    return BoxInt(z)

# ____________________________________________________________


def make_execute_list(cpuclass):
    execute = [None] * (rop._LAST+1)
    for key, value in rop.__dict__.items():
        if not key.startswith('_'):
            if (rop._SPECIAL_FIRST <= value <= rop._SPECIAL_LAST or
                rop._GUARD_FIRST <= value <= rop._GUARD_LAST):
                continue
            if execute[value] is not None:
                raise Exception("duplicate entry for op number %d" % value)
            if key.endswith('_PURE'):
                key = key[:-5]
            name = 'do_' + key.lower()
            try:
                execute[value] = getattr(cpuclass, name)
            except AttributeError:
                execute[value] = globals()[name]
    cpuclass._execute_list = execute

def get_execute_function(cpu, opnum):
    # workaround for an annotation limitation: putting this code in
    # a specialize:memo function makes sure the following line is
    # constant-folded away.  Only works if opnum is a constant, of course.
    return cpu._execute_list[opnum]
get_execute_function._annspecialcase_ = 'specialize:memo'

def execute(cpu, opnum, argboxes, descr=None):
    func = get_execute_function(cpu, opnum)
    return func(cpu, argboxes, descr)
execute._annspecialcase_ = 'specialize:arg(1)'
