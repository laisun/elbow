import numpy as np
import tensorflow as tf

import uuid
import copy

from bayesflow.models import ConditionalDistribution, WrapperNode, JMContext

class JointModel(ConditionalDistribution):

    def __init__(self, name=None):
        self._components = []
        self._components_by_key = {}

        self._explicit_marginalizations = {}
        self._explicit_marginalizations_reverse = {} # TODO make this a generic lookup table provided when we attach an explicit variational model
        self._explicit_variational_model = None

        self.inputs_random = {}
        self.inputs_nonrandom = {}

        if name is None:
            name =  str(uuid.uuid4().hex)[:6]
        self.name = name
        
    def extend(self, d):
        """
        Extend the joint model to include a new ConditionalDistribution d. 
        We require the joint model be built in topological order, so 
        all of d's parent ConditionalDistributions must already be part of the joint model. 
        Any inputs *not* part of the current joint model are assumed to be free inputs of 
        the joint model. 
        """

        if d.model is None:
            d.model = self
        else:
            assert(d.model == self)
        
        for param, node in d.inputs_random.items():
            if node not in self._components:
                self.inputs_random[(d, param)] = node
                
        self._components.append(d)

    def _input_shape(self, param):
        (node, subparam) = param
        return node._input_shape(subparam)

    def variational_model(self):
        # TODO this is still really sketchy.
        # basic idea is that we want a variational model to
        # attach a q to *every* output of a CD.
        # the simplest way to do this is to build up an explicit
        # joint variational model and attach it. BUT I want to support
        # the monadic 'marginalize' syntax, which is essentially building
        # up a mean field variational model piece-by-piece
        
        if self._explicit_variational_model is None:
            vm_joint = JointModel()
            for (vname, qdist) in self._explicit_marginalizations.items():
                vm_joint.extend(qdist)

            self._explicit_variational_model = vm_joint

        return self._explicit_variational_model
     
    def marginalize(self, model_var, q_dist=None):
        """
        model_var: name of one of this JointModel's output variables
        q_dist: a Q distribution with which to integrate out that variable

        Result: this JointModel loses model_var as an output variable, 
                but gains the inputs of the Q distribution as parameters
                to be optimized. 
        """
        if q_dist is None:
            q_dist = model_var._default_variational_model()

        self._explicit_marginalizations[model_var] = q_dist
        self._explicit_marginalizations_reverse[q_dist] = model_var
        
    def _match_variational_names(self, variational_sample):

        """
        TODO implement method to convert keys from variational model RV names to object model names
        """
        
        renamed_sample = {}
        for (vkey, val) in variational_sample.items():
            vnode, vname = vkey
            try:
                mkey = self._explicit_marginalizations_reverse[vnode]
            except KeyError:
                continue
            print "WARNING IGNORING VARIATIONAL NAME", vname
            renamed_sample[mkey] = val
        return renamed_sample

    def attach_variational_model(self, vm):
        # given a variational model, figure out which variational
        # names correspond to which object-level names. this means we
        # require either that the names match, or that we provide an
        # explciit mapping...
        raise Exception("not yet implemented")
    
    def observe(self, model_var, val):
        tf_value = tf.convert_to_tensor(val)
        q_dist = WrapperNode(tf_value, name="observed_" + model_var.name, model=None)
        self.marginalize(model_var, q_dist)


    def outputs(self):
        """The set of variables output by a JointModel includes all outputs
        of all components that have not been marginalized away.  Note
        that this is *not* just leaves of the DAG. The JointModel
        defined by p(A, B) = p(A)p(B|A) has both A and B as outputs.
        """
        non_marginalized = [node for node in self._components if node not in self._explicit_marginalizations.keys()]
        return non_marginalized
        
    def _topo_sorted(self):
        # currently the extend method guarantees topo sorting,
        # in general we might need to actually run an algorithm here. 
        return self._components
            
    def _sample(self, **input_vals):

        """
        Given:
          input_vals: dict mapping names of inputs at this node, to TF nodes encoding sampled values
        Return:
          sampled: dict mapping names of outputs at this node, to TF nodes encoding sampled values
          sample_all_sources: Python fn that generates a feed_dict containing the base randomness
                              needed for these samples. 
        """

        sampled_vals = {(self, param): val for (param, val) in input_vals.items()}
        random_sources = {}
        
        for component in self._topo_sorted():
            random_sources.update(component._sampled_source)            
            sampled_vals[component.name] = component._sampled
            
        def sample_all_sources():
            return {placeholder : source() for (placeholder, source) in random_sources.items()}

            
        return sampled_vals, sample_all_sources

    def sample(self, seed=0):
        sampled_vals, sample_all_sources = self._sample()

        init = tf.initialize_all_variables()
        sess = tf.Session()
        sess.run(init)

        np.random.seed(seed)
        fd = sample_all_sources()
        return {name: sess.run(val, feed_dict=fd) for (name, val) in sampled_vals.items()}
        
    def _logp(self, **point_vals):
        """
        Compute a (stochastic estimate of) a lower bound on the log probability of 
        outputs (provided as point values) given inputs (also provided as point values),
        using a variational model to integrate over latents. 
        """
        
        vm = self.variational_model()
        q_sample, stochastic_eps_fn = vm._sample()
        q_entropy = vm._entropy_lower_bound()

        component_lps = []
        for component in self._topo_sorted():

            component_qs = {}
            for param in component.inputs().keys():
                try:
                    q_dist = self._explicit_marginalizations[component.inputs_random[param]]
                    component_qs["q_" + param] = q_dist
                except KeyError:
                    # assume nonrandom, continue...
                    continue

            q_dist = self._explicit_marginalizations[component]
            component_qs["q_result"] = q_dist
                
            expected_lp = component._expected_logp(**component_qs)
            component_lps.append(expected_lp)
            
        joint_lp = tf.reduce_sum(tf.pack(component_lps))
        lp_bound = joint_lp + q_entropy
        
        return lp_bound, stochastic_eps_fn
    
    def _entropy_lower_bound(self):
        """
        TODO check this is correct. and deal with the case of hierarchical variational models that are themselves marginalized...
        """
        
        component_entropies = []
        for component in self._topo_sorted():
            input_samples = {param : sourcenode._sampled for param, sourcenode in component.inputs_random.items()}
            h = component._parameterized_entropy_lower_bound(**input_samples)
            component_entropies.append(h)

        joint_entropy = tf.reduce_sum(tf.pack(component_entropies))
        return joint_entropy

    def posterior(self, session, feed_dict=None):
        vm = self.variational_model()

        posterior_vals = {}
        for node in vm._components:
            d = node._optimized_params(session, feed_dict=feed_dict)
            if len(d) > 0:
                posterior_vals[node.name] = d
        return posterior_vals
        
    def train(self, steps=200, adam_rate=0.1, debug=False, return_session=False):
        elbo, sample_stochastic = self._logp()

        try:
            train_step = tf.train.AdamOptimizer(adam_rate).minimize(-elbo)
        except ValueError as e:
            print e
            steps = 0

        init = tf.initialize_all_variables()

        if debug:
            debug_ops = tf.add_check_numerics_ops()

        sess = tf.Session()
        sess.run(init)
        for i in range(steps):
            fd = sample_stochastic()

            if debug:
                sess.run(debug_ops, feed_dict = fd)

            sess.run(train_step, feed_dict = fd)

            elbo_val = sess.run((elbo), feed_dict=fd)
            print i, elbo_val

        posterior = self.posterior(sess, fd)

        #fd = sample_stochastic()    
        #elbo_terms = decompose_elbo(sess, fd)
        #posterior = inspect_posterior(sess, fd)

        #if return_session:
        #    return elbo_terms, posterior, sess, fd
        #else:
        #    sess.close()
        #    return elbo_terms, posterior
        return posterior
    

    """
    COPIED from ConditionalDistribution originally...
 
    def q_distribution(self):
        
        if self._q_distribution is None:
            default_q = self.default_q()

            # explicitly use the superclass method since some subclasses
            # may redefine attach_q to prevent user-attached q's
            ConditionalDistribution.attach_q(self, default_q)
            
        return self._q_distribution

    def attach_q(self, q_distribution):
        # TODO check that the types and shape of the Q distribution match
        if self._q_distribution is not None:
            raise Exception("trying to attach Q distribution %s at %s, but another distribution %s is already attached!" % (self._q_distribution, self, self._q_distribution))

        assert(self.shape == q_distribution.shape)
        
        self._q_distribution = q_distribution
    
    def observe(self, observed_val):
        qdist = ObservedQDistribution(observed_val)
        self.attach_q(qdist)
        return qdist

    def default_q(self):
        raise Exception("default Q distribution not implemented!")

    def init_q_true(self):
        for name, node in self.input_nodes.items():
            node.init_q_true()
        
        qdist = self.q_distribution()
        if not isinstance(qdist, ObservedQDistribution):
            try:
                qdist.initialize_to_value(self._sampled_value)
                print "initialized", self.name, qdist
            except Exception as e:
                print "cannot initialize node", self.name, "qdist", qdist, e
    """

