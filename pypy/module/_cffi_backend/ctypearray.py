"""
Arrays.
"""

from pypy.interpreter.error import OperationError, operationerrfmt
from pypy.interpreter.baseobjspace import Wrappable
from pypy.interpreter.gateway import interp2app
from pypy.interpreter.typedef import TypeDef
from pypy.rpython.lltypesystem import rffi
from pypy.rlib.objectmodel import keepalive_until_here
from pypy.rlib.rarithmetic import ovfcheck

from pypy.module._cffi_backend.ctypeobj import W_CType
from pypy.module._cffi_backend.ctypeprim import W_CTypePrimitiveChar
from pypy.module._cffi_backend.ctypeprim import W_CTypePrimitiveUniChar
from pypy.module._cffi_backend.ctypeptr import W_CTypePtrOrArray
from pypy.module._cffi_backend import cdataobj


class W_CTypeArray(W_CTypePtrOrArray):
    _attrs_            = ['length', 'ctptr']
    _immutable_fields_ = ['length', 'ctptr']

    def __init__(self, space, ctptr, length, arraysize, extra):
        W_CTypePtrOrArray.__init__(self, space, arraysize, extra, 0,
                                   ctptr.ctitem)
        self.length = length
        self.ctptr = ctptr

    def str(self, cdataobj):
        if isinstance(self.ctitem, W_CTypePrimitiveChar):
            s = rffi.charp2strn(cdataobj._cdata, cdataobj.get_array_length())
            keepalive_until_here(cdataobj)
            return self.space.wrap(s)
        return W_CTypePtrOrArray.str(self, cdataobj)

    def unicode(self, cdataobj):
        if isinstance(self.ctitem, W_CTypePrimitiveUniChar):
            s = rffi.wcharp2unicoden(rffi.cast(rffi.CWCHARP, cdataobj._cdata),
                                     cdataobj.get_array_length())
            keepalive_until_here(cdataobj)
            return self.space.wrap(s)
        return W_CTypePtrOrArray.unicode(self, cdataobj)

    def _alignof(self):
        return self.ctitem.alignof()

    def newp(self, w_init):
        space = self.space
        datasize = self.size
        #
        if datasize < 0:
            if (space.isinstance_w(w_init, space.w_list) or
                space.isinstance_w(w_init, space.w_tuple)):
                length = space.int_w(space.len(w_init))
            elif space.isinstance_w(w_init, space.w_basestring):
                # from a string, we add the null terminator
                length = space.int_w(space.len(w_init)) + 1
            else:
                length = space.getindex_w(w_init, space.w_OverflowError)
                if length < 0:
                    raise OperationError(space.w_ValueError,
                                         space.wrap("negative array length"))
                w_init = space.w_None
            #
            try:
                datasize = ovfcheck(length * self.ctitem.size)
            except OverflowError:
                raise OperationError(space.w_OverflowError,
                    space.wrap("array size would overflow a ssize_t"))
            #
            cdata = cdataobj.W_CDataNewOwningLength(space, datasize,
                                                    self, length)
        #
        else:
            cdata = cdataobj.W_CDataNewOwning(space, datasize, self)
        #
        if not space.is_w(w_init, space.w_None):
            self.convert_from_object(cdata._cdata, w_init)
            keepalive_until_here(cdata)
        return cdata

    def _check_subscript_index(self, w_cdata, i):
        space = self.space
        if i < 0:
            raise OperationError(space.w_IndexError,
                                 space.wrap("negative index not supported"))
        if i >= w_cdata.get_array_length():
            raise operationerrfmt(space.w_IndexError,
                "index too large for cdata '%s' (expected %d < %d)",
                self.name, i, w_cdata.get_array_length())
        return self

    def convert_from_object(self, cdata, w_ob):
        space = self.space
        if (space.isinstance_w(w_ob, space.w_list) or
            space.isinstance_w(w_ob, space.w_tuple)):
            lst_w = space.listview(w_ob)
            if self.length >= 0 and len(lst_w) > self.length:
                raise operationerrfmt(space.w_IndexError,
                    "too many initializers for '%s' (got %d)",
                                      self.name, len(lst_w))
            ctitem = self.ctitem
            for i in range(len(lst_w)):
                ctitem.convert_from_object(cdata, lst_w[i])
                cdata = rffi.ptradd(cdata, ctitem.size)
        elif isinstance(self.ctitem, W_CTypePrimitiveChar):
            try:
                s = space.str_w(w_ob)
            except OperationError, e:
                if not e.match(space, space.w_TypeError):
                    raise
                raise self._convert_error("str or list or tuple", w_ob)
            n = len(s)
            if self.length >= 0 and n > self.length:
                raise operationerrfmt(space.w_IndexError,
                                      "initializer string is too long for '%s'"
                                      " (got %d characters)",
                                      self.name, n)
            for i in range(n):
                cdata[i] = s[i]
            if n != self.length:
                cdata[n] = '\x00'
        elif isinstance(self.ctitem, W_CTypePrimitiveUniChar):
            try:
                s = space.unicode_w(w_ob)
            except OperationError, e:
                if not e.match(space, space.w_TypeError):
                    raise
                raise self._convert_error("unicode or list or tuple", w_ob)
            n = len(s)
            if self.length >= 0 and n > self.length:
                raise operationerrfmt(space.w_IndexError,
                              "initializer unicode string is too long for '%s'"
                                      " (got %d characters)",
                                      self.name, n)
            unichardata = rffi.cast(rffi.CWCHARP, cdata)
            for i in range(n):
                unichardata[i] = s[i]
            if n != self.length:
                unichardata[n] = u'\x00'
        else:
            raise self._convert_error("list or tuple", w_ob)

    def convert_to_object(self, cdata):
        return cdataobj.W_CData(self.space, cdata, self)

    def add(self, cdata, i):
        p = rffi.ptradd(cdata, i * self.ctitem.size)
        return cdataobj.W_CData(self.space, p, self.ctptr)

    def iter(self, cdata):
        return W_CDataIter(self.space, self.ctitem, cdata)


class W_CDataIter(Wrappable):
    _immutable_fields_ = ['ctitem', 'cdata', '_stop']    # but not '_next'

    def __init__(self, space, ctitem, cdata):
        self.space = space
        self.ctitem = ctitem
        self.cdata = cdata
        length = cdata.get_array_length()
        self._next = cdata._cdata
        self._stop = rffi.ptradd(cdata._cdata, length * ctitem.size)

    def iter_w(self):
        return self.space.wrap(self)

    def next_w(self):
        result = self._next
        if result == self._stop:
            raise OperationError(self.space.w_StopIteration, self.space.w_None)
        self._next = rffi.ptradd(result, self.ctitem.size)
        return self.ctitem.convert_to_object(result)

W_CDataIter.typedef = TypeDef(
    'CDataIter',
    __module__ = '_cffi_backend',
    __iter__ = interp2app(W_CDataIter.iter_w),
    next = interp2app(W_CDataIter.next_w),
    )
W_CDataIter.typedef.acceptable_as_base_class = False
