"""
Basic Concept:
--------------

All concurrency is expressed by some means of coroutines.
This is the lowest possible exposable interface.

A coroutine is a structure that controls a sequence
of continuations in time. It contains a frame object
that is a restartable stack chain. This frame object
is updated on every switch.

The frame can be None. Either the coroutine is not yet
bound, or it is the current coroutine of some costate.
See below. XXX rewrite a definition of these terms.

There is always a notation of a "current" and a "last"
coroutine. Current has no frame and represents the
running program. last is needed to keep track of the
coroutine that receives a new frame chain after a switch.

A costate object holds last and current.
There are different coroutine concepts existing in
parallel, like plain interp-level coroutines and
app-level structures like coroutines, greenlets and
tasklets.
Every concept is associated with its own costate object.
This allows for peaceful co-existence of many concepts.
The type of a switch is determined by the target's costate.
"""

from pypy.interpreter.baseobjspace import Wrappable
from pypy.rpython.rstack import yield_current_frame_to_caller, resume_point
from pypy.rpython.objectmodel import we_are_translated

try:
    from py.magic import greenlet
    main_greenlet = greenlet.getcurrent()

    class FrameChain(object):

        def __init__(self, thunk=None):
            if thunk:
                self.greenlet = greenlet(thunk)
            else:
                self.greenlet = greenlet.getcurrent()

        def switch(self):
            last = FrameChain()
            return self.greenlet.switch(last)

        def shutdown(self):
            current = FrameChain()
            target = current.greenlet.parent
            target.switch(None)

except ImportError:
    def greenlet(*args, **kwargs):
        raise NotImplementedError("need either greenlets or a translated version of pypy")

import sys, os


class BaseCoState(object):
    def __init__(self):
        self.current = self.main = None

    def __repr__(self):
        "NOT_RPYTHON"
        # for debugging only
        return '<%s current=%r>' % (self.__class__.__name__, self.current)

    def update(self, new):
        syncstate.leaving = self.current
        syncstate.entering = new
        self.current = new
        frame, new.frame = new.frame, None
        return frame


class CoState(BaseCoState):
    def __init__(self):
        BaseCoState.__init__(self)
        self.current = self.main = Coroutine(self)

class CoroutineDamage(SystemError):
    pass


class SyncState(object):
    def __init__(self):
        self.reset()

    def reset(self):
        self.default_costate = None
        self.leaving = None
        self.entering = None
        self.things_to_do = False
        self.temp_exc = None
        self.to_delete = []

    def switched(self, incoming_frame):
        left = syncstate.leaving
        entered = syncstate.entering
        syncstate.leaving = syncstate.entering = None
        if left is not None:   # mostly to work around an annotation problem;
                               # should not really be None
            left.frame = incoming_frame
            left.goodbye()
        if entered is not None:
            entered.hello()
        if self.things_to_do:
            self._do_things_to_do()

    def check_for_zombie(self, obj):
        return co in self.to_delete

    def postpone_deletion(self, obj):
        self.to_delete.append(obj)
        self.things_to_do = True

    def _do_things_to_do(self):
        if self.temp_exc is not None:
            # somebody left an unhandled exception and switched to us.
            # this both provides default exception handling and the
            # way to inject an exception, like CoroutineExit.
            e, self.temp_exc = self.temp_exc, None
            self.things_to_do = bool(self.to_delete)
            raise e
        while self.to_delete:
            delete, self.to_delete = self.to_delete, []
            for obj in delete:
                obj.parent = obj.costate.current
                obj._kill_finally()
        else:
            self.things_to_do = False

syncstate = SyncState()


class CoroutineExit(SystemExit):
    # XXX SystemExit's __init__ creates problems in bookkeeper.
    def __init__(self):
        pass

class AbstractThunk(object):
    def call(self):
        raise NotImplementedError("abstract base class")


class Coroutine(Wrappable):
    def __init__(self, state=None):
        self.frame = None
        if state is None:
            state = self._get_default_costate()
        self.costate = state
        self.parent = None
        self.thunk = None

    def __repr__(self):
        'NOT_RPYTHON'
        # just for debugging
        if hasattr(self, '__name__'):
            return '<Coro %s frame=%r %s>' % (self.__name__, self.frame, self.thunk is not None)
        else:
            return '<coro frame=%r %s>' % (self.frame, self.thunk is not None)

    def _get_default_costate():
        state = syncstate.default_costate
        if state is None:
            state = syncstate.default_costate = CoState()
        return state
    _get_default_costate = staticmethod(_get_default_costate)

    def _get_default_parent(self):
        return self.costate.current

    def bind(self, thunk):
        assert isinstance(thunk, AbstractThunk)
        if self.frame is not None:
            raise CoroutineDamage
        if self.parent is None:
            self.parent = self._get_default_parent()
        assert self.parent is not None
        self.thunk = thunk
        if we_are_translated():
            self.frame = self._bind()
        else:
            self.frame = self._greenlet_bind()

    def _greenlet_bind(self):
        state = self.costate
        self.parent = state.current
        assert self.parent is not None
        weak = [self]
        def _greenlet_execute(incoming_frame):
            try:
                return weak[0]._execute(incoming_frame)
            finally:
                del weak[0]
                chain.shutdown()
        chain = FrameChain(_greenlet_execute)
        return chain

    def _bind(self):
        state = self.costate
        self.parent = state.current
        incoming_frame = yield_current_frame_to_caller()
        return self._execute(incoming_frame)

    def _execute(self, incoming_frame):
        syncstate.switched(incoming_frame)
        state = self.costate
        try:
            try:
                try:
                    exc = None
                    self.thunk.call()
                    resume_point("coroutine__bind", self, state)
                except Exception, e:
                    exc = e
                    raise
            finally:
                self.finish(exc)
                self.thunk = None
        except CoroutineExit:
            # ignore a shutdown exception
            pass
        except Exception, e:
            # redirect all unhandled exceptions to the parent
            syncstate.things_to_do = True
            syncstate.temp_exc = exc
        while self.parent is not None and self.parent.frame is None:
            # greenlet behavior is fine
            self.parent = self.parent.parent
        return state.update(self.parent)

    def switch(self):
        if self.frame is None:
            # considered a programming error.
            # greenlets and tasklets have different ideas about this.
            raise CoroutineDamage
        state = self.costate
        incoming_frame = state.update(self).switch()
        resume_point("coroutine_switch", self, state, returns=incoming_frame)
        syncstate.switched(incoming_frame)

    def kill(self):
        if self.frame is None:
            return
        state = self.costate
        syncstate.things_to_do = True
        syncstate.temp_exc = CoroutineExit()
        self.parent = state.current
        self.switch()

    def _kill_finally(self):
        try:
            self._userdel()
        except Exception:
            pass # maybe print a warning?
        self.kill()

    def __del__(self):
        # provide the necessary clean-up if this coro is left
        # with a frame.
        # note that AppCoroutine has to take care about this
        # as well, including a check for user-supplied __del__.
        # Additionally note that in the context of __del__, we are
        # not in the position to issue a switch.
        # we defer it completely.
        if self.frame is not None:
            syncstate.postpone_deletion(self)

    def _userdel(self):
        # override this for exposed coros
        pass

    def is_alive(self):
        return self.frame is not None or self is self.costate.current

    def is_zombie(self):
        return self.frame is not None and syncstate.check_for_zombie(self)

    def getcurrent():
        costate = Coroutine._get_default_costate()
        return costate.current
    getcurrent = staticmethod(getcurrent)

    def getmain():
        costate = Coroutine._get_default_costate()
        return costate.main
    getmain = staticmethod(getmain)

    def hello(self):
        "Called when execution is transferred into this coroutine."

    def goodbye(self):
        "Called just after execution is transferred away from this coroutine."

    def finish(self, exc=None):
        "stephan forgot me"

# _________________________________________________
