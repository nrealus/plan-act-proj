from __future__ import annotations
from csv import Dialect
from hashlib import new

import sys

sys.path.append("/home/nrealus/perso/latest/prog/ai-planning-sandbox/python-playground7")

import typing
import warnings
import math
from enum import Enum
from copy import deepcopy
from src.utility.unionfind import UnionFind2
from src.utility.new_int_id import new_int_id
from src.constraints.domain import Domain, DomainType

############################################

# 22 / 08 / 2022

# The main constraint network is implemented here.
# Its purpose is to store and aggregate constraints on temporal and object variables
# to allow the planning and acting system to make informed decisions during planning search, execution and monitoring
# using various queries (like "unified", "unifiable", "separated", "separable", or to check variables' possible legal values (domains)).

# To achieve this, we need to propagate constraints introduced by the planning search,
# which is done through local (incomplete) consistency checking,
# leaving out full consistency checking for the final planning search nodes,
# as that is more expensive (algorithmically). (See GNT 2016 4.4 )

# Constants are represented as object variables with a singleton domain.

# As such it wraps a "binding constraint network" (BCN) on object variables, and a "simple temporal network" (STN) on temporal variables (points).
# These networks are combined and need each other (references passed in constraint propagation) to support mixing temporal and object variables.
# In the STN, temporal constraints constrain (duh) temporal variables (time points) to happen within a bounded window relatively to each other.
# The lower and upper bound of this window correspond to object variables.
# Usually, values are used directly instead, but with a "constant" object variable this can be done as well.
# However, if these "duration bounds" object variables were not fixed constants but "actual variables",
# constraint propagation in one of the networks should trigger propagation in the other one.
# This can happen in the BCN after a change on their domain (due to the propagation of some other constraint),
# or in the STN after a new constraint on the same temporal variables is propagated.
# This is why there is a reference to the STN in the BCN constraint propagation function and vice-versa.

# For more details about the integration of object and temporal variables, see FAPE (Bit-Monnot, 2020).

# The implementation is, of course, not ideal and could be optimised.
# Some redesigns could be possible in the future as well.

############################################

class ConstraintType(Enum):
    UNIFICATION = 0
    DISJ_UNIFICATION = 1
    SEPARATION = 2
    GENERAL_RELATION = 3
    DOMAIN_VAL_LEQ = 4
    DOMAIN_VAL_GEQ = 5
    DOMAIN_VAL_LE = 6
    DOMAIN_VAL_GE = 7
    TEMPORAL = 8

class ConstraintNetwork():

    def __init__(self):
        self.m_bcn: BCN = BCN()
        self.m_stn: STN = STN()

    def declare_and_init_objvars(self, p_domains: typing.Dict[str, Domain]) -> None:
        """
        Wrapper for declaring and initialising object variables (in the BCN)
        (i.e. creating corresponding entries in the domains dictionary entry and adding to the union-find structure)
        Arguments:
            p_domains (dict(str,Domain)): dictionary of domains and their associated object variables to integrate
        Returns:
            None
        Side effects:
            Updates BCN domains object and initialises BCN union-find object used for unification constraints
        """
        self.m_bcn.m_unifications.make_set(p_domains.keys())
        self.m_bcn.m_domains = { **self.m_bcn.m_domains , **p_domains }

    def objvar_domain(self, p_var:str) -> Domain:
        """
        Wrapper used to access the domain object of an object variable (through the BCN)
        Arguments:
            p_var (str): an object variable
        Returns:
           The domain (Domain) of the specified object variable 
        """
        return self.m_bcn.m_domains[p_var]

    def objvars_unified(self, p_var1:str, p_var2:str) -> bool:
        """
        Query used to determine whether two object variables are unified,
        i.e. are bound by a unification constraint or are both reduced to the same singleton domain.
        Arguments:
            p_var1 (str): an object variable
            p_var2 (str): an object variable
        Returns:
            True if the specified object variables are unified, False otherwise
        """
        if p_var1 == p_var2:
            return True
        # include check of symmetric orderings ? (v1 (<=) v2) and (v2 (<=) v1) <==> v1 (=) v2
        # that actually would be much easier to represent with a graph as a "traditional" adjancency list than a disjoint set (union-find)...
        if (self.m_bcn.m_unifications.contains([p_var1]) and self.m_bcn.m_unifications.contains([p_var2])
            and self.m_bcn.m_unifications.find(p_var1) == self.m_bcn.m_unifications.find(p_var2)
        ):
            return True
        if self.m_bcn.m_domains[p_var1].size() == 1:
            return self.m_bcn.m_domains[p_var1].get_values() == self.m_bcn.m_domains[p_var2].get_values() and not self.m_bcn.m_domains[p_var1].contains(Domain._UNKNOWN_VALUE)
        return False

    def objvars_unifiable(self, p_var1:str, p_var2:str) -> bool:
        """
        Query used to determine whether two object variables are unifiable,
        i.e. are not bound by a separation constraint and have intersecting domains.
        Arguments:
            p_var1 (str): an object variable
            p_var2 (str): an object variable
        Returns:
            True if the specified object variables are unifiable, False otherwise
        """
        if p_var1 == p_var2:
            return True

        if ((p_var1 in self.m_bcn.m_separations and p_var2 in self.m_bcn.m_separations[p_var1])
            or (p_var2 in self.m_bcn.m_separations and p_var1 in self.m_bcn.m_separations[p_var2])
        ):
            return False

        if not self.m_bcn.m_domains[p_var1].intersects(self.m_bcn.m_domains[p_var2]):
            return False

        if self.m_bcn.m_domains[p_var1].contains(Domain._UNKNOWN_VALUE) or self.m_bcn.m_domains[p_var2].contains(Domain._UNKNOWN_VALUE):
            return False

        if self.m_bcn.m_unifications.contains([p_var1]) and self.m_bcn.m_unifications.contains([p_var2]):
            cc1 = self.m_bcn.m_unifications.connected_component(p_var1)
            cc2 = self.m_bcn.m_unifications.connected_component(p_var2)
            for unif_var1 in cc1:
                for unif_var2 in cc2:
                    if unif_var1 in self.m_bcn.m_separations and unif_var2 in self.m_bcn.m_separations[unif_var1]:
                        return False
        return True

    def objvars_separable(self, p_var1:str, p_var2:str) -> bool:
        """
        Query used to determine whether two object variables are separable,
        i.e. are not unified.
        Arguments:
            p_var1 (str): an object variable
            p_var2 (str): an object variable
        Returns:
            True if the specified object variables are separable, False otherwise
        """
        return not self.objvars_unified(p_var1, p_var2)

    def objvars_separated(self, p_var1:str, p_var2:str) -> bool:
        """
        Query used to determine whether two object variables are separated,
        i.e. are not unifiable.
        Arguments:
            p_var1 (str): an object variable
            p_var2 (str): an object variable
        Returns:
            True if the specified object variables are separated, False otherwise
        """        
        return not self.objvars_unifiable(p_var1, p_var2)

    #def timepoint_domain(self, p_var:str) -> Domain:
    #    """
    #    Wrapper used to access the domain object of an object variable (through the BCN)
    #    Arguments:
    #        p_var (str): an object variable
    #    Returns:
    #       The domain (Domain) of the specified object variable 
    #    """
    #    return self.m_bcn.m_domains[p_var]

    # "mixed constraints" (involving both temporal and object variables) are dealt with using both constraints networks
    def propagate_constraints(
        self,
        p_input_constraints:typing.List[typing.Tuple[ConstraintType,typing.Any]],
        p_just_checking_no_propagation=False,
    ) -> bool:
        """
        Method allowing to (partially, locally) propagate the specified constraints, by triggering (non independent) constraint propagation for both the BCN and STN.
        Arguments:
            p_input_constraints (list((ConstraintType, constraint))):
                List of input constraints to (attempt to) propagate.
                The constraints are formatted the following way:
                    if TEMPORAL:
                        (timepoint1, timepoint2, objvar, strict_or_not) or (timepoint1, timepoint2, constant value, strict_or_not)
                        In the second case, a new object variable with singleton domain contain the constant value is created and used to represent the constraint
                    if DOMAIN_VAL_GE or DOMAIN_VAL_LE:
                        (objvar, value)
                    if UNIFICATION or SEPARATIon:
                        (objvar1, objvar2)
                        This method automatically dispatches symmetric constraints, as required by the constraint propagation function for the BCN.
                        No need to manually input symmetric constraints to this method.
                    if DISJ_UNIFICATION:
                        (objvar1, [objvar2, objvar3, ...])
                    if GENERAL_RELATION:
                        (relation_name, ([param_objvars...], [[objvars_values...]...]))
            p_just_checking_no_propagation (bool):
                Indicates whether to restore the changes to the constraint networks and domains as they were before, i.e. just checking propagation, not applying it.
        Returns:
            True if the constraints can be successfully propagated
        Side effects:
            If p_just_checking_no_propagation is False (by default), the constraints and changes introduced to the constraint networks and domains will be saved if propagation is successful.
            If it is True, then even if propagation is successful, the changes will be reverted and there won't be any side effects.
            Currently these backups and restorations are managed through deepcopies... (inefficient and not elegant). 
        """
        binding_constraints_worklist = []
        temporal_constraints_worklist = []
        
        _temp_bin_constrs:typing.Dict[typing.Tuple[str,str],typing.Set[ConstraintType]] = {}
        for (cstr_type, cstr) in p_input_constraints:
            if cstr_type == ConstraintType.TEMPORAL:
                temporal_constraints_worklist.append(cstr)
            else:
                if cstr_type == ConstraintType.UNIFICATION or cstr_type == ConstraintType.SEPARATION:
                    if not cstr in _temp_bin_constrs or not cstr_type in _temp_bin_constrs[cstr]:
                        _temp_bin_constrs[cstr] = set([cstr_type])
                        binding_constraints_worklist.append((cstr_type,(cstr[0],cstr[1])))
                        binding_constraints_worklist.append((cstr_type,(cstr[1],cstr[0])))
                    else:
                        warnings.warn("No need to feed symmetric binary constraints to this function... It takes care of it by itself.")
                else:
                    binding_constraints_worklist.append((cstr_type,cstr))
        
        self.m_bcn.backup()
        self.m_stn.backup()

        if (self.m_bcn._propagate(binding_constraints_worklist,self.m_stn)
            and self.m_stn._propagate(temporal_constraints_worklist,self.m_bcn)
        ):
            if p_just_checking_no_propagation:
                self.m_bcn.restore()
                self.m_stn.restore()
            return True

        self.m_bcn.restore()
        self.m_stn.restore()
        return False

############################################

# 22 / 08 / 2022

# The binding constraint network is implemented here.
# Additional info is available in the notes for constraints.py,
# notably on the linkage in constraint propagation between the STN and BCN.

# Currently, (partial) propagation is achieved through full lookahead arc consistency (aka maintaining arc consistency) based on AC-3.
# In the future, adapting a more advanced constraint propagation algorithm would be desirable.

# Representations and data structures aren't very optimised, except for the Union-Find for the unification/equality constraint.
# Relation tables are represented in a very crude way. In the future, a B-tree or BDD representation could be used.
# Support for "functional" (continuous) relations would be very welcome in the future

# As a side note, the union-find isn't necessarily that suitable if we want to support ordering constraints,
# as using a simple adjacency list representation of a graph (just like for the other constraints) would be easier for that.
# However, we would lose the efficiency of the lookups and connected component fetching.

# Extensions accounting for uncertainty, probability, and partial observability are kept for later.

############################################

class BCN():

    def __init__(self):
        self.m_domains: typing.Dict[str, Domain] = {}
        self.m_unifications: UnionFind2 = UnionFind2()
        self.m_disj_unifications: typing.Dict[str,typing.Set[str]] = {}
        self.m_separations: typing.Dict[str,typing.Set[str]] = {}
        #self.m_orderings: typing.Dict[str,typing.Set[str]] = {}
        self.m_general_relations: typing.Dict[str,typing.Tuple[typing.Tuple[str,...], typing.List[typing.Tuple[object,...]]]] = {}
        # general relations inefficient but good enough for now... B+Tree ???
        # linear arithmetic constraints ? for example for durations...
        # ANSWER : through general relation constraints (corresponding to formula) and fape-type linking between binding constraint net and temporal net
        self.m_old_domains: typing.Dict[str, Domain] = {}
        self.m_old_unifications: UnionFind2 = UnionFind2()
        self.m_old_disj_unifications: typing.Dict[str,typing.Set[str]] = {}
        self.m_old_separations: typing.Dict[str,typing.Set[str]] = {}
        #self.m_old_orderings: typing.Dict[str,typing.Set[str]] = {}
        self.m_old_general_relations: typing.Dict[str,typing.Tuple[typing.Tuple[str,...], typing.List[typing.Tuple[object,...]]]] = {}

    def backup(self) -> None:
        """
        Backs up collections (deep copies...)
        Returns:
            None
        Side effects:
            Backs up collections (deep copies...)
        """
        self.m_old_domains = deepcopy(self.m_domains)
        self.m_old_unifications = deepcopy(self.m_unifications)
        self.m_old_disj_unifications = deepcopy(self.m_disj_unifications)
        self.m_old_separations = deepcopy(self.m_separations)
        #self.m_old_orderings = deepcopy(self.m_orderings)
        self.m_old_general_relations = deepcopy(self.m_general_relations)

    def restore(self) -> None:
        """
        Restores collections from their backup
        Returns:
            None
        Side effects:
            Restores collections from their backup
        """
        self.m_domains = self.m_old_domains
        self.m_unifications = self.m_old_unifications
        self.m_disj_unifications = self.m_old_disj_unifications
        self.m_separations = self.m_old_separations
        #self.m_old_orderings = self.m_old_orderings
        self.m_general_relations = self.m_old_general_relations

    def clear(self) -> None:
        """
        Clears (by reinstantiating) all collections (including backups)
        Returns:
            None
        Side effects:
            Clears (by reinstantiating) all collections (including backups)
        """
        self.m_domains = {}
        self.m_unifications = UnionFind2()
        self.m_disj_unifications = {}
        self.m_separations = {}
        #self.m_orderings = {}
        self.m_general_relations = {}

        self.m_old_domains = {}
        self.m_old_unifications = UnionFind2()
        self.m_old_disj_unifications = {}
        self.m_old_separations = {}
        #self.m_old_orderings = {}
        self.m_old_general_relations = {}

    # full lookahead (maintaining) arc consistency, based on ac3
    # quite an inefficient implementation because of the copying etc...
    # ideally - the domain objects should only contain pointers to values, not the values themselves
    # that way, we can get away with feeding this function with shallow copies - or maybe even find a better way than that - instead of deep copies
    # the implementation also has some temporary/caching shallow copies of lists/sets which ideally would have to be dealt with 
    # also the representation of table relations is (way too) naive - but is enough for now
    def _propagate(
        self,
        p_input_constraints:typing.List[typing.Tuple[ConstraintType,typing.Any]],
        p_stn:STN=None
    ) -> bool:
        """
        Performs full lookahead (maintaining) arc consistency, based on AC-3
        Requires (unification and separation) input constraints to be symmetric, i.e. both (var1, var2) AND (var2, var1)
        Called from ConstraintNetwork.propagate_constraints_partial, which takes care of including both symmetric constraints.
        Arguments:
            p_input_constraints:
                Input constraints, fed to the method in the same format as in ConstraintNetwork.propagate_constraints_partial (but without the temporal constraints, obviously)
            p_stn (STN):
                STN to interface with.
        Returns:
            True if (partial/local, arc-consistent) constraint propagation was successful, False otherwise
        Side effects:
            Modifies domains object and collection storing constraints during propagation.
        """
        #file:///home/nrealus/T%C3%A9l%C3%A9chargements/781f16-3.pdf
        worklist = p_input_constraints#list(p_input_constrs)
        while (len(worklist) > 0):

            (constr_type, constr) = worklist.pop(0)

            change_info = []
            var1 = None
            val = None
            var2 = None
            var2list = []

            if (constr_type == ConstraintType.DOMAIN_VAL_LEQ or constr_type == ConstraintType.DOMAIN_VAL_LE
                or constr_type == ConstraintType.DOMAIN_VAL_GEQ or constr_type == ConstraintType.DOMAIN_VAL_GE):

                var1 = constr[0]
                val = constr[1]
                if constr_type == ConstraintType.DOMAIN_VAL_LEQ or constr_type == ConstraintType.DOMAIN_VAL_LE:
                    changed = self.m_domains[var1].restrict_to_ls(val, constr_type == ConstraintType.DOMAIN_VAL_LE)
                elif constr_type == ConstraintType.DOMAIN_VAL_GEQ or constr_type == ConstraintType.DOMAIN_VAL_GE:
                    changed = self.m_domains[var1].restrict_to_gt(val, constr_type == ConstraintType.DOMAIN_VAL_GE)

                if changed:
                    change_info.append((var1,val))

                if self.m_domains[var1].is_empty():
                    return False

            elif constr_type == ConstraintType.UNIFICATION:
                
                var1 = constr[0]
                var2 = constr[1]
                
                if ((var1 in self.m_separations and var2 in self.m_separations[var1])
                    or (var2 in self.m_separations and var1 in self.m_separations[var2])
                ):
                    return False

                self.m_unifications.add_and_union(var1, var2)
                
                changed = self.m_domains[var1].intersection(self.m_domains[var2])
                if changed:
                    change_info.append((var1,var2))

                if self.m_domains[var1].is_empty():
                    return False
            
            elif constr_type == ConstraintType.DISJ_UNIFICATION:
                
                var1 = constr[0]
                var2list = constr[1]
            
                self.m_disj_unifications.setdefault(var1,set()).update(var2list)
            
                _temp = deepcopy(self.m_domains[var2list[0]]) # deep copy so that the actual domain of var2list[0] doesn't get modified in the loop
                for i in range(1,len(var2list)): # start at 1 instead of 0 because first element already taken care of on the previous line
                    _temp.union(self.m_domains[var2list[i]])
            
                changed = self.m_domains[var1].intersection(_temp)
                if changed:
                    change_info.append((var1,var2list))
            
                if self.m_domains[var1].is_empty():
                    return False

            elif constr_type == ConstraintType.SEPARATION:
                
                var1 = constr[0]
                var2 = constr[1]

                if ((self.m_unifications.contains([var1])
                    and self.m_unifications.contains([var2])
                    and self.m_unifications.find(var1) == self.m_unifications.find(var2))
                        or (self.m_domains[var1].size() == 1 and self.m_domains[var1].get_values() == self.m_domains[var2].get_values())
                ):
                    return False

                self.m_separations.setdefault(var1,set()).add(var2)

                changed = self.m_domains[var1].difference_if_other_is_singleton(self.m_domains[var2])
                if changed:
                    change_info.append((var1,var2))
                
                if self.m_domains[var1].is_empty():
                    return False

            #elif constr_type == ConstraintType.ORDER_LE or constr_type == ConstraintType.ORDER_GE:
            #    
            #    var1 = constr[0]
            #    var2 = constr[1]
            #
            #    self.m_separations.setdefault(var1,set()).add(var2)
            #
            #    changed = self.m_domains[var1].difference_if_other_is_singleton(self.m_domains[var2])
            #    if changed:
            #        change_info.append((var1,var2))
            #    
            #    if self.m_domains[var1].is_empty():
            #        return False

            elif constr_type == ConstraintType.GENERAL_RELATION:
                
                relation_name = constr[0]
                relation_param_vars = list(constr[1][0])
                relation_table = list(constr[1][1]) # copy (shallow) so that the input parameter doesn't get modified
                self.m_general_relations.setdefault(relation_name,(relation_param_vars,[]))[1].extend(relation_table)

                n_rows = len(self.m_general_relations[relation_name][1])
                for row in range(n_rows-1,-1,-1): # backwards loop, so no problem removing elements from list
                    for col in range(len(self.m_general_relations[relation_name][1][row])):
                        if not self.m_domains[relation_param_vars[col]].contains(self.m_general_relations[relation_name][1][row][col]):
                            self.m_general_relations[relation_name][1].pop(row)
                            break
                
                proj_doms:typing.Dict[str,Domain] = {}
                for col in range(len(relation_param_vars)):
                    var = relation_param_vars[col]
                    proj_doms[var] = Domain()
                    for row in range(len(self.m_general_relations[relation_name][1])):
                        proj_doms[var].add_discrete_value(self.m_general_relations[relation_name][1][row][col])

                for var in proj_doms:

                    changed = self.m_domains[var].intersection(proj_doms[var])
                    if changed:
                        change_info.append((var,relation_name))

                    if self.m_domains[var].is_empty():
                        return False

            while len(change_info) > 0:

                (var1, arg) = change_info.pop(0)
                val = None
                var2 = None
                var2list = []
                relname = None

                if constr_type == ConstraintType.DOMAIN_VAL_LE or constr_type == ConstraintType.DOMAIN_VAL_GE:
                    val = arg
                elif constr_type == ConstraintType.UNIFICATION or constr_type == ConstraintType.SEPARATION:
                    var2 = arg
                elif constr_type == ConstraintType.DISJ_UNIFICATION:
                    var2list = arg
                elif constr_type == ConstraintType.GENERAL_RELATION:
                    relname = arg

                if self.m_unifications.contains([var1]):
                    for v in self.m_unifications.connected_component(var1):
                        if v != var1 and v != var2:
                            worklist.append((ConstraintType.UNIFICATION,(v, var1)))
                            worklist.append((ConstraintType.UNIFICATION,(var1, v)))

                if var1 in self.m_disj_unifications:
                    worklist.append((ConstraintType.DISJ_UNIFICATION,(var1,list(self.m_disj_unifications[var1]))))
                for v in self.m_disj_unifications:
                    if v != var1 and var1 in self.m_disj_unifications[v]:
                        worklist.append((ConstraintType.DISJ_UNIFICATION,(v,list(self.m_disj_unifications[v]))))

                if var1 in self.m_separations:
                    for v in self.m_separations[var1]:
                        if v != var2: #and obviously v can't be having a separation with itself anyway
                            worklist.append((ConstraintType.SEPARATION,(v, var1)))
                            worklist.append((ConstraintType.SEPARATION,(var1, v)))

                for name in self.m_general_relations:
                    if name != relname and var1 in self.m_general_relations[name][0]:
                        worklist.append((ConstraintType.GENERAL_RELATION,(name, self.m_general_relations[name])))

                #if p_stn != None:
                #    if var1 in p_stn.m_involved_objvars:
                #        for cstr in p_stn.m_involved_objvars[var1]:
                #            # actually important, can't just get away with evaluating the max in stn. in case the max goes up, need to enforce previous, more restrictive max
                #            # htb : "helper temporal bound"
                #            #_htb_var = "_htb".join(hex(new_int_id()))
                #            #self.m_domains[_htb_var] = {self.m_domains[var1].max_value()}
                #            #if not p_stn._propagate([(cstr[0],cstr[1],_htb_var)],self):
                #            #    return False
                #            # although maybe that's precisely what we shouldn't do ? since it's not "least-constraining..."
                #            # + would allow "variable" bounds... 
                #
                # the thing is, we're using apsp computation / minimal network establishment for consistency checking of the stn
                # and if ever min(l)+min(u) < 0 (for -l <= x-y <= u) that will manifest in the apsp graph/matrix, with a < 0 value on the diagonal
                            

        return True

############################################

# 22 / 08 / 2022

# The simple temporal network is implemented here.
# Additional info is available in the notes for constraints.py,
# notably on the linkage in constraint propagation between the STN and BCN.

# Currently, propagation is done in a non-incremental way using a direct computation of
# the all-pairs shortest paths matrix using the Floyd-Warshall algorithm.
# In the future, a more subtle and efficient approach (Johnson's algorithm, Planken 2008 path consistency, others...)

# Extensions accounting for uncertainty, probability, and partial observability are kept for later.

# The linking with object variables (see constraints.py) is not complete yet, and must be slightly changed for easier support of "multivaluate duration" object variables.
# Moreover, this approach allows to support even more general duration constraints, such as t2 - t1 <= func(x1, ..., xn)
# where t1, t2 are timepoints (temporal variables) in this STN, xi are object variables, and func is a function.
# Indeed, we can use an object variable "delta" to constrain two timepoints in this STN
# and create a general relation constraint in the BCN binding delta to func(x1, ..., xn).
# |---------relation_delta_func------------|
# | x1 | x2 | ... | xn | delta             |
# | v1 | v2 | ... | vn | func(v1, ..., vn) |  (for example, v1+v2)
# (This table representation is discrete, but in the general case it can all be continuous.)
# (Although for that we'll have to find a way to represent continuous/functional relations in the BCN)

# Similarly, to "store" the duration of an edge as an object variable (for example to use it in functions or anything, really) - as opposed to just
# the _range_ of the duration, i.e. the bounds on the "time window" - we could define an object variable with [0,inf] domain, describing the (equal) lower *and* upper bounds on the
# edge to the reference "zero" time point, effectively describing the happening time of the time point, considering the domain on this variable will (via propagation) be reduced
# once it is dispatched or once it happens. If we do that for two time points, and make another object variable, defined with a "functional" general relation
# corresponding to the subtraction of those two variables, leaving us with an object variable containing the duration between two dispatched time points.
# Maybe there is a slightly more straightforward approach for that

# Consistency, constraint propagation etc currently works only in a very least-constraining fashion.
# This is currently why when the domain of a  no constraint propagation
# Dealing with variable temporal constraints is a quite tricky, and not every result and approach carries on trivially from typical "constant" STNs.
# Reasoning in this situation may introduce "conditional", "branching" reasoning, which is way too complex to tackle now. This is investigated in Pralet 2014 (Time-dependent STNs).
# Finally, we probably would need to investigate how the planning search interleaved with partial/incremental uncertainty grounding ("Charlie and Eve search nodes")
# behaves with simple temporal constraints first, as more complex variable/functional temporal constraints (with non-singleton domains for object variables describing bounds)
# can be seen as a form of uncertainty themselves. That may shed some light on whether more complex STNs may introduce additional difficulties for planning search,
# local/full consistency maintaining (due to, for example, possibly unreasonable branching to deal with various cases of variable/functional temporal constraints)

# There is absolutely no need to pursue this line of work for now.

############################################

class STN():

    def __init__(self):
        self.m_controllability: typing.Dict[str, bool] = {}
        # bool indicates whether the variable is controllable or not. Zero time point controllable ? idk
        self.m_constraints: typing.Dict[typing.Tuple[str,str],typing.Set[(str,bool)]] = {}
        self.m_involved_objvars: typing.Dict[str, typing.Set[typing.Tuple[str,str]]] = {}
        # to deal with general temporal constraints of form t1 - t2 <= f(x1, ... , xn)
        # for now : only var
        # constraints of the form : t1 - t2 <= d (object variable) are interpreted as : t1 - t2 <= max{ v | v € dom(v) } : dom(v) = domain of v in binding constr net
        self.m_minimal_network: typing.Dict[typing.Tuple[str,str],float] = {}
        # ("dist_STN(t1,t2) is the minimal delay between t1 and t2, which is given in the minimal network")

        self.m_old_controllability: typing.Dict[str, bool] = {}
        self.m_old_constraints: typing.Dict[typing.Tuple[str,str],typing.Set[(str,bool)]] = {}
        self.m_old_involved_objvars: typing.Dict[str, typing.Set[typing.Tuple[str,str]]] = {}
        self.m_old_minimal_network: typing.Dict[typing.Tuple[str,str],float] = {}

    def backup(self) -> None:
        """
        Backs up collections (deep copies...)
        Returns:
            None
        Side effects:
            Backs up collections (deep copies...)
        """
        self.m_old_controllability = deepcopy(self.m_controllability)
        self.m_old_constraints = deepcopy(self.m_constraints)
        self.m_old_involved_objvars = deepcopy(self.m_involved_objvars)
        self.m_old_minimal_network = deepcopy(self.m_minimal_network)

    def restore(self) -> None:
        """
        Restores collections their backup
        Returns:
            None
        Side effects:
            Restores collections their backup
        """
        self.m_controllability = self.m_old_controllability
        self.m_constraints = self.m_old_constraints
        self.m_involved_objvars = self.m_old_involved_objvars
        self.m_minimal_network = self.m_old_minimal_network

    def clear(self) -> None:
        """
        Clears (by reinstantiating) all collections (including backups)
        Returns:
            None
        Side effects:
            Clears (by reinstantiating) all collections (including backups)
        """
        self.m_controllability = {}
        self.m_constraints = {}
        self.m_involved_objvars = {}
        self.m_minimal_network = {}

        self.m_old_controllability = {}
        self.m_old_constraints = {}
        self.m_old_involved_objvars = {}
        self.m_old_minimal_network = {}
        
    def size(self) -> int:
        """
        Returns:
            Returns the number of time points in the STN (int)
        """
        return len(self.m_controllability)

    def _propagate(
        self,
        p_input_constraints:typing.List[typing.Tuple[str,str,str|float,bool]],
        p_bcn:BCN
    ) -> bool:
        """
        Propagates input constraints to network.
        Called from ConstraintNetwork.propagate_constraints_partial, which takes care of including both symmetric constraints.
        Arguments:
            p_input_constraints:
                Input constraints, fed to the method in the same format as in ConstraintNetwork.propagate_constraints_partial (only temporal ones, obviously)
            p_bcn (BCN):
                BCN to interface with.
        Returns:
            True if constraint propagation was successful, False otherwise
        Side effects:
            Modifies collections during propagation.
        """
        worklist = p_input_constraints#list(p_input_constraints)
        for (t1, t2, bound, strict) in worklist:
            
            if type(bound) is str:
                var = bound
            else:
                var = "_hcov_{0}".format(new_int_id()) # hcov = helper constant object variable
                p_bcn.m_domains[var] = Domain(DomainType.DISCRETE, [bound])

            #if t1 == t2 and (p_bcn.m_domains[var].max_value() < 0 or (strict and p_bcn.m_domains[var].max_value() == 0)):
            #    return False
            #elif t1 == t2:
            #    continue
            #else:
            self.m_controllability.setdefault(t1,True)# will deal with controllability later
            self.m_controllability.setdefault(t2,True)# will deal with controllability later

            self.m_constraints.setdefault((t1,t2),set()).add((var,strict))
            self.m_involved_objvars.setdefault(var,set()).add((t1,t2))
            if (t2,t1) in self.m_constraints: # (t2,t1), not (t1,t2) !!!!!
                for (other_var, strict) in self.m_constraints[(t2,t1)]:
                    if strict:
                        cstr_type = ConstraintType.DOMAIN_VAL_GE
                    else:
                        cstr_type = ConstraintType.DOMAIN_VAL_GEQ
                    if (not p_bcn._propagate([(cstr_type,(other_var,-p_bcn.m_domains[var].max_value()))], self)):
                        return False

        res = self._apsp(p_bcn)
        for v in self.m_controllability:
            if res[(v,v)] < 0:
                return False

        self.m_minimal_network = res
        return True
        #return (variables, constraints, res)

    def _apsp(self, p_bcn:BCN): # floyd warshall apprach
        
        res = dict(self.m_minimal_network)
        for q in self.m_controllability:
            for u in self.m_controllability:
                for v in self.m_controllability:
                    res[(u,v)] = min(
                        res.setdefault((u,v),self._eval((u,v), p_bcn)),
                        res.setdefault((u,q),self._eval((u,q), p_bcn)) + res.setdefault((q,v),self._eval((q,v), p_bcn)))
        return res

    def _eval(self, p_cstr:typing.Tuple[str,str], p_bcn:BCN):

        if p_cstr in self.m_constraints:
            min = 0
            res = math.inf
            for (objvar, strict) in self.m_constraints[p_cstr]:
                if not strict:
                    min = p_bcn.m_domains[objvar].max_value()
                else:
                    min = p_bcn.m_domains[objvar].max_value() - sys.float_info.epsilon
                if min <= res:
                    res = min
            return min
        else:
            return math.inf