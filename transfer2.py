import numpy as np
import tensorflow as tf
import google.protobuf.text_format as pbtf

devices = (
    "/job:tge/replica:0/task:0/device:GPU:0",
    "/job:tge/replica:0/task:1/device:GPU:0",
)

server = tf.distribute.Server(tf.train.ClusterSpec({
    "tge": ["127.0.0.1:3901", "127.0.0.1:3902"]
}), job_name='tge', task_index=0, protocol="grpc")

for base in (1,3,5,7):
    with tf.device(devices[0]):
        p = tf.placeholder(dtype=tf.float64)

    with tf.device(devices[1]):
        x = tf.identity(p)
        x = tf.concat([x, x], 0)

    with tf.device(devices[0]):
        x = tf.identity(x)
        x = tf.concat([x, x], 0)

    with tf.device(devices[1]):
        x = tf.identity(x)
        x = tf.concat([x, x], 0)

    with tf.device(devices[0]):
        x = tf.identity(x)
        x = tf.concat([x, x], 0)

    with tf.device(devices[1]):
        x = tf.identity(x)
        x = tf.concat([x, x], 0)

    with tf.device(devices[0]):
        x = tf.identity(x)
        x = tf.concat([x, x], 0)

    with tf.device(devices[1]):
        x = tf.identity(x)

    run_meta = tf.compat.v1.RunMetadata()
    run_opt = tf.compat.v1.RunOptions(trace_level=tf.RunOptions.FULL_TRACE, output_partition_graphs=True)
    x = tf.Session(server.target).run(x, { p: np.random.uniform(size=(128*base, 1024)) }, options=run_opt, run_metadata=run_meta)
    print(x.shape)

    with open("meta{}.pb".format((base)), "w") as fo:
        fo.write(pbtf.MessageToString(run_meta))

# Another process (remember to restrict CUDA_VISIBLE_DEVICES):
# tf.distribute.Server(tf.train.ClusterSpec({
#     "tge": ["127.0.0.1:3901", "127.0.0.1:3902"]
# }), job_name='tge', task_index=1, protocol="grpc").join()

# %%capture
#
# import tensorflow as tf
# import google.protobuf.text_format as pbtf
# import json
#
# with open("meta.pb", "r") as f:
#     m = pbtf.Parse(f.read(), tf.compat.v1.RunMetadata())
#
# source = [x for dev in m.step_stats.dev_stats for x in dev.node_stats if x.node_name == '_SOURCE'][0].all_start_micros
#
# for dev in m.step_stats.dev_stats:
#     for node in dev.node_stats:
#         if 'edge' in node.node_name:
#             print(dev.device, node.node_name, node.all_start_micros - source, node.all_end_rel_micros)
