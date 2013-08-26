Frequently Asked Questions
==========================

.. contents::

What is PyPy?
-------------

PyPy is a reimplementation of Python in Python, using the RPython translation
toolchain.

PyPy tries to find new answers about ease of creation, flexibility,
maintainability and speed trade-offs for language implementations.
For further details see our :doc:`goal and architecture document <architecture>`.


Is PyPy a drop in replacement for CPython?
------------------------------------------

Almost!

The mostly likely stumbling block for any given project is support for
:ref:`extension modules <extension-modules>`.  PyPy supports a continually growing
number of extension modules, but so far mostly only those found in the
standard library.

The language features (including builtin types and functions) are very
complete and well tested, so if your project does not use many
extension modules there is a good chance that it will work with PyPy.

We list the differences we know about in :doc:`cpython differences <cpython_differences>`.


Do CPython Extension modules work with PyPy?
--------------------------------------------

We have experimental support for CPython extension modules, so
they run with minor changes.  This has been a part of PyPy since
the 1.4 release, but support is still in beta phase.  CPython
extension modules in PyPy are often much slower than in CPython due to
the need to emulate refcounting.  It is often faster to take out your
CPython extension and replace it with a pure python version that the
JIT can see.

We fully support ctypes-based extensions. But for best performance, we
recommend that you use the cffi_ module to interface with C code.

For information on which third party extensions work (or do not work)
with PyPy see the `compatibility wiki`_.

.. _compatibility wiki: https://bitbucket.org/pypy/compatibility/wiki/Home
.. _cffi: http://cffi.readthedocs.org/


On which platforms does PyPy run?
---------------------------------

PyPy is regularly and extensively tested on Linux machines and on Mac
OS X and mostly works under Windows too (but is tested there less
extensively). PyPy needs a CPython running on the target platform to
bootstrap, as cross compilation is not really meant to work yet.
At the moment you need CPython 2.5 - 2.7
for the translation process. PyPy's JIT requires an x86 or x86_64 CPU.
(There has also been good progress on getting the JIT working for ARMv7.)


Which Python version (2.x?) does PyPy implement?
------------------------------------------------

PyPy currently aims to be fully compatible with Python 2.7. That means that
it contains the standard library of Python 2.7 and that it supports 2.7
features (such as set comprehensions).


.. _threading:

Does PyPy have a GIL?  Why?
-------------------------------------------------

Yes, PyPy has a GIL.  Removing the GIL is very hard.  The problems are
essentially the same as with CPython (including the fact that our
garbage collectors are not thread-safe so far).  Fixing it is possible,
as shown by Jython and IronPython, but difficult.  It would require
adapting the whole source code of PyPy, including subtle decisions about
whether some effects are ok or not for the user (i.e. the Python
programmer).

Instead, since 2012, there is work going on on a still very experimental
Software Transactional Memory (STM) version of PyPy.  This should give
an alternative PyPy which internally has no GIL, while at the same time
continuing to give the Python programmer the complete illusion of having
one.  It would in fact push forward *more* GIL-ish behavior, like
declaring that some sections of the code should run without releasing
the GIL in the middle (these are called *atomic sections* in STM).


How do I write extension modules for PyPy?
------------------------------------------

See :doc:`extending`.


.. _how-fast-is-pypy:

How fast is PyPy?
-----------------
This really depends on your code.
For pure Python algorithmic code, it is very fast.  For more typical
Python programs we generally are 3 times the speed of Cpython 2.6 .
You might be interested in our `benchmarking site`_ and our
:doc:`jit documentation <rpython:jit/index>`.

Note that the JIT has a very high warm-up cost, meaning that the
programs are slow at the beginning.  If you want to compare the timings
with CPython, even relatively simple programs need to run *at least* one
second, preferrably at least a few seconds.  Large, complicated programs
need even more time to warm-up the JIT.

.. _benchmarking site: http://speed.pypy.org


Couldn't the JIT dump and reload already-compiled machine code?
---------------------------------------------------------------

No, we found no way of doing that.  The JIT generates machine code
containing a large number of constant addresses --- constant at the time
the machine code is written.  The vast majority is probably not at all
constants that you find in the executable, with a nice link name.  E.g.
the addresses of Python classes are used all the time, but Python
classes don't come statically from the executable; they are created anew
every time you restart your program.  This makes saving and reloading
machine code completely impossible without some very advanced way of
mapping addresses in the old (now-dead) process to addresses in the new
process, including checking that all the previous assumptions about the
(now-dead) object are still true about the new object.


How do I get into PyPy development?  Can I come to sprints?
-----------------------------------------------------------

Certainly you can come to sprints! We always welcome newcomers and try
to help them as much as possible to get started with the project.  We
provide tutorials and pair them with experienced PyPy
developers. Newcomers should have some Python experience and read some
of the PyPy documentation before coming to a sprint.

Coming to a sprint is usually the best way to get into PyPy development.
If you get stuck or need advice, :doc:`contact us <index>`. IRC is
the most immediate way to get feedback (at least during some parts of the day;
most PyPy developers are in Europe) and the `mailing list`_ is better for long
discussions.

.. _mailing list: http://python.org/mailman/listinfo/pypy-dev


OSError: ... cannot restore segment prot after reloc... Help?
-------------------------------------------------------------

On Linux, if SELinux is enabled, you may get errors along the lines of
"OSError: externmod.so: cannot restore segment prot after reloc: Permission
denied." This is caused by a slight abuse of the C compiler during
configuration, and can be disabled by running the following command with root
privileges::

    # setenforce 0

This will disable SELinux's protection and allow PyPy to configure correctly.
Be sure to enable it again if you need it!
