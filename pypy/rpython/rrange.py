from pypy.annotation.pairtype import pairtype
from pypy.annotation import model as annmodel
from pypy.rpython.rmodel import Repr, TyperError, IntegerRepr
from pypy.rpython.lltype import Ptr, GcStruct, Signed, malloc, Void
from pypy.objspace.flow.model import Constant

# ____________________________________________________________
#
#  Concrete implementation of RPython lists that are returned by range()
#  and never mutated afterwards:
#
#    struct range {
#        Signed start, stop;    // step is always constant
#    }

RANGE = GcStruct("range", ("start", Signed), ("stop", Signed))
RANGEITER = GcStruct("range", ("next", Signed), ("stop", Signed))


class RangeRepr(Repr):
    lowleveltype = Ptr(RANGE)

    def __init__(self, step):
        self.step = step

    def rtype_len(self, hop):
        v_rng, = hop.inputargs(self)
        cstep = hop.inputconst(Signed, self.step)
        return hop.gendirectcall(ll_rangelen, v_rng, cstep)

    def make_iterator_repr(self):
        return RangeIteratorRepr(self)

class __extend__(pairtype(RangeRepr, IntegerRepr)):

    def rtype_getitem((r_rng, r_int), hop):
        if hop.has_implicit_exception(IndexError):
            s = "getitem on range with try, except: block not supported."
            raise TyperError, s
        v_lst, v_index = hop.inputargs(r_rng, Signed)
        cstep = hop.inputconst(Signed, r_rng.step)
        if hop.args_s[1].nonneg:
            llfn = ll_rangeitem_nonneg
        else:
            llfn = ll_rangeitem
        hop.exception_is_here()
        return hop.gendirectcall(llfn, v_lst, v_index, cstep)

# ____________________________________________________________
#
#  Low-level methods.

def _ll_rangelen(start, stop, step):
    if step > 0:
        result = (stop - start + (step-1)) // step
    else:
        result = (start - stop - (step+1)) // (-step)
    if result < 0:
        result = 0
    return result

def ll_rangelen(l, step):
    return _ll_rangelen(l.start, l.stop, step)

def ll_rangeitem_nonneg(l, i, step):
    return l.start + i*step

def ll_rangeitem(l, i, step):
    if i < 0:
        length = ll_rangelen(l, step)
        i += length
    return l.start + i*step

# ____________________________________________________________
#
#  Irregular operations.

def ll_newrange(start, stop):
    l = malloc(RANGE)
    l.start = start
    l.stop = stop
    return l

def rtype_builtin_range(hop):
    vstep = hop.inputconst(Signed, 1)
    if hop.nb_args == 1:
        vstart = hop.inputconst(Signed, 0)
        vstop, = hop.inputargs(Signed)
    elif hop.nb_args == 2:
        vstart, vstop = hop.inputargs(Signed, Signed)
    else:
        vstart, vstop, vstep = hop.inputargs(Signed, Signed, Signed)
        assert isinstance(vstep, Constant)

    if isinstance(hop.r_result, RangeRepr):
        return hop.gendirectcall(ll_newrange, vstart, vstop)
    else:
        # cannot build a RANGE object, needs a real list
        raise TyperError("range() result used as a normal list: "
                         "XXX not implemented")
        #return hop.gendirectcall(ll_range2list, vstart, vstop, vstep)

rtype_builtin_xrange = rtype_builtin_range

def ll_range2list(start, stop, step):
    pass
# ____________________________________________________________
#
#  Iteration.

class RangeIteratorRepr(Repr):
    lowleveltype = Ptr(RANGEITER)

    def __init__(self, r_rng):
        self.r_rng = r_rng

    def newiter(self, hop):
        v_rng, = hop.inputargs(self.r_rng)
        citerptr = hop.inputconst(Void, self.lowleveltype)
        return hop.gendirectcall(ll_rangeiter, citerptr, v_rng)

    def rtype_next(self, hop):
        v_iter, = hop.inputargs(self)
        cstep = hop.inputconst(Signed, self.r_rng.step)
        if self.r_rng.step > 0:
            llfn = ll_rangenext_up
        else:
            llfn = ll_rangenext_down
        hop.has_implicit_exception(StopIteration) # record that we know about it
        hop.exception_is_here()
        return hop.gendirectcall(llfn, v_iter, cstep)

def ll_rangeiter(ITERPTR, rng):
    iter = malloc(ITERPTR.TO)
    iter.next = rng.start
    iter.stop = rng.stop
    return iter

def ll_rangenext_up(iter, step):
    next = iter.next
    if next >= iter.stop:
        raise StopIteration
    iter.next = next + step
    return next

def ll_rangenext_down(iter, step):
    next = iter.next
    if next <= iter.stop:
        raise StopIteration
    iter.next = next + step
    return next
