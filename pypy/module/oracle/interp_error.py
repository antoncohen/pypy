from pypy.interpreter.baseobjspace import Wrappable
from pypy.rpython.lltypesystem import rffi, lltype
from pypy.interpreter.typedef import TypeDef, interp_attrproperty
from pypy.interpreter.gateway import interp2app
from pypy.module.oracle import roci, config

class State: 
    def __init__(self, space):
        w_module = space.getbuiltinmodule('cx_Oracle')
        def get(name):
            return space.getattr(w_module, space.wrap(name))

        self.w_DatabaseError = get('DatabaseError')
        self.w_OperationalError = get('OperationalError')
        self.w_InterfaceError = get('InterfaceError')
        self.w_ProgrammingError = get('ProgrammingError')
        self.w_NotSupportedError = get('NotSupportedError')
        self.w_IntegrityError = get('IntegrityError')
        self.w_Variable = get('Variable')

        w_import = space.builtin.get('__import__')
        w_decimal = space.call(w_import, space.newlist(
            [space.wrap('decimal')]))
        self.w_DecimalType = space.getattr(w_decimal, space.wrap("Decimal"))
        w_datetime = space.call(w_import, space.newlist(
            [space.wrap('datetime')]))
        self.w_DateTimeType = space.getattr(w_datetime, space.wrap("datetime"))
        self.w_DateType = space.getattr(w_datetime, space.wrap("date"))


def get(space): 
    return space.fromcache(State) 

class W_Error(Wrappable):
    def __init__(self, space, environment, context, retrieveError):
        self.context = context
        if retrieveError:
            if environment.errorHandle:
                handle = environment.errorHandle
                handleType = roci.OCI_HTYPE_ERROR
            else:
                handle = environment.handle
                handleType = roci.OCI_HTYPE_ENV

            codeptr = lltype.malloc(rffi.CArray(roci.sb4), 1, flavor='raw')
            BUFSIZE = 1024
            textbuf, text = rffi.alloc_buffer(BUFSIZE)

            try:
                status = roci.OCIErrorGet(
                    handle, 1, lltype.nullptr(roci.oratext.TO), codeptr,
                    textbuf, BUFSIZE, handleType)
                if status != roci.OCI_SUCCESS:
                    raise OperationError(
                        w_InternalErrorException,
                        space.wrap("No Oracle error?"))

                self.code = codeptr[0]
                self.message = config.w_string(
                    space,
                    rffi.str_from_buffer(textbuf, text,
                                         BUFSIZE, BUFSIZE))
            finally:
                lltype.free(codeptr, flavor='raw')
                rffi.keep_buffer_alive_until_here(textbuf, text)

            if config.WITH_UNICODE:
                # XXX remove double zeros at the end
                pass

    def desc_str(self):
        return self.message

W_Error.typedef = TypeDef(
    'Error',
    __str__ = interp2app(W_Error.desc_str),
    code = interp_attrproperty('code', W_Error),
    message = interp_attrproperty('message', W_Error))


