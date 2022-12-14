from __future__ import annotations

import sys
sys.path.append("/home/nrealus/perso/latest/prog/ai-planning-sandbox/python-playground7")

import typing
import time
from enum import Enum
from src.utility.new_int_id import new_int_id
from src.constraints.constraints import ConstraintNetwork, ConstraintType
from src.assertion import Assertion

############################################


############################################

class ActionMethodTemplate():

    class Type(Enum):
        ACTION = 0
        METHOD = 1

    def __init__(self,
        p_type:Type,
        p_name:str,
        p_param_domain_vars:typing.Tuple[typing.Tuple[str,str],...],
        p_assertions_func:typing.Callable[[str,str,typing.Dict[str,str]],typing.Set[Assertion]]=(lambda ts,te,params: set()),
        p_constraints_func:typing.Callable[[str,str,typing.Dict[str,str]],typing.Tuple[ConstraintType,typing.Any]]=(lambda ts,te,params: set()),
    ):
        self._type = p_type
        self._name = p_name
        self._params_domain_vars = p_param_domain_vars
        self._assertions_func = p_assertions_func
        self._constraints_func = p_constraints_func

    @property
    def type(self) -> Type:
        return self._type

    @property
    def name(self) -> str:
        return self._name

    @property
    def param_domain_vars(self) -> typing.Tuple[typing.Tuple[str,str],...]:
        return self._params_domain_vars

    @property
    def assertions_func(self) -> typing.Callable[[str,str,typing.Dict[str,str]],typing.Set[Assertion]]:
        return self._assertions_func

    @property
    def constraints_func(self) -> typing.Callable[[str,str,typing.Dict[str,str]],typing.Set[typing.Tuple[ConstraintType,typing.Any]]]:
        return self._constraints_func

class ActionMethod():

    def __init__(self,
        p_template:ActionMethodTemplate,
        p_param_arg_vars:typing.Tuple[typing.Tuple[str,str],...],
        p_name:str="",
        p_time_start:str="",
        p_time_end:str="",
    ):

        k = new_int_id()
        if p_name == "":
            _name = "{0}_{1}".format(p_template.name, str(k))
        else:
            _name = p_name
        if p_time_start == "":
            ts = "__ts_act_{0}".format(str(k))
        else:
            ts = p_time_start
        if p_time_end == "":
            te = "__te_act_{0}".format(str(k))
        else:
            te = p_time_end

        self._template = p_template
        self._args = p_param_arg_vars
        self._name = _name
        self._time_start = ts
        self._time_end = te
        self._assertions = p_template.assertions_func(ts,te,{ k:v for (k,v) in p_param_arg_vars })
        self._constraints = p_template.constraints_func(ts,te,{ k:v for (k,v) in p_param_arg_vars })

    @property
    def type(self) -> ActionMethodTemplate.Type:
        return self._template.type

    @property
    def template(self) -> ActionMethodTemplate:
        return self._template

    @property
    def name(self) -> str:
        return self._name

    @property
    def param_arg_vars(self) -> typing.Tuple[typing.Tuple[str,str],...]:
        return self._args

    @property
    def time_start(self) -> str:
        return self._time_start

    @property
    def time_end(self) -> str:
        return self._time_end

    @property
    def assertions(self) -> typing.Set[Assertion]:
        return self._assertions

    @property
    def constraints(self) -> typing.Set[typing.Tuple[ConstraintType,typing.Any]]:
        return self._constraints

    def propagate_applicability(self,
        p_time:str,
        p_cn:ConstraintNetwork,
        p_assertions:typing.Iterable[Assertion],
        p_revert_on_failure:bool,
        p_revert_on_success:bool,
        p_assertion_to_support:Assertion=None,
    ) -> typing.Iterable[typing.Tuple[Assertion,Assertion]]:
        '''
        Attempts to propagate the constraints necessary to enforce applicability of this action/method at the
        specified time, considering specified assertions and in the specified constraint network.
        Used to determine whether this action/method can be applicable in the specified situation by
        propagating the constraints necessary to enforce applicability in the specified situation
        (i.e. starting at p_time, having all the action/method's assertions starting at time p_time causally supported
        from p_assertions, supporting p_assertion_to_support with one of the action/method's assertions)
        If propagation is successful, then applicability in the specified situation is indeed possible
        (i.e. can be enforced and even remains enforced, if p_backtrack is False).
        Arguments:
            p_time (str):
                The time to test applicability at
            p_cn (ConstraintNetwork):
                The constraint network describing the situation and where to propagate the constraints
            p_assertions (Iterable[Assertions]):
                The assertions describing the situation
            p_assertion_to_support (Assertion, None by default):
                An assertion that must be supported by one of the action/method's assertions. Can be None.
            p_backtrack (bool, True by default):
                Whether to backtrack the changes propagated to the constraint network (in case it is successful).
                In other words, whether to "apply" the action/method (enforce its applicability) or simply check if
                applicability is possible.
        Returns:
            A collection of Assertion pairs (supporter, supportee) describing the causal supports introduced with the applicability.
            The first element is the supporter assertion, the second element is the supported one.
            If applicability is impossible (applicability constraints propagation failed) the an empty [] list is returned.
        Side effects:
            Changes propagated to p_cn, in case p_backtrack is False.
        '''

        res = []
        p_cn.backup()
        if not p_cn.propagate_constraints(self.constraints, p_backup=False, p_revert_on_failure=False, p_revert_on_success=False):
            if p_revert_on_failure:
                p_cn.backtrack()
            return []
        # the action/method's starting time must be "now" (p_time)
        #if p_cn.tempvars_unified(self.time_start, p_time):
        if (p_cn.propagate_constraints([
                (ConstraintType.TEMPORAL,(self.time_start, p_time, 0, False)),
                (ConstraintType.TEMPORAL,(p_time, self.time_start, 0, False))],
            p_backup=False, p_revert_on_failure=True, p_revert_on_success=False)
            # and p_cn.tempvars_minimal_directed_distance(self.time_start, p_time) == 0
        ):
            b2 = (p_assertion_to_support == None)
            for i_act_or_meth_asrt in self.assertions:
                b1 = False
                for i_chronicle_asrt in p_assertions:
                    if i_act_or_meth_asrt == i_chronicle_asrt: # just in case
                        break
                    # the action/method must have at least one assertion (any, not necessarily starting at the same time as it)
                    # supporting an unsupported assertion already present in the chronicle
                    if i_chronicle_asrt.propagate_causal_support_by(i_act_or_meth_asrt, p_cn, p_revert_on_failure=True, p_revert_on_success=False):
                        res.append((i_act_or_meth_asrt, i_chronicle_asrt))
                        if not b2 and i_chronicle_asrt == p_assertion_to_support:
                            b2 = True
                    # the chronicle must support all action/method's assertions which start at the same time as it
                    # elif because two assertions can't support each other
                    elif not b1 and p_cn.tempvars_unified(i_act_or_meth_asrt.time_start,self.time_start):
                        if i_act_or_meth_asrt.propagate_causal_support_by(i_chronicle_asrt, p_cn, p_revert_on_failure=True, p_revert_on_success=False):
                            res.append((i_chronicle_asrt, i_act_or_meth_asrt))
                            b1 = True
                if not b1:
                    if p_revert_on_failure:
                        p_cn.backtrack()
                    return []
            if not b2:
                if p_revert_on_failure:
                    p_cn.backtrack()
                return []
        if p_revert_on_success:
            p_cn.backtrack()
        return res
