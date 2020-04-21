def model_fn():
    from tensorflow.contrib.slim.nets import vgg
    x = tf.placeholder(tf.float32, shape=(None, 224, 224, 3))
    y = tf.placeholder(tf.float32, shape=(None, 1000))
    output, _ = vgg.vgg_19(x, 1000)
    loss = tf.nn.sigmoid_cross_entropy_with_logits(labels=y, logits=output)
    optimizer = tf.train.GradientDescentOptimizer(0.2).minimize(tf.reduce_sum(loss))
    return optimizer

# def model_fn():
#     from tensorflow.contrib.slim.nets import resnet_v2
#     x = tf.placeholder(tf.float32, shape=(None, 224, 224, 3))
#     y = tf.placeholder(tf.float32, shape=(None, 1000))
#     output, _ = resnet_v2.resnet_v2_101(x, 1000)
#     output = tf.contrib.slim.flatten(output)
#     loss = tf.nn.sigmoid_cross_entropy_with_logits(labels=y, logits=output)
#     optimizer = tf.train.GradientDescentOptimizer(0.2).minimize(tf.reduce_sum(loss))
#     return optimizer

# def model_fn():
#     x = tf.placeholder(tf.float32, shape=(None, 1024))
#     y = tf.placeholder(tf.float32, shape=(None, 10,))
#     hidden = tf.contrib.slim.fully_connected(x, 256, activation_fn=tf.nn.softmax)
#     output = tf.contrib.slim.fully_connected(hidden, 10, activation_fn=tf.nn.softmax)
#     loss = tf.nn.sigmoid_cross_entropy_with_logits(labels=y, logits=output)
#     optimizer = tf.train.GradientDescentOptimizer(0.2).minimize(tf.reduce_sum(loss))
#     return optimizer

# def model_fn():
#     slim = tf.contrib.slim
#     x = tf.placeholder(tf.float32, shape=(None, 32, 32, 3))
#     y = tf.placeholder(tf.float32, shape=(None, 1000))
#     net = slim.conv2d(x, 32, [5, 5])
#     net = slim.max_pool2d(net, [2, 2], 2)
#     net = slim.conv2d(net, 64, [5, 5])
#     net = slim.max_pool2d(net, [2, 2], 2)
#     net = slim.flatten(net)
#     net = slim.fully_connected(net, 1024, activation_fn=tf.nn.sigmoid)
#     net = slim.fully_connected(net, 1000, activation_fn=None)
#     loss = tf.nn.sigmoid_cross_entropy_with_logits(labels=y, logits=net)
#     acc = tf.reduce_mean(tf.nn.softmax(net) * y)
#     optimizer = tf.train.GradientDescentOptimizer(0.001).minimize(tf.reduce_sum(loss))
#     return optimizer

# def model_fn():
#     from tensorflow.contrib.slim.nets import inception
#     x = tf.placeholder(tf.float32, shape=(None, 224, 224, 3))
#     y = tf.placeholder(tf.float32, shape=(None, 1000))
#     output, _ = inception.inception_v3(x, 1000)
#     loss = tf.nn.sigmoid_cross_entropy_with_logits(labels=y, logits=output)
#     optimizer = tf.train.GradientDescentOptimizer(0.2).minimize(tf.reduce_sum(loss))
#     return optimizer

import time
import tensorflow as tf
import google.protobuf.text_format as pbtf
from tensorflow.python.client import timeline
from tensorflow.distribute.cluster_resolver import TFConfigClusterResolver

import os
os.environ["TF_CONFIG"] = '{ "cluster": { "worker": ["10.28.1.24:3806", "10.28.1.16:3901", "10.28.1.17:3901"] }, "task": {"type": "worker", "index": 0} }'

def setup_workers(workers, protocol="grpc"):
    import urllib.request
    import time

    param = '/'.join(server.replace(':', '%3A') for server in workers)
    for task_id, server in enumerate(workers):
        if task_id == 0: continue
        url = "http://{}:3905/{}/restart/{}/{}/{}".format(server.split(':')[0], int(time.time()) + 10, protocol, task_id, param)
        assert urllib.request.urlopen(url).read() == b'ok'
    time.sleep(1)

setup_workers(["10.28.1.24:3806", "10.28.1.16:3901", "10.28.1.17:3901"])

BATCHSIZE=288

devices = (
    "/job:worker/replica:0/task:0/device:GPU:0",
    "/job:worker/replica:0/task:0/device:GPU:1",
    "/job:worker/replica:0/task:1/device:GPU:0",
    "/job:worker/replica:0/task:1/device:GPU:1",
    "/job:worker/replica:0/task:2/device:GPU:0",
    "/job:worker/replica:0/task:2/device:GPU:1"
)
resolver = TFConfigClusterResolver()
cluster = resolver.cluster_spec()
dist = tf.distribute.experimental.MultiWorkerMirroredStrategy(
        tf.distribute.experimental.CollectiveCommunication.NCCL)
config = dist.update_config_proto(tf.ConfigProto())
config.ClearField("device_filters")
server = tf.distribute.Server(cluster, job_name='worker', task_index=0, protocol="grpc", config=config)

opt = model_fn()
init = tf.global_variables_initializer()
gdef = tf.get_default_graph().as_graph_def(add_shapes=True)

with open("model.pb", "w") as fo:
    fo.write(pbtf.MessageToString(gdef))

import tge

# options = [[0, 1], [1, 0], [0, 2], [2, 0], [1, 1]]
# strategy = { node.name: [np.random.randint(0, 2)] + options[np.random.randint(0, len(options))] for node in gdef.node }

strategy = { node.name: [0, 1, 1, 1, 1, 1, 1] for node in gdef.node }

g = (tge.TGE(gdef, devices)
    .custom(strategy)
    .replace_placeholder(BATCHSIZE)
    .use_collective()
    # .verbose()
    .compile()
    .get_result()
)

with open("modified.pb", "w") as fo:
    fo.write(pbtf.MessageToString(g))

tf.reset_default_graph()
resolver = TFConfigClusterResolver()
cluster = resolver.cluster_spec()
dist = tf.distribute.experimental.MultiWorkerMirroredStrategy(
        tf.distribute.experimental.CollectiveCommunication.NCCL)
config = dist.update_config_proto(tf.ConfigProto())
config.ClearField("device_filters")
tf.import_graph_def(g)
graph = tf.get_default_graph()

opt = graph.get_operation_by_name("import/GradientDescent/replica_0")
init = graph.get_operation_by_name("import/init/replica_0")

sess = tf.Session(server.target, config=config)
sess.run(init)
sess.run(opt)

run_meta = tf.compat.v1.RunMetadata()
run_opt = tf.compat.v1.RunOptions(trace_level=tf.RunOptions.FULL_TRACE, output_partition_graphs=True)
sess.run(opt, options=run_opt, run_metadata=run_meta)

with open("meta.pb", "w") as fo:
    fo.write(pbtf.MessageToString(run_meta))

tl = timeline.Timeline(run_meta.step_stats)
with open("timeline.json", "w") as fo:
    fo.write(tl.generate_chrome_trace_format())

tic = time.perf_counter()
sess.run(opt)
toc = time.perf_counter()

import pickle
try:
    prof_dict = pickle.load(open("profile_data.pickle", "rb"))
except:
    from profiler import Profiler
    prof_dict = {}
    tf.reset_default_graph()
    opt = model_fn()
    init = tf.global_variables_initializer()
    gdef = tf.get_default_graph().as_graph_def(add_shapes=True)
    p = Profiler(gdef, BATCHSIZE // 6, server.target)
    for node in gdef.node:
        prof_dict[(node.name, 1)] = [ p.profile(node.name, device) // 5 for device in devices ]
        prof_dict[(node.name, 6)] = [ p.profile(node.name, device) for device in devices ]
    pickle.dump(prof_dict, open("profile_data.pickle", "wb"))

# from profiler import NcclProfiler
# nccl_model = NcclProfiler(devices, server.target).profile()
# print(nccl_model)

g = (tge.TGE(gdef, devices)
    .custom(strategy)
    .replace_placeholder(BATCHSIZE)
    .use_collective()
    # .verbose()
    # .set_bandwidth(intra=2810, inter=2810) # single worker g9
    # .set_nccl_model(nccl_model)
    .set_bandwidth(intra=2810, inter=816)
    .evaluate(prof_dict, "simulated.json")
)

print("actual: {}".format(toc - tic))
print("simulated: {}".format(g[0]))
print("memory: {}".format(g[1]))
