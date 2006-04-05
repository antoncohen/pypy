from pypy.annotation import model as annmodel
from pypy.rpython import extregistry

import ctypes


CFuncPtrType = type(ctypes.CFUNCTYPE(None))

def cfuncptrtype_compute_annotation(type, instance):

    def compute_result_annotation(*args_s):
        """
        Answer the annotation of the external function's result
        """
        result_ctype = instance.restype
        s_result = annmodel.SomeCTypesObject(result_ctype,
                                         annmodel.SomeCTypesObject.OWNSMEMORY)
        return s_result.return_annotation()

    return annmodel.SomeBuiltin(compute_result_annotation, 
        methodname=instance.__name__)

def cfuncptrtype_specialize_call(hop):
    # this is necessary to get the original function pointer when specializing
    # the metatype
    assert hop.spaceop.opname == "simple_call"
    cfuncptr = hop.spaceop.args[0].value

    args_r = []
    for ctype in cfuncptr.argtypes:
        s_arg = annmodel.SomeCTypesObject(ctype,
                              annmodel.SomeCTypesObject.MEMORYALIAS)
        r_arg = hop.rtyper.getrepr(s_arg)
        args_r.append(r_arg)

    vlist = hop.inputargs(*args_r)
    unwrapped_args_v = [r_arg.getvalue(hop.llops, v)
                        for r_arg, v in zip(args_r, vlist)]

    ll_func = cfuncptr.llinterp_friendly_version
    v_result = hop.llops.gendirectcall(ll_func, *unwrapped_args_v)
    return v_result

extregistry.register_metatype(CFuncPtrType, 
    compute_annotation=cfuncptrtype_compute_annotation,
    specialize_call=cfuncptrtype_specialize_call)
