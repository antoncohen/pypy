
from pypy.interpreter.baseobjspace import Wrappable
from pypy.interpreter.error import operationerrfmt
from pypy.interpreter.typedef import TypeDef, GetSetProperty
from pypy.interpreter.gateway import interp2app, unwrap_spec
from pypy.module.micronumpy import interp_dtype, interp_ufuncs, support
from pypy.module.micronumpy.arrayimpl import create_implementation
from pypy.module.micronumpy.strides import find_shape_and_elems
from pypy.tool.sourcetools import func_with_new_name
from pypy.rlib import jit

def _find_shape(space, w_size):
    if space.isinstance_w(w_size, space.w_int):
        return [space.int_w(w_size)]
    shape = []
    for w_item in space.fixedview(w_size):
        shape.append(space.int_w(w_item))
    return shape

def scalar_w(space, dtype, w_object):
    arr = W_NDimArray([], dtype)
    arr.implementation.set_scalar_value(dtype.coerce(space, w_object))
    return arr

class W_NDimArray(Wrappable):
    def __init__(self, shape, dtype, buffer=0, offset=0, strides=None,
                 order='C'):
        if strides is not None or offset != 0 or buffer != 0:
            raise Exception("unsupported args")
        self.implementation = create_implementation(shape, dtype, order)

    @jit.unroll_safe
    def descr_get_shape(self, space):
        shape = self.get_shape()
        return space.newtuple([space.wrap(i) for i in shape])

    def get_shape(self):
        return self.implementation.get_shape()

    def descr_set_shape(self, space, w_new_shape):
        self.implementation = self.implementation.set_shape(
            _find_shape(space, w_new_shape))

    def get_dtype(self):
        return self.implementation.dtype

    def descr_get_dtype(self, space):
        return self.implementation.dtype

    def descr_get_ndim(self, space):
        return space.wrap(len(self.get_shape()))

    def descr_getitem(self, space, w_idx):
        if (isinstance(w_idx, W_NDimArray) and w_idx.get_shape() == self.get_shape() and
            w_idx.get_dtype().is_bool_type()):
            return self.getitem_filter(space, w_idx)
        return self.implementation.descr_getitem(space, w_idx)

    def descr_setitem(self, space, w_idx, w_value):
        if (isinstance(w_idx, W_NDimArray) and w_idx.shape == self.shape and
            w_idx.find_dtype().is_bool_type()):
            return self.setitem_filter(space, w_idx,
                                       support.convert_to_array(space, w_value))
        self.implementation.descr_setitem(space, w_idx, w_value)

    def create_iter(self):
        return self.implementation.create_iter()

    def is_scalar(self):
        return self.implementation.is_scalar()

    def descr_get_size(self, space):
        return space.wrap(support.product(self.implementation.get_shape()))

    def get_scalar_value(self):
        return self.implementation.get_scalar_value()

    def _binop_impl(ufunc_name):
        def impl(self, space, w_other, w_out=None):
            return getattr(interp_ufuncs.get(space), ufunc_name).call(space,
                                                        [self, w_other, w_out])
        return func_with_new_name(impl, "binop_%s_impl" % ufunc_name)

    descr_add = _binop_impl("add")

@unwrap_spec(offset=int)
def descr_new_array(space, w_subtype, w_shape, w_dtype=None, w_buffer=None,
                    offset=0, w_strides=None, w_order=None):
    dtype = space.interp_w(interp_dtype.W_Dtype,
          space.call_function(space.gettypefor(interp_dtype.W_Dtype), w_dtype))
    shape = _find_shape(space, w_shape)
    return W_NDimArray(shape, dtype)

W_NDimArray.typedef = TypeDef(
    "ndarray",
    __new__ = interp2app(descr_new_array),

    __add__ = interp2app(W_NDimArray.descr_add),

    __getitem__ = interp2app(W_NDimArray.descr_getitem),
    __setitem__ = interp2app(W_NDimArray.descr_setitem),

    dtype = GetSetProperty(W_NDimArray.descr_get_dtype),
    shape = GetSetProperty(W_NDimArray.descr_get_shape,
                           W_NDimArray.descr_set_shape),
    ndim = GetSetProperty(W_NDimArray.descr_get_ndim),
    size = GetSetProperty(W_NDimArray.descr_get_size),
)

def decode_w_dtype(space, w_dtype):
    if w_dtype is None or space.is_w(w_dtype, space.w_None):
        return None
    return space.interp_w(interp_dtype.W_Dtype,
          space.call_function(space.gettypefor(interp_dtype.W_Dtype), w_dtype))

@unwrap_spec(ndmin=int, copy=bool, subok=bool)
def array(space, w_object, w_dtype=None, copy=True, w_order=None, subok=False,
          ndmin=0):
    if not space.issequence_w(w_object):
        if w_dtype is None or space.is_w(w_dtype, space.w_None):
            w_dtype = interp_ufuncs.find_dtype_for_scalar(space, w_object)
        dtype = space.interp_w(interp_dtype.W_Dtype,
          space.call_function(space.gettypefor(interp_dtype.W_Dtype), w_dtype))
        return scalar_w(space, dtype, w_object)
    if w_order is None or space.is_w(w_order, space.w_None):
        order = 'C'
    else:
        order = space.str_w(w_order)
        if order != 'C':  # or order != 'F':
            raise operationerrfmt(space.w_ValueError, "Unknown order: %s",
                                  order)
    if isinstance(w_object, W_NDimArray):
        if (not space.is_w(w_dtype, space.w_None) and
            w_object.dtype is not w_dtype):
            raise operationerrfmt(space.w_NotImplementedError,
                                  "copying over different dtypes unsupported")
        if copy:
            return w_object.copy(space)
        return w_object
    dtype = decode_w_dtype(space, w_dtype)
    shape, elems_w = find_shape_and_elems(space, w_object, dtype)
    if dtype is None:
        for w_elem in elems_w:
            dtype = interp_ufuncs.find_dtype_for_scalar(space, w_elem,
                                                        dtype)
            if dtype is interp_dtype.get_dtype_cache(space).w_float64dtype:
                break
        if dtype is None:
            dtype = interp_dtype.get_dtype_cache(space).w_float64dtype
    if ndmin > len(shape):
        shape = [1] * (ndmin - len(shape)) + shape
    arr = W_NDimArray(shape, dtype, order=order)
    arr_iter = arr.create_iter()
    for w_elem in elems_w:
        arr_iter.setitem(dtype.coerce(space, w_elem))
        arr_iter.next()
    return arr

@unwrap_spec(order=str)
def zeros(space, w_shape, w_dtype=None, order='C'):
    dtype = space.interp_w(interp_dtype.W_Dtype,
        space.call_function(space.gettypefor(interp_dtype.W_Dtype), w_dtype)
    )
    shape = _find_shape(space, w_shape)
    if not shape:
        return scalar_w(space, dtype, space.wrap(0))
    return space.wrap(W_NDimArray(shape, dtype=dtype, order=order))

def ones(space):
    pass

def dot(space):
    pass

def isna(space):
    pass

def concatenate(space):
    pass

def repeat(space):
    pass

def count_reduce_items(space):
    pass
