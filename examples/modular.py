import numpy as np
import tensorflow as tf

import bayesflow as bf
import bayesflow.util as util

from bayesflow.models import JMContext
from bayesflow.models.elementary import Gaussian, BernoulliMatrix, BetaMatrix, DirichletMatrix
from bayesflow.models.factorizations import *
from bayesflow.models.transforms import DeterministicTransform, Exp
from bayesflow.models.neural import VAEEncoder, VAEDecoderBernoulli, init_weights, init_zero_vector
from bayesflow.models.train import optimize_elbo, print_inference_summary

"""
Examples / test cases for a new API allowing construction of
models and inference routines from modular components.
"""


def gaussian_mean_model():

    with JMContext() as jm:
        mu = Gaussian(mean=0, std=10, shape=(1,), name="mu")
        X = Gaussian(mean=mu, std=1, shape=(100,), name="X")

    sampled = jm.sample(seed=0)
    sampled_X = sampled["X"]
    X.observe(sampled_X)
    jm.marginalize(mu)

    return jm
    
def gaussian_lowrank_model():
    with JMContext() as jm:
        A = Gaussian(mean=0.0, std=1.0, shape=(100, 3), name="A")
        B = Gaussian(mean=0.0, std=1.0, shape=(100, 3), name="B")
        C = NoisyGaussianMatrixProduct(A=A, B=B, std=0.1, name="C")

    sampled = jm.sample(seed=0)
    sampled_C = sampled["C"]
    C.observe(sampled_C)
    jm.marginalize(A)
    jm.marginalize(B)

    return jm
    
def gaussian_randomwalk_model():
    with JMContext() as jm:
        A = Gaussian(mean=0.0, std=1.0, shape=(100, 2), name="A")
        C = NoisyCumulativeSum(A=A, std=0.1, name="C")

    sampled = jm.sample(seed=0)
    C.observe(sampled["C"])
    jm.marginalize(A)
    
    return jm

def clustering_gmm_model(n_clusters = 4,
                         cluster_center_std = 5.0,
                         cluster_spread_std = 2.0,
                         n_points = 500,
                         dim = 2):

    with JMContext() as jm:
        centers = Gaussian(mean=0.0, std=cluster_center_std, shape=(n_clusters, dim), name="centers")
        weights = DirichletMatrix(alpha=1.0,
                                  shape=(n_clusters,),
                                  name="weights")
        X = GMMClustering(weights=weights, centers=centers,
                          std=cluster_spread_std, shape=(n_points, dim), name="X")

        
    sampled = jm.sample(seed=0)
    X.observe(sampled["X"])

    jm.marginalize(centers)
    jm.marginalize(weights)
    
    return jm

def latent_feature_model():
    K = 3
    D = 10
    N = 100

    a, b = np.float32(1.0), np.float32(1.0)

    with JMContext() as jm:
        pi = BetaMatrix(alpha=a, beta=b, shape=(K,), name="pi")
        B = BernoulliMatrix(p=pi, shape=(N, K), name="B")
        G = Gaussian(mean=0.0, std=1.0, shape=(K, D), name="G")
        D = NoisyLatentFeatures(B=B, G=G, std=0.1, name="D")
        
    sampled = jm.sample(seed=0)
    D.observe(sampled["D"])
    jm.marginalize(pi)
    jm.marginalize(B)
    jm.marginalize(G)

    return jm


def sparsity():
    with JMContext() as jm:
        G1 = Gaussian(mean=0, std=1.0, shape=(100,10), name="G1")
        expG1 = DeterministicTransform(G1, Exp, name="expG1")
        X = MultiplicativeGaussianNoise(expG1, 1.0, name="X")

    sampled = jm.sample()
    X.observe(sampled["X"])

    jm.marginalize(G1)
    
    return jm

def autoencoder():
    d_z = 2
    d_hidden=256
    d_x = 28*28
    N=100

    from util import get_mnist
    Xdata, ydata = get_mnist()
    Xbatch = Xdata[0:N]
    
    def init_decoder_params(d_z, d_hidden, d_x):
        # TODO come up with a simpler/more elegant syntax for point weights.
        # maybe just let the decoder initialize and manage its own weights / q distributions? 
        w_decode_h = DeltaQDistribution(init_weights(d_z, d_hidden))
        w_decode_h2 = DeltaQDistribution(init_weights(d_hidden, d_x))
        b_decode_1 = DeltaQDistribution(init_zero_vector(d_hidden))
        b_decode_2 = DeltaQDistribution(init_zero_vector(d_x))
        
        w1 = FlatDistribution(value=np.zeros((d_z, d_hidden), dtype=np.float32), fixed=False, name="w1")
        w1.attach_q(w_decode_h)
        w2 = FlatDistribution(value=np.zeros(( d_hidden, d_x), dtype=np.float32), fixed=False, name="w2")
        w2.attach_q(w_decode_h2)
        b1 = FlatDistribution(value=np.zeros((d_hidden,), dtype=np.float32), fixed=False, name="b1")
        b1.attach_q(b_decode_1)
        b2 = FlatDistribution(value=np.zeros((d_x), dtype=np.float32), fixed=False, name="b2")
        b2.attach_q(b_decode_2)
        
        return w1, w2, b1, b2

    w1, w2, b1, b2 = init_decoder_params(d_z, d_hidden, d_x)
    z = GaussianMatrix(mean=0, std=1.0, output_shape=(N,d_z), name="z")
    X = VAEDecoderBernoulli(z, w1, w2, b1, b2, name="X")
    
    X.observe(Xbatch)
    tfX = tf.constant(Xbatch, dtype=tf.float32)

    q_z = VAEEncoder(tfX, d_hidden, d_z)
    z.attach_q(q_z)
    
    return X

def main():

    """
    print "gaussian mean estimation"
    model = gaussian_mean_model()
    posterior = model.train(steps=500)
    print posterior

    
    print "gaussian matrix factorization"
    model = gaussian_lowrank_model()
    posterior = model.train(steps=500)
    print posterior

    
    print "gaussian random walk"
    model = gaussian_randomwalk_model()
    posterior = model.train(steps=1000)
    print posterior

    print "gaussian mixture model"
    model = clustering_gmm_model()
    posterior = model.train(steps=1000)
    print posterior

    
    print "latent features"
    model = latent_feature_model()
    posterior = model.train(steps=1000)
    print posterior
    """
    
    print "bayesian sparsity"
    model = sparsity()
    posterior = model.train(steps=1000)
    print posterior

    return
    
    print "variational autoencoder"
    model = autoencoder()
    posterior = model.train(steps=1000, adam_rate=0.001)
    print posterior
    
    
if __name__ == "__main__":
    main()
