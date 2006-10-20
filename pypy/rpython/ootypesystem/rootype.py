from pypy.annotation import model as annmodel
from pypy.rpython.rmodel import Repr
from pypy.rpython.ootypesystem import ootype
from pypy.rpython.ootypesystem.ootype import Void, Class
from pypy.annotation.pairtype import pairtype

class __extend__(annmodel.SomeOOClass):
    def rtyper_makerepr(self, rtyper):
        return ooclass_repr
    def rtyper_makekey(self):
        return self.__class__,

class __extend__(annmodel.SomeOOInstance):
    def rtyper_makerepr(self, rtyper):
        return OOInstanceRepr(self.ootype)
    def rtyper_makekey(self):
        return self.__class__, self.ootype

class __extend__(annmodel.SomeOOBoundMeth):
    def rtyper_makerepr(self, rtyper):
        return OOBoundMethRepr(self.ootype, self.name)
    def rtyper_makekey(self):
        return self.__class__, self.ootype, self.name

class __extend__(annmodel.SomeOOStaticMeth):
    def rtyper_makerepr(self, rtyper):
        return OOStaticMethRepr(self.method)
    def rtyper_makekey(self):
        return self.__class__, self.method



class OOClassRepr(Repr):
    lowleveltype = Class
ooclass_repr = OOClassRepr()

class OOInstanceRepr(Repr):
    def __init__(self, ootype):
        self.lowleveltype = ootype

    def rtype_getattr(self, hop):
        attr = hop.args_s[1].const
        s_inst = hop.args_s[0]
        _, meth = self.lowleveltype._lookup(attr)
        if meth is not None:
            # just return instance - will be handled by simple_call
            return hop.inputarg(hop.r_result, arg=0)
        self.lowleveltype._check_field(attr)
        vlist = hop.inputargs(self, Void)
        return hop.genop("oogetfield", vlist,
                         resulttype = hop.r_result.lowleveltype)

    def rtype_setattr(self, hop):
        if self.lowleveltype is Void:
            return
        attr = hop.args_s[1].const
        self.lowleveltype._check_field(attr)
        vlist = hop.inputargs(self, Void, hop.args_r[2])
        return hop.genop('oosetfield', vlist)

    def rtype_is_true(self, hop):
        vlist = hop.inputargs(self)
        return hop.genop('oononnull', vlist, resulttype=ootype.Bool)


class __extend__(pairtype(OOInstanceRepr, OOInstanceRepr)):
    def rtype_is_((r_ins1, r_ins2), hop):
        # NB. this version performs no cast to the common base class
        vlist = hop.inputargs(r_ins1, r_ins2)
        return hop.genop('oois', vlist, resulttype=ootype.Bool)

    rtype_eq = rtype_is_

    def rtype_ne(rpair, hop):
        v = rpair.rtype_eq(hop)
        return hop.genop("bool_not", [v], resulttype=ootype.Bool)

class OOBoundMethRepr(Repr):
    def __init__(self, ootype, name):
        self.lowleveltype = ootype
        self.name = name

    def rtype_simple_call(self, hop):
        TYPE = hop.args_r[0].lowleveltype
        _, meth = TYPE._lookup(self.name)
        if isinstance(meth, ootype._overloaded_meth):
            ARGS = tuple([repr.lowleveltype for repr in hop.args_r[1:]])
            desc = meth._get_desc(self.name, ARGS)
            cname = hop.inputconst(Void, desc)
        else:
            cname = hop.inputconst(Void, self.name)
        vlist = hop.inputargs(self, *hop.args_r[1:])
        hop.exception_is_here()
        return hop.genop("oosend", [cname]+vlist,
                         resulttype = hop.r_result.lowleveltype)


class OOStaticMethRepr(Repr):
    def __init__(self, METHODTYPE):
        self.lowleveltype = METHODTYPE


class __extend__(pairtype(OOInstanceRepr, OOBoundMethRepr)):

    def convert_from_to(_, v, llops):
        return v
