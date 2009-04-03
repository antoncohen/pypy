
class ResOperation(object):
    """The central ResOperation class, representing one operation."""

    # for 'jump': points to the target loop;
    jump_target = None

    # for 'guard_*'
    suboperations = None
    optimized = None

    # for 'guard_nonvirtualizable'
    vdesc = None

    # for x86 backend
    liveboxes = None

    def __init__(self, opnum, args, result, descr=None):
        assert isinstance(opnum, int)
        self.opnum = opnum
        self.args = list(args)
        assert not isinstance(result, list)
        self.result = result
        self.setdescr(descr)

    def setdescr(self, descr):
        # for 'call', 'new', 'getfield_gc'...: the descr is a number provided
        # by the backend holding details about the type of the operation --
        # actually an instance of a class, typically Descr, that inherits
        # from AbstractDescr
        from pypy.jit.metainterp.history import check_descr
        check_descr(descr)
        self.descr = descr

    def clone(self):
        res = ResOperation(self.opnum, self.args, self.result, self.descr)
        res.jump_target = self.jump_target
        res.vdesc = self.vdesc
        return res

    def __repr__(self):
        return self.repr()

    def repr(self):
        # RPython-friendly version
        if self.result is not None:
            sres = '%s = ' % (self.result,)
        else:
            sres = ''
        return '%s%s(%s)' % (sres, self.getopname(),
                             ', '.join([str(a) for a in self.args]))

    def getopname(self):
        try:
            return opname[self.opnum].lower()
        except KeyError:
            return '<%d>' % self.opnum

    def is_guard(self):
        return rop._GUARD_FIRST <= self.opnum <= rop._GUARD_LAST

    def is_always_pure(self):
        return rop._ALWAYS_PURE_FIRST <= self.opnum <= rop._ALWAYS_PURE_LAST

    def has_no_side_effect(self):
        return rop._NOSIDEEFFECT_FIRST <= self.opnum <= rop._NOSIDEEFFECT_LAST

    def can_raise(self):
        return rop._CANRAISE_FIRST <= self.opnum <= rop._CANRAISE_LAST

    def is_ovf(self):
        return rop._OVF_FIRST <= self.opnum <= rop._OVF_LAST

    def is_comparison(self):
        return rop._COMPARISON_FIRST <= self.opnum <= rop._COMPARISON_LAST

    def is_final(self):
        return rop._FINAL_FIRST <= self.opnum <= rop._FINAL_LAST

# ____________________________________________________________


class rop(object):
    """The possible names of the ResOperations."""

    _FINAL_FIRST = 1
    JUMP                   = 1
    FAIL                   = 2
    #RAISE                  = 3
    _FINAL_LAST = 9

    _GUARD_FIRST = 10 # ----- start of guard operations -----
    GUARD_TRUE             = 10
    GUARD_FALSE            = 11
    GUARD_VALUE            = 12
    GUARD_CLASS            = 13
    GUARD_NONVIRTUALIZED   = 14
    GUARD_NO_EXCEPTION     = 15
    GUARD_EXCEPTION        = 16
    _GUARD_LAST = 19 # ----- end of guard operations -----

    _NOSIDEEFFECT_FIRST = 20 # ----- start of no_side_effect operations -----
    _ALWAYS_PURE_FIRST = 20 # ----- start of always_pure operations -----
    CALL_PURE              = 20
    #
    INT_ADD                = 30
    INT_SUB                = 31
    INT_MUL                = 32
    INT_FLOORDIV           = 33
    INT_MOD                = 34
    INT_AND                = 35
    INT_OR                 = 36
    INT_XOR                = 37
    INT_RSHIFT             = 38
    INT_LSHIFT             = 39
    UINT_ADD               = 40
    UINT_SUB               = 41
    UINT_MUL               = 42
    UINT_LSHIFT            = 43
    UINT_RSHIFT            = 44
    #
    _COMPARISON_FIRST = 45
    INT_LT                 = 45
    INT_LE                 = 46
    INT_EQ                 = 47
    INT_NE                 = 48
    INT_GT                 = 49
    INT_GE                 = 50
    UINT_LT                = 51
    UINT_LE                = 52
    UINT_EQ                = 53
    UINT_NE                = 54
    UINT_GT                = 55
    UINT_GE                = 56
    _COMPARISON_LAST = 56
    #
    INT_IS_TRUE            = 60
    INT_NEG                = 61
    INT_INVERT             = 62
    BOOL_NOT               = 63
    UINT_IS_TRUE           = 64
    #
    OONONNULL              = 70
    OOISNULL               = 71
    OOIS                   = 72
    OOISNOT                = 73
    #
    ARRAYLEN_GC            = 77
    STRLEN                 = 78
    STRGETITEM             = 79
    GETFIELD_GC_PURE       = 80
    GETFIELD_RAW_PURE      = 81
    GETARRAYITEM_GC_PURE   = 82
    _ALWAYS_PURE_LAST = 82  # ----- end of always_pure operations -----

    GETARRAYITEM_GC        = 83
    GETFIELD_GC            = 84
    GETFIELD_RAW           = 85
    _NOSIDEEFFECT_LAST = 89 # ----- end of no_side_effect operations -----

    NEW                    = 90
    NEW_WITH_VTABLE        = 91
    NEW_ARRAY              = 92
    SETARRAYITEM_GC        = 93
    SETFIELD_GC            = 94
    SETFIELD_RAW           = 95
    NEWSTR                 = 96
    STRSETITEM             = 97

    _CANRAISE_FIRST = 100 # ----- start of can_raise operations -----
    CALL = 100
    #
    _OVF_FIRST = 110
    INT_ADD_OVF            = 110
    INT_SUB_OVF            = 111
    INT_MUL_OVF            = 112
    INT_NEG_OVF            = 113
    INT_MOD_OVF            = 114
    _OVF_LAST = 114
    _CANRAISE_LAST = 119 # ----- end of can_raise operations -----
    _LAST = 119     # for the backend to add more internal operations


opname = {}      # mapping numbers to the original names, for debugging
for _key, _value in rop.__dict__.items():
    if type(_value) is int and _key.isupper() and not _key.startswith('_'):
        assert _value not in opname, "collision! %s and %s" % (
            opname[_value], _key)
        opname[_value] = _key
