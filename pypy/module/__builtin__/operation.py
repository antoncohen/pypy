"""
Interp-level implementation of the basic space operations.
"""

from pypy.interpreter import gateway
from pypy.interpreter.error import OperationError
from pypy.interpreter.gateway import unwrap_spec, WrappedDefault
from rpython.rlib.runicode import UNICHR
from rpython.rlib.rfloat import isnan, isinf, round_double
from rpython.rlib import rfloat
import __builtin__

def abs(space, w_val):
    "abs(number) -> number\n\nReturn the absolute value of the argument."
    return space.abs(w_val)

def chr(space, w_ascii):
    "Return a string of one character with the given ascii code."
    try:
        char = __builtin__.chr(space.int_w(w_ascii))
    except ValueError:  # chr(out-of-range)
        raise OperationError(space.w_ValueError,
                             space.wrap("character code not in range(256)"))
    return space.wrap(char)

@unwrap_spec(code=int)
def unichr(space, code):
    "Return a Unicode string of one character with the given ordinal."
    # XXX range checking!
    try:
        c = UNICHR(code)
    except ValueError:
        raise OperationError(space.w_ValueError,
                             space.wrap("unichr() arg out of range"))
    return space.wrap(c)

def len(space, w_obj):
    "len(object) -> integer\n\nReturn the number of items of a sequence or mapping."
    return space.len(w_obj)


def checkattrname(space, w_name):
    # This is a check to ensure that getattr/setattr/delattr only pass a
    # string to the rest of the code.  XXX not entirely sure if these three
    # functions are the only way for non-string objects to reach
    # space.{get,set,del}attr()...
    # Note that if w_name is already an exact string it must be returned
    # unmodified (and not e.g. unwrapped-rewrapped).
    if not space.is_w(space.type(w_name), space.w_str):
        name = space.str_w(w_name)    # typecheck
        w_name = space.wrap(name)     # rewrap as a real string
    return w_name

def delattr(space, w_object, w_name):
    """Delete a named attribute on an object.
delattr(x, 'y') is equivalent to ``del x.y''."""
    w_name = checkattrname(space, w_name)
    space.delattr(w_object, w_name)
    return space.w_None

def getattr(space, w_object, w_name, w_defvalue=None):
    """Get a named attribute from an object.
getattr(x, 'y') is equivalent to ``x.y''."""
    w_name = checkattrname(space, w_name)
    try:
        return space.getattr(w_object, w_name)
    except OperationError, e:
        if w_defvalue is not None:
            if e.match(space, space.w_AttributeError):
                return w_defvalue
        raise

def hasattr(space, w_object, w_name):
    """Return whether the object has an attribute with the given name.
    (This is done by calling getattr(object, name) and catching exceptions.)"""
    w_name = checkattrname(space, w_name)
    if space.findattr(w_object, w_name) is not None:
        return space.w_True
    else:
        return space.w_False

def hash(space, w_object):
    """Return a hash value for the object.  Two objects which compare as
equal have the same hash value.  It is possible, but unlikely, for
two un-equal objects to have the same hash value."""
    return space.hash(w_object)

def oct(space, w_val):
    """Return the octal representation of an integer."""
    # XXX does this need to be a space operation?
    return space.oct(w_val)

def hex(space, w_val):
    """Return the hexadecimal representation of an integer."""
    return space.hex(w_val)

def id(space, w_object):
    "Return the identity of an object: id(x) == id(y) if and only if x is y."
    return space.id(w_object)

def cmp(space, w_x, w_y):
    """return 0 when x == y, -1 when x < y and 1 when x > y """
    return space.cmp(w_x, w_y)

def coerce(space, w_x, w_y):
    """coerce(x, y) -> (x1, y1)

Return a tuple consisting of the two numeric arguments converted to
a common type, using the same rules as used by arithmetic operations.
If coercion is not possible, raise TypeError."""
    return space.coerce(w_x, w_y)

def divmod(space, w_x, w_y):
    """Return the tuple ((x-x%y)/y, x%y).  Invariant: div*y + mod == x."""
    return space.divmod(w_x, w_y)

# semi-private: works only for new-style classes.
def _issubtype(space, w_cls1, w_cls2):
    return space.issubtype(w_cls1, w_cls2)

# ____________________________________________________________

# Here 0.30103 is an upper bound for log10(2)
NDIGITS_MAX = int((rfloat.DBL_MANT_DIG - rfloat.DBL_MIN_EXP) * 0.30103)
NDIGITS_MIN = -int((rfloat.DBL_MAX_EXP + 1) * 0.30103)

@unwrap_spec(number=float, w_ndigits = WrappedDefault(0))
def round(space, number, w_ndigits):
    """round(number[, ndigits]) -> floating point number

Round a number to a given precision in decimal digits (default 0 digits).
This always returns a floating point number.  Precision may be negative."""
    # Algorithm copied directly from CPython

    # interpret 2nd argument as a Py_ssize_t; clip on overflow
    ndigits = space.getindex_w(w_ndigits, None)

    # nans, infinities and zeros round to themselves
    if number == 0 or isinf(number) or isnan(number):
        return space.wrap(number)

    # Deal with extreme values for ndigits. For ndigits > NDIGITS_MAX, x
    # always rounds to itself.  For ndigits < NDIGITS_MIN, x always
    # rounds to +-0.0.
    if ndigits > NDIGITS_MAX:
        return space.wrap(number)
    elif ndigits < NDIGITS_MIN:
        # return 0.0, but with sign of x
        return space.wrap(0.0 * number)

    # finite x, and ndigits is not unreasonably large
    z = round_double(number, ndigits)
    if isinf(z):
        raise OperationError(space.w_OverflowError,
                             space.wrap("rounded value too large to represent"))
    return space.wrap(z)

# ____________________________________________________________

iter_sentinel = gateway.applevel('''
    # NOT_RPYTHON  -- uses yield
    # App-level implementation of the iter(callable,sentinel) operation.

    def iter_generator(callable_, sentinel):
        while 1:
            result = callable_()
            if result == sentinel:
                return
            yield result

    def iter_sentinel(callable_, sentinel):
        if not callable(callable_):
            raise TypeError, 'iter(v, w): v must be callable'
        return iter_generator(callable_, sentinel)

''', filename=__file__).interphook("iter_sentinel")

def iter(space, w_collection_or_callable, w_sentinel=None):
    """iter(collection) -> iterator over the elements of the collection.

iter(callable, sentinel) -> iterator calling callable() until it returns
                            the sentinal.
"""
    if w_sentinel is None:
        return space.iter(w_collection_or_callable)
    else:
        return iter_sentinel(space, w_collection_or_callable, w_sentinel)

def next(space, w_iterator, w_default=None):
    """next(iterator[, default])
Return the next item from the iterator. If default is given and the iterator
is exhausted, it is returned instead of raising StopIteration."""
    try:
        return space.next(w_iterator)
    except OperationError, e:
        if w_default is not None and e.match(space, space.w_StopIteration):
            return w_default
        raise

def ord(space, w_val):
    """Return the integer ordinal of a character."""
    return space.ord(w_val)

@unwrap_spec(w_modulus = WrappedDefault(None))
def pow(space, w_base, w_exponent, w_modulus):
    """With two arguments, equivalent to ``base**exponent''.
With three arguments, equivalent to ``(base**exponent) % modulus'',
but much more efficient for large exponents."""
    return space.pow(w_base, w_exponent, w_modulus)

def repr(space, w_object):
    """Return a canonical string representation of the object.
For simple object types, eval(repr(object)) == object."""
    return space.repr(w_object)

def setattr(space, w_object, w_name, w_val):
    """Store a named attribute into an object.
setattr(x, 'y', z) is equivalent to ``x.y = z''."""
    w_name = checkattrname(space, w_name)
    space.setattr(w_object, w_name, w_val)
    return space.w_None

def intern(space, w_str):
    """``Intern'' the given string.  This enters the string in the (global)
table of interned strings whose purpose is to speed up dictionary lookups.
Return the string itself or the previously interned string object with the
same value."""
    if space.is_w(space.type(w_str), space.w_str):
        return space.new_interned_w_str(w_str)
    raise OperationError(space.w_TypeError, space.wrap("intern() argument must be string."))

def callable(space, w_object):
    """Check whether the object appears to be callable (i.e., some kind of
function).  Note that classes are callable."""
    return space.callable(w_object)

@unwrap_spec(w_format_spec = WrappedDefault(""))
def format(space, w_obj, w_format_spec):
    """Format a obj according to format_spec"""
    return space.format(w_obj, w_format_spec)
