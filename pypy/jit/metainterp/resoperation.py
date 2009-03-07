
class ResOperation(object):
    """The central ResOperation class, representing one operation."""

    # for 'merge_point'
    specnodes = None
    key = None

    # for 'jump' and 'guard_*'
    jump_target = None

    # for 'guard_*'
    counter = 0
    storage_info = None
    liveboxes = None

    # for 'guard_nonvirtualizable'
    vdesc = None

    def __init__(self, opnum, args, result, descr=0):
        assert isinstance(opnum, int)
        self.opnum = opnum
        self.args = list(args)
        assert not isinstance(result, list)
        self.result = result
        # for 'call', 'new', 'getfield_gc'...: the descr is a number provided
        # by the backend holding details about the type of the operation
        assert isinstance(descr, int)
        self.descr = descr

    def __repr__(self):
        result = self.repr()
        if self.liveboxes is not None:
            result = '%s [%s]' % (result, ', '.join(map(repr, self.liveboxes)))
        return result

    def repr(self):
        # RPython-friendly version
        if self.result is not None:
            sres = '%s = ' % (self.result,)
        else:
            sres = ''
        return '%s%s(%s)' % (sres, self.getopname(),
                             ', '.join([str(a) for a in self.args]))

    def clone(self):
        op = ResOperation(self.opnum, self.args, self.result, self.descr)
        op.specnodes = self.specnodes
        op.key = self.key
        return op

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

# ____________________________________________________________


class rop(object):
    """The possible names of the ResOperations."""

    _SPECIAL_FIRST = 1
    MERGE_POINT            = 1
    CATCH                  = 2
    JUMP                   = 3
    _SPECIAL_LAST = 9

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
    #
    _COMPARISON_FIRST = 44
    INT_LT                 = 44
    INT_LE                 = 45
    INT_EQ                 = 46
    INT_NE                 = 47
    INT_GT                 = 48
    INT_GE                 = 49
    UINT_LT                = 50
    UINT_LE                = 51
    UINT_EQ                = 52
    UINT_NE                = 53
    UINT_GT                = 54
    UINT_GE                = 55
    _COMPARISON_LAST = 55
    #
    INT_IS_TRUE            = 60
    INT_NEG                = 61
    INT_INVERT             = 62
    BOOL_NOT               = 63
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
