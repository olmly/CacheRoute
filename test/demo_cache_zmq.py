import time
import zmq

ctx = zmq.Context()
sock = ctx.socket(zmq.PUB)
sock.bind("tcp://127.0.0.1:5557")

time.sleep(1.0)

events = [
    {
        "event_type": "BlockStored",
        "namespace": "demo-model::default_tokenizer::kvcache",
        "instance_id": "hp_127.0.0.1:9001",
        "chunk_key": "chunk-demo-1",
        "device": "gpu",
        "occurred_at": int(time.time() * 1000),
        "prefix_key": "prefix-demo-1",
        "chunk_keys": ["chunk-demo-1"],
    },
    {
        "event_type": "BlockStored",
        "namespace": "demo-model::default_tokenizer::kvcache",
        "instance_id": "hp_127.0.0.1:9001",
        "chunk_key": "chunk-demo-2",
        "device": "gpu",
        "occurred_at": int(time.time() * 1000),
        "prefix_key": "prefix-demo-1",
        "chunk_keys": ["chunk-demo-1", "chunk-demo-2"],
    },
    {
        "event_type": "BlockStored",
        "namespace": "demo-model::default_tokenizer::kvcache",
        "instance_id": "hp_127.0.0.1:9002",
        "chunk_key": "chunk-demo-1",
        "device": "cpu",
        "occurred_at": int(time.time() * 1000),
    },
]

for ev in events:
    sock.send_json(ev)
    time.sleep(0.2)

time.sleep(0.5)
sock.close()
ctx.term()
print("done")