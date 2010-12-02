"""This module is always available. It provides access to mathematical
functions for complex numbers."""

# Complex math module

# much code borrowed from mathmodule.c

import math
from math import e, pi

try: from __pypy__ import builtinify
except ImportError: builtinify = lambda f: f


# constants
_one = complex(1., 0.)
_half = complex(0.5, 0.)
_i = complex(0., 1.)
_halfi = complex(0., 0.5)



# internal functions not available from Python
def _to_complex(x):
    if isinstance(x, complex):
        return x
    if isinstance(x, (str, unicode)):
        return x          # and let it fail later
    return complex(x)

def _prodi(x):
    x = _to_complex(x)
    real = -x.imag
    imag = x.real
    return complex(real, imag)


@builtinify
def phase(x):
    x = _to_complex(x)
    return math.atan2(x.imag, x.real)


@builtinify
def polar(x):
    x = _to_complex(x)
    phi = math.atan2(x.imag, x.real)
    r = abs(x)
    return r, phi


@builtinify
def rect(r, phi):
    return complex(r * math.cos(phi), r * math.sin(phi))


@builtinify
def acos(x):
    """acos(x)

    Return the arc cosine of x."""

    x = _to_complex(x)
    return -(_prodi(log((x+(_i*sqrt((_one-(x*x))))))))


@builtinify
def acosh(x):
    """acosh(x)

    Return the hyperbolic arccosine of x."""

    x = _to_complex(x)
    z = log(_sqrt_half*(sqrt(x+_one)+sqrt(x-_one)))
    return z+z


@builtinify
def asin(x):
    """asin(x)

    Return the arc sine of x."""

    x = _to_complex(x)
    # -i * log[(sqrt(1-x**2) + i*x]
    squared = x*x
    sqrt_1_minus_x_sq = sqrt(_one-squared)
    return -(_prodi(log((sqrt_1_minus_x_sq+_prodi(x)))))


@builtinify
def asinh(x):
    """asinh(x)

    Return the hyperbolic arc sine of x."""

    x = _to_complex(x)
    z = log((_sqrt_half * (sqrt(x+_i)+sqrt((x-_i))) ))
    return z+z


@builtinify
def atan(x):
    """atan(x)
    
    Return the arc tangent of x."""

    x = _to_complex(x)
    return _halfi*log(((_i+x)/(_i-x)))


@builtinify
def atanh(x):
    """atanh(x)

    Return the hyperbolic arc tangent of x."""

    x = _to_complex(x)
    return _half*log((_one+x)/(_one-x))


@builtinify
def cos(x):
    """cos(x)

    Return the cosine of x."""

    x = _to_complex(x)
    real = math.cos(x.real) * math.cosh(x.imag)
    imag = -math.sin(x.real) * math.sinh(x.imag)
    return complex(real, imag)


@builtinify
def cosh(x):
    """cosh(x)
    
    Return the hyperbolic cosine of x."""

    x = _to_complex(x)
    real = math.cos(x.imag) * math.cosh(x.real)
    imag = math.sin(x.imag) * math.sinh(x.real)
    return complex(real, imag)


@builtinify
def exp(x):
    """exp(x)
    
    Return the exponential value e**x."""

    x = _to_complex(x)
    l = math.exp(x.real)
    real = l * math.cos(x.imag)
    imag = l * math.sin(x.imag)
    return complex(real, imag)


@builtinify
def log(x, base=None):
    """log(x)

    Return the natural logarithm of x."""
    
    if base is not None:
        return log(x) / log(base)
    x = _to_complex(x)
    l = math.hypot(x.real,x.imag)
    imag = math.atan2(x.imag, x.real)
    real = math.log(l)
    return complex(real, imag)


@builtinify
def log10(x):
    """log10(x)

    Return the base-10 logarithm of x."""
    
    x = _to_complex(x)
    l = math.hypot(x.real, x.imag)
    imag = math.atan2(x.imag, x.real)/math.log(10.)
    real = math.log10(l)
    return complex(real, imag)


@builtinify
def sin(x):
    """sin(x)

    Return the sine of x."""
    
    x = _to_complex(x)
    real = math.sin(x.real) * math.cosh(x.imag)
    imag = math.cos(x.real) * math.sinh(x.imag)
    return complex(real, imag)


@builtinify
def sinh(x):
    """sinh(x)

    Return the hyperbolic sine of x."""
    
    x = _to_complex(x)
    real = math.cos(x.imag) * math.sinh(x.real)
    imag = math.sin(x.imag) * math.cosh(x.real)
    return complex(real, imag)


@builtinify
def sqrt(x):
    """sqrt(x)

    Return the square root of x."""
    
    x = _to_complex(x)
    if x.real == 0. and x.imag == 0.:
        real, imag = 0, 0
    else:
        s = math.sqrt(0.5*(math.fabs(x.real) + math.hypot(x.real,x.imag)))
        d = 0.5*x.imag/s
        if x.real > 0.:
            real = s
            imag = d
        elif x.imag >= 0.:
            real = d
            imag = s
        else:
            real = -d
            imag = -s
    return complex(real, imag)

_sqrt_half = sqrt(_half)


@builtinify
def tan(x):
    """tan(x)

    Return the tangent of x."""

    x = _to_complex(x)
    sr = math.sin(x.real)
    cr = math.cos(x.real)
    shi = math.sinh(x.imag)
    chi = math.cosh(x.imag)
    rs = sr * chi
    is_ = cr * shi
    rc = cr * chi
    ic = -sr * shi
    d = rc*rc + ic * ic
    real = (rs*rc + is_*ic) / d
    imag = (is_*rc - rs*ic) / d
    return complex(real, imag)


@builtinify
def tanh(x):
    """tanh(x)

    Return the hyperbolic tangent of x."""
    
    x = _to_complex(x)
    si = math.sin(x.imag)
    ci = math.cos(x.imag)
    shr = math.sinh(x.real)
    chr = math.cosh(x.real)
    rs = ci * shr
    is_ = si * chr
    rc = ci * chr
    ic = si * shr
    d = rc*rc + ic*ic
    real = (rs*rc + is_*ic) / d
    imag = (is_*rc - rs*ic) / d
    return complex(real, imag)

def isnan(x):
    """isnan(z) -> bool
    Checks if the real or imaginary part of z not a number (NaN)"""
    x = _to_complex(x)
    return math.isnan(x.real) or math.isnan(x.imag)

def isinf(x):
    """isnan(z) -> bool
    Checks if the real or imaginary part of z is infinite"""
    x = _to_complex(x)
    return math.isinf(x.real) or math.isinf(x.imag)
