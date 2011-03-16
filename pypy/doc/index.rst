
Welcome to PyPy Development
=============================================

The PyPy project aims at producing a flexible and fast Python_
implementation.  The guiding idea is to translate a Python-level
description of the Python language itself to lower level languages.
Rumors have it that the secret goal is being faster-than-C which is
nonsense, isn't it?  `more...`_

Getting into PyPy ... 
=============================================

* `Release 1.4`_: the latest official release

* `PyPy Blog`_: news and status info about PyPy 

* `Documentation`_: extensive documentation and papers_ about PyPy.  

* `Getting Started`_: Getting started and playing with PyPy. 

Mailing lists, bug tracker, IRC channel
=============================================

* `Development mailing list`_: development and conceptual
  discussions. 

* `Subversion commit mailing list`_: updates to code and
  documentation. 

* `Development bug/feature tracker`_: filing bugs and feature requests. 

* `Sprint mailing list`_: mailing list for organizing upcoming sprints. 

* **IRC channel #pypy on freenode**: Many of the core developers are hanging out 
  at #pypy on irc.freenode.net.  You are welcome to join and ask questions
  (if they are not already developed in the FAQ_).
  You can find logs of the channel here_.

.. XXX play1? 

Meeting PyPy developers
=======================

The PyPy developers are organizing sprints and presenting results at
conferences all year round. They will be happy to meet in person with
anyone interested in the project.  Watch out for sprint announcements
on the `development mailing list`_.

.. _Python: http://docs.python.org/index.html
.. _`more...`: architecture.html#mission-statement 
.. _`PyPy blog`: http://morepypy.blogspot.com/
.. _`development bug/feature tracker`: https://codespeak.net/issue/pypy-dev/ 
.. _here: http://tismerysoft.de/pypy/irc-logs/pypy
.. _`sprint mailing list`: http://codespeak.net/mailman/listinfo/pypy-sprint 
.. _`subversion commit mailing list`: http://codespeak.net/mailman/listinfo/pypy-svn
.. _`development mailing list`: http://codespeak.net/mailman/listinfo/pypy-dev
.. _`FAQ`: faq.html
.. _`Documentation`: docindex.html 
.. _`Getting Started`: getting-started.html
.. _papers: extradoc.html
.. _`Release 1.4`: http://pypy.org/download.html


Detailed Documentation
======================

.. The following documentation is important and reasonably up-to-date:

.. toctree::
   :maxdepth: 1

   .. The following stuff is high-value and (vaguely) true:
   getting-started.rst
   getting-started-python.rst
   getting-started-dev.rst
   windows.rst
   faq.rst
   architecture.rst
   coding-guide.rst
   cpython_differences.rst
   cleanup-todo.rst
   garbage_collection.rst
   interpreter.rst
   objspace.rst

   dev_method.rst
   extending.rst

   extradoc.rst
     .. ^^ integrate this one level up: dcolish?

   glossary.rst

   contributor.rst

   .. True, high-detail:
   interpreter-optimizations.rst
   configuration.rst
   low-level-encapsulation.rst
   parser.rst
   rlib.rst
   rtyper.rst
   translation.rst
   jit/_ref.rst
   jit/index.rst
   jit/overview.rst
   jit/pyjitpl5.rst

   index-of-release-notes.rst

   ctypes-implementation.rst
     .. ^^ needs attention

   how-to-release.rst
     .. ^^ needs attention

   index-report.rst
     .. ^^ of historic interest, and about EU fundraising

   stackless.rst
     .. ^^ it still works; needs JIT integration; hasn't been maintained for years


Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
* :ref:`glossary`

