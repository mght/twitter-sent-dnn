"""
CNN for sentence modeling described in:
A Convolutional Neural Network for Modeling Sentence
"""
import sys, os, time
import pdb

import math, random
import numpy as np
import theano
import theano.tensor as T
import util

THEANO_COMPILE_MODE = "FAST_RUN"

from logreg import LogisticRegression
class WordEmbeddingLayer(object):
    """
    Layer that takes input vectors, output the sentence matrix
    """
    def __init__(self, rng, 
                 input,
                 vocab_size, 
                 embed_dm):
        """
        input: theano.tensor.dmatrix, (number of instances, sentence word number)
        
        vocab_size: integer, the size of vocabulary,

        embed_dm: integer, the dimension of word vector representation
        """                
        # # Note:
        # # assume the padding is the last row
        # # and it's constant to 0
        # pad_val = np.zeros((1, embed_dm))
        
        # embed_val = np.concatenate((embed_val_except_pad, pad_val), 
        #                            axis = 0)
        
        embedding_val = rng.uniform(
            low = -1,
            high = 1,
            size = (vocab_size, embed_dm)
        )
        
        embedding_val[vocab_size-1,:] = 0 # the <PAD> character is intialized to 0
        
        self.embeddings = theano.shared(
            np.asarray(embedding_val, 
                       dtype = theano.config.floatX),
            borrow = True,
            name = 'embeddings'
        )

        
        self.params = [self.embeddings]
        
        self.param_shapes = [(vocab_size, embed_dm)]
        
        # updated_embeddings = self.embeddings[:-1] # all rows are updated except for the last row
        
        # self.normalize = theano.function(inputs = [],
        #                                  updates = { self.embeddings:
        #                                              (self.embeddings/ 
        #                                               T.sqrt((self.embeddings**2).sum(axis=1)).dimshuffle(0,'x'))
        #                                          }
        # )

        # self.normalize() #initial normalization

        # Return:
        
        # :type, theano.tensor.tensor4
        # :param, dimension(1, 1, word embedding dimension, number of words in sentence)
        #         made to be 4D to fit into the dimension of convolution operation
        sent_embedding_list, updates = theano.map(lambda sent: self.embeddings[sent], 
                                                  input)
        sent_embedding_tensor = T.stacklists(sent_embedding_list) # make it into a 3D tensor
        
        self.output = sent_embedding_tensor.dimshuffle(0, 'x', 2, 1) # make it a 4D tensor
        
class DropoutLayer(object):
    """
    As the name suggests

    Refer to here: https://github.com/mdenil/dropout/blob/master/mlp.py
    """

    def __init__(self, input, rng, dropout_rate):

        srng = theano.tensor.shared_randomstreams.RandomStreams(
            rng.randint(999999))
        
        # p=1-p because 1's indicate keep and p is prob of dropping
        mask = srng.binomial(n=1, 
                             p=1-dropout_rate, 
                             size=input.shape)

        self.output = input * T.cast(mask, theano.config.floatX)
        
    
class ConvFoldingPoolLayer(object):
    """
    Convolution, folding and k-max pooling layer
    """
    def __init__(self, 
                 rng, 
                 input,
                 filter_shape,
                 k,
                 W = None,
                 b = None):
        """
        rng: numpy random number generator
        input: theano.tensor.tensor4
               the sentence matrix, (number of instances, number of input feature maps,  embedding dimension, number of words)
        
        filter_shape: tuple of length 4, 
           dimension: (number of filters, num input feature maps, filter height, filter width)
        
        k: int or theano.tensor.iscalar,
           the k value in the max-pooling layer

        W: theano.tensor.tensor4,
           the filter weight matrices, 
           dimension: (number of filters, num input feature maps, filter height, filter width)

        b: theano.tensor.vector,
           the filter bias, 
           dimension: (filter number, )
                
        """
        self.input = input
        self.k = k
        self.filter_shape = filter_shape

        if W is not None:
            W_val = W
        else:
            fan_in = np.prod(filter_shape[1:])
            
            fan_out = (filter_shape[0] * np.prod(filter_shape[2:]) / 
                       k) # it's 
            
            W_bound = np.sqrt(6. / (fan_in + fan_out))

            W_val = np.asarray(
                rng.uniform(low=-W_bound, high=W_bound, size=filter_shape),
                dtype=theano.config.floatX
            )

        self.W = theano.shared(
            value = np.asarray(W_val,
                               dtype = theano.config.floatX),
            name = "W",
            borrow=True
        )
        
        # make b
        if b is not None:
            b_val = b
        else:
            b_size = (filter_shape[0], )
            b_val = np.zeros(
                b_size, 
                dtype = theano.config.floatX
            )
            
        self.b = theano.shared(
            value = np.asarray(
                b_val,
                dtype = theano.config.floatX
            ),
            name = "b",
            borrow = True
        )

        self.params = [self.W, self.b]
        self.param_shapes = [filter_shape,
                             b_size ]

    def fold(self, x):
        """
        :type x: theano.tensor.tensor4
        """
        return (x[:, :, T.arange(0, x.shape[2], 2)] + 
                x[:, :, T.arange(1, x.shape[2], 2)]) / 2
        
    def k_max_pool(self, x, k):
        """
        perform k-max pool on the input along the rows

        input: theano.tensor.tensor4
           
        k: theano.tensor.iscalar
            the k parameter

        Returns: 
        4D tensor
        """
        ind = T.argsort(x, axis = 3)

        sorted_ind = T.sort(ind[:,:,:, -k:], axis = 3)
        
        dim0, dim1, dim2, dim3 = sorted_ind.shape
        
        indices_dim0 = T.arange(dim0).repeat(dim1 * dim2 * dim3)
        indices_dim1 = T.arange(dim1).repeat(dim2 * dim3).reshape((dim1*dim2*dim3, 1)).repeat(dim0, axis=1).T.flatten()
        indices_dim2 = T.arange(dim2).repeat(dim3).reshape((dim2*dim3, 1)).repeat(dim0 * dim1, axis = 1).T.flatten()
        
        return x[indices_dim0, indices_dim1, indices_dim2, sorted_ind.flatten()].reshape(sorted_ind.shape)
        
    @property
    def output(self):
        # non-linear transform of the convolution output
        conv_out = T.nnet.conv.conv2d(self.input, 
                                      self.W, 
                                      border_mode = "full") 
        
        # fold
        fold_out = self.fold(conv_out)
                
        # k-max pool        
        pool_out = (self.k_max_pool(fold_out, self.k) + 
                    self.b.dimshuffle('x', 0, 'x', 'x'))

        
        # return T.tanh(pool_out)
        # use recitifer
        return T.switch(pool_out > 0, pool_out, 0)

def train_and_test(
        use_adadelta = True,
        learning_rate = 0.1,
        delay_embedding_learning = True,
        epsilon = 0.000001,
        rho = 0.95,
        nkerns = [6, 12],
        embed_dm = 48,
        k_top = 5,
        L2_reg=0.00001,
        n_hidden = 500,
        batch_size = 500,
        n_epochs = 2000, 
        print_config = {}):

    ###################
    # get the data    #
    ###################
    datasets = util.stanford_sentiment('data/stanfordSentimentTreebank/trees/processed.pkl',
                                       corpus_folder = 'data/stanfordSentimentTreebank/trees/')
    
    train_set_x, train_set_y = datasets[0]
    valid_set_x, valid_set_y = datasets[1]
    test_set_x, test_set_y = datasets[2]
    word2index = datasets[3]
    index2word = datasets[4]

    n_train_batches = train_set_x.get_value(borrow=True).shape[0] / batch_size
    train_sent_len = train_set_x.get_value(borrow=True).shape[1]
    possible_labels =  set(train_set_y.get_value().tolist())
    
    
    ###################################
    # Symbolic variable definition    #
    ###################################
    x = T.imatrix('x') # the word indices matrix
    sent_len = x.shape[1]
    y = T.ivector('y') # the sentiment labels

    batch_index = T.iscalar('batch_index')
    
    rng = np.random.RandomState(1234)        
        
    
    ###############################
    # Construction of the network #
    ###############################

    # Layer 1, the embedding layer
    layer1 = WordEmbeddingLayer(rng, 
                                input = x, 
                                vocab_size = len(word2index), 
                                embed_dm = embed_dm)    
    
    # Layer 2: convolution&fold&pool layer
    filter_shape = (nkerns[0],
                    1, 
                    1, 10
    )
    
    layer2_k = int(max(k_top, 
                       math.ceil(.5 * train_sent_len)))

    dpl = DropoutLayer(
        input = layer1.output,
        rng = rng, 
        dropout_rate = 0.5
    ) 
    
    layer2 = ConvFoldingPoolLayer(rng, 
                                  input = dpl.output, 
                                  filter_shape = filter_shape, 
                                  k = layer2_k)
    
    # Layer 3: convolution&fold&pool layer
    filter_shape = (nkerns[1], 
                    nkerns[0],
                    1, 7 
    )
    
    dpl = DropoutLayer(
        input = layer2.output,
        rng = rng, 
        dropout_rate = 0.5
    ) 
    
    layer3 = ConvFoldingPoolLayer(rng, 
                                  input = dpl.output,
                                  filter_shape = filter_shape, 
                                  k = k_top)
    
    # Hiddne layer: dropout layer
    layer4 = DropoutLayer(
        input = layer3.output,
        rng = rng, 
        dropout_rate = 0.5
    )
    
    layer4_input = layer4.output.flatten(2) #make it into a row 
    
    
    # Softmax Layer
    model = LogisticRegression(
        rng,
        input = layer4_input, 
        n_in = nkerns[1] * k_top * embed_dm / 4, # we fold twice, so divide by 4
        n_out = len(possible_labels) # five sentiment level
    )

    ############################
    # L2 regularizer           #
    ############################
    
    L2_sqr = (
        0.00001 * (layer1.embeddings ** 2).sum()
        + 0.0003 * (layer2.W ** 2).sum()
        + 0.0003 * (layer3.W ** 2).sum()
        + 0.0001 * (model.W ** 2).sum()
    )
    
    ############################
    # Training function and    #
    # AdaDelta learning rate   #
    ############################
    cost = model.nnl(y) + L2_sqr
        
    if not delay_embedding_learning:
        params = (layer1.params + layer2.params + layer3.params + model.params)
        param_shapes=  (layer1.param_shapes + layer2.param_shapes + layer3.param_shapes + model.param_shapes)
    else:
        params = (layer2.params + layer3.params + model.params)
        param_shapes=  (layer2.param_shapes + layer3.param_shapes + model.param_shapes)

    # AdaDelta parameter symbols
    # E[g^2]
    # initialized to zero
    egs = [
        theano.shared(
            value = np.zeros(param_shape,
                             dtype = theano.config.floatX
                         ),
            borrow = True,        
            name = "Eg:" + param.name
        )
        for param_shape, param in zip(param_shapes, params)
    ]
    
    # E[\delta x^2]
    # initialized to zero
    exs = [
        theano.shared(
            value = np.zeros(param_shape,
                             dtype = theano.config.floatX
                         ),
            borrow = True,        
            name = "Ex:" + param.name
        )
        for param_shape, param in zip(param_shapes, params)
    ]
    
    param_grads = [T.grad(cost, param) for param in params]
    
    if use_adadelta:
        # AdaDelta parameter update
        # Update E[g^2]
        
        updates = [
            (eg, rho * eg + (1 - rho) * T.pow(param_grad, 2))
            for eg, param_grad, param_shape in zip(egs, param_grads, param_shapes)
        ]

        delta_x = [-(T.sqrt(ex + epsilon) / T.sqrt(eg + epsilon)) * param_grad
                   for eg, ex, param_grad in zip(egs, exs, param_grads)
        ]
        # More updates for the gradients
        param_updates = [
            (param, param + dx)
            for eg, ex, param, dx in zip(egs, exs, params, delta_x)
        ]

        updates += param_updates

        # # Last, updates for E[x^2]
        updates += [
            (ex, rho * ex + (1 - rho) * T.pow(dx, 2))
            for ex, dx in zip(exs, delta_x)
        ] 

    else:
        updates = [
            (param, param - learning_rate * param_grad)
            for param, param_grad in zip(params, param_grads)
        ]

    print updates

    train_model = theano.function(inputs = [batch_index],
                                  outputs = [cost], 
                                  updates = updates,
                                  givens = {
                                      x: train_set_x[batch_index * batch_size: (batch_index + 1) * batch_size],
                                      y: train_set_y[batch_index * batch_size: (batch_index + 1) * batch_size]
                                  },
    )

        
    train_error = theano.function(inputs = [],
                                  outputs = model.errors(y), 
                                  givens = {
                                      x: train_set_x,
                                      y: train_set_y
                                  }, 
    )

    valid_error = theano.function(inputs = [],
                                  outputs = model.errors(y), 
                                  givens = {
                                      x: valid_set_x,
                                      y: valid_set_y
                                  }, 
                                  # mode = "DebugMode"
    )
    
    #############################
    # Debugging purpose code    #
    #############################
    # : PARAMETER TUNING NOTE:
    # some demonstration of the gradient vanishing probelm
    
    if print_config["nnl"]:
        get_nnl = theano.function(
            inputs = [batch_index],
            outputs = model.nnl(y),
            givens = {
                x: train_set_x[batch_index * batch_size: (batch_index + 1) * batch_size],
                y: train_set_y[batch_index * batch_size: (batch_index + 1) * batch_size]
            }
        )
        
    if print_config["L2_sqr"]:
        get_L2_sqr = theano.function(
            inputs = [],
            outputs = L2_sqr
        )
        
    if print_config["grad_abs_mean"]:
        print_grads = theano.function(
            inputs = [batch_index], 
            outputs = [theano.printing.Print(param.name)(
                T.mean(T.abs_(param_grad))
            )
                       for param, param_grad in zip(params, param_grads)
                   ], 
            givens = {
                x: train_set_x[batch_index * batch_size: (batch_index + 1) * batch_size],
                y: train_set_y[batch_index * batch_size: (batch_index + 1) * batch_size]
            }
        )
    if print_config["lr"]:
        print_learning_rates = theano.function(
            inputs = [],
            outputs = [theano.printing.Print(ex.name)(
                T.mean(T.sqrt(ex + epsilon) / T.sqrt(eg + epsilon))
            ) 
                       for eg, ex in zip(egs, exs)]
        )

    if print_config["embeddings"]:
        print_embeddings = theano.function(
            inputs = [],
            outputs = theano.printing.Print("embeddings")(layer1.embeddings)
        )
    
    if print_config["logreg_W"]:
        print_logreg_W = theano.function(
            inputs = [],
            outputs = theano.printing.Print(model.W.name)(model.W)
        )

    if print_config["convlayer1_W"]:
        print_convlayer1_W = theano.function(
            inputs = [],
            outputs = theano.printing.Print(layer2.W.name)(layer2.W)
        )

    if print_config["convlayer2_W"]:
        print_convlayer2_W = theano.function(
            inputs = [],
            outputs = theano.printing.Print(layer3.W.name)(layer3.W)
        )

    if print_config["p_y_given_x"]:
        print_p_y_given_x = theano.function(
            inputs = [batch_index],
            outputs = theano.printing.Print("p_y_given_x")(model.p_y_given_x),
            givens = {
                x: train_set_x[batch_index * batch_size: (batch_index + 1) * batch_size]
            }
        )
    
    #the training loop
    patience = 10000  # look as this many examples regardless
    patience_increase = 2  # wait this much longer when a new best is
                                  # found
    improvement_threshold = 0.995  # a relative improvement of this much is
    # considered significant
                                  
    validation_frequency = min(n_train_batches, patience / 2)

    best_validation_loss = np.inf
    best_iter = 0

    start_time = time.clock()
    done_looping = False
    epoch = 0
    
    nnls = []
    L2_sqrs = []
    
    while (epoch < n_epochs) and (not done_looping):
        epoch += 1
        print "At epoch {0}".format(epoch)
        
        train_set_x_data = train_set_x.get_value(borrow = True)
        train_set_y_data = train_set_y.get_value(borrow = True)
        
        permutation = np.random.permutation(train_set_x.get_value(borrow=True).shape[0])

        train_set_x.set_value(train_set_x_data[permutation])
        train_set_y.set_value(train_set_y_data[permutation])
        
        for minibatch_index in xrange(n_train_batches):
           
            train_cost = train_model(minibatch_index)

            if print_config["nnl"]:
                nnls.append(get_nnl(minibatch_index))
                
            if print_config["L2_sqr"]:
                L2_sqrs.append(get_L2_sqr())

            if print_config["p_y_given_x"]:
                print_p_y_given_x(minibatch_index)

            if print_config["convlayer2_W"]:
                print_convlayer2_W()

            if print_config["convlayer1_W"]:
                print_convlayer1_W()
                
            if print_config["grad_abs_mean"]:
                print_grads(minibatch_index)

            # print_grads(minibatch_index)
            # print_learning_rates()
            # print_embeddings()
            # print_logreg_param()
            
            # iteration number
            iter = (epoch - 1) * n_train_batches + minibatch_index

            if (minibatch_index+1) % 50 == 0 or minibatch_index == n_train_batches - 1:
                print "%d / %d minibatches completed" %(minibatch_index + 1, n_train_batches)                
                if print_config["nnl"]:
                    print "`nnl` for the past 50 minibatches is %f" %(np.mean(np.array(nnls)))
                    nnls = []
                if print_config["L2_sqr"]:
                    print "`L2_sqr`` for the past 50 minibatches is %f" %(np.mean(np.array(L2_sqrs)))
                    L2_sqrs = []
                

            if (iter + 1) % validation_frequency == 0:
                print "At epoch %d and minibatch %d. \nTrain error %.2f%%\nDev error %.2f%%\n" %(
                    epoch, 
                    minibatch_index,
                    train_error() * 100, 
                    valid_error() * 100
                )
    
    end_time = time.clock()
    print(('Optimization complete. Best validation score of %f %% '
           'obtained at iteration %i, with test performance %f %%') %
          (best_validation_loss * 100., best_iter + 1, test_score * 100.))
    print >> sys.stderr, ('The code for file ' +
                          os.path.split(__file__)[1] +
                          ' ran for %.2fm' % ((end_time - start_time) / 60.))

    
if __name__ == "__main__":
    print_config = {
        "lr": False,
        "logreg_W": False,
        "logreg_b": False,
        "convlayer2_W": False,
        "convlayer1_W": False,
        "grad_abs_mean": False,
        "p_y_given_x": False,
        "embeddings": False,
        "nnl": True,
        "L2_sqr": True,
    }
    
    train_and_test(
        use_adadelta = False,
        delay_embedding_learning = True,
        learning_rate = 0.01, 
        batch_size = 50, 
        print_config = print_config
    )
