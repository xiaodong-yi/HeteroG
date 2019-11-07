#![allow(irrefutable_let_patterns)]
#![allow(dead_code, unused_imports)]
#![allow(non_camel_case_types)]
#![deny(bare_trait_objects)]
#![warn(clippy::all)]

use oh_my_rust::*;
use protobuf::{Message, parse_from_bytes};

mod proto;
mod graph;
mod strategy;
mod polishing;
mod scheduler;

// reason for this additional abstraction layer: trait object still requires specifying associate types. a Bundle groups a strategy and the graph together to remove the need.
trait AbstractBundle {
    fn plan_and_compile(&mut self, target: &mut graph::Target);
    fn build_graph(&mut self, iter: &[crate::proto::node_def::NodeDef]);
}

struct TheBundle<NEX: Default, TEX: Default, S: strategy::Strategy<NEX=NEX, TEX=TEX>> {
    strategy: S,
    graph: Option<Box<graph::Graph<NEX, TEX>>> // NOTE: we keep the graph in box since there are pointers inside nodes that refers to the graph. TODO: use Pin for the box to gurarantee that they are not moved
}

impl<NEX: Default, TEX: Default, S: strategy::Strategy<NEX=NEX, TEX=TEX>> TheBundle<NEX, TEX, S> {
    pub fn new(strategy: S) -> Self {
        Self { strategy, graph: None }
    }
}

impl<NEX: Default, TEX: Default, S: strategy::Strategy<NEX=NEX, TEX=TEX>> AbstractBundle for TheBundle<NEX, TEX, S> {
    fn plan_and_compile(&mut self, target: &mut graph::Target) {
        self.strategy.plan(self.graph.as_mut().unwrap(), target);
        self.graph.as_mut().unwrap().compile(target)
    }

    fn build_graph(&mut self, nodes: &[crate::proto::node_def::NodeDef]) {
        self.graph = Some(graph::Graph::<NEX, TEX>::new(nodes))
    }
}

type Bundle = Box<dyn AbstractBundle>;
type Topology = (Box<[u64]>, Box<[Box<[usize]>]>);

struct Context(Bundle, graph::Target);

#[no_mangle]
unsafe extern fn tge(bundle: *mut Bundle, topo: *mut Topology, pb: *const u8, pb_len: u32, devices: *const u8, devices_len: u32) -> *mut Context {
    let pb = std::slice::from_raw_parts(pb, pb_len as usize);
    let g: proto::graph::GraphDef = parse_from_bytes(pb).unwrap();
    (&mut *bundle).build_graph(&g.node);

    let (links, paths) = *reclaim(topo);

    let devices_str = std::str::from_utf8(std::slice::from_raw_parts(devices, devices_len as usize)).unwrap();
    let devices: Vec<_> = devices_str.split_ascii_whitespace().map(|x| x.to_owned()).collect();

    let target = graph::Target::new(proto::graph::GraphDef::new(), devices.into_boxed_slice(), links, paths);

    leak(Context(*reclaim(bundle), target))
}

#[no_mangle]
unsafe extern fn topology(links_raw: *const u8, links_len: u32, paths_raw: *const u8, paths_len: u32) -> *mut Topology {
    let links_str = std::str::from_utf8(std::slice::from_raw_parts(links_raw, links_len as usize)).unwrap();
    let links = links_str.split_ascii_whitespace().map(|x| x.parse().unwrap()).collect();

    let paths_str = std::str::from_utf8(std::slice::from_raw_parts(paths_raw, paths_len as usize)).unwrap();
    let paths = paths_str.lines().map(|x| x.split_ascii_whitespace().map(|x| x.parse().unwrap()).collect()).collect();

    leak((links, paths))
}

#[no_mangle]
extern fn not_at_all() -> *mut Bundle {
    let bundle = TheBundle::new(strategy::NotAtAll);
    leak(Bundle::from(Box::new(bundle)))
}

#[repr(u8)]
enum CommunicationMethod {
    NONE=0, PS0=1, RING=2, NCCL=3
}

// #[no_mangle]
// extern fn data_parallel(inner: CommunicationMethod, outer: CommunicationMethod) -> *mut Bundle {
//     let bundle = match (inner, outer) {
//         (CommunicationMethod::PS0, CommunicationMethod::NONE) => Bundle::from(Box::new(TheBundle::new(strategy::DataParallelOneForAll))),
//         (CommunicationMethod::RING, CommunicationMethod::NONE) => Bundle::from(Box::new(TheBundle::new(strategy::DataParallelRing))),
//         (CommunicationMethod::NCCL, CommunicationMethod::NONE) => Bundle::from(Box::new(TheBundle::new(strategy::DataParallelNccl))),
//         _ => unimplemented!()
//     };
//
//     Box::leak(Box::new(bundle))
// }

// #[no_mangle]
// unsafe extern fn heft(profiler: extern fn(*const u8, u32) -> u64) -> *mut Bundle {
//     let bundle = TheBundle::new(strategy::NaiveGreedyEarliestFinishTime { profiler });
//     Box::leak(Box::new(Bundle::from(Box::new(bundle))))
// }

// #[no_mangle]
// unsafe extern fn dynamic_programming(profiler: extern fn(*const u8, u32) -> u64) -> *mut Bundle {
//     let strategy = strategy::DynamicProgrammingEarliestFinishTime::new(profiler);
//     let bundle = TheBundle::new(strategy);
//     Box::leak(Box::new(Bundle::from(Box::new(bundle))))
// }

#[no_mangle]
unsafe extern fn custom(strategy_data: *const u8, len: u32) -> *mut Bundle {
    let strategy_data = std::str::from_utf8(std::slice::from_raw_parts(strategy_data, len as usize)).unwrap();
    let strategy_dict = strategy_data.split_ascii_whitespace().collect::<Vec<_>>()
        .chunks(2).map(|x| (x[0].to_string(), x[1].parse().unwrap())).collect();
    let strategy = strategy::Custom { strategy_map: strategy_dict };
    let bundle = TheBundle::new(strategy);
    leak(Bundle::from(Box::new(bundle)))
}

#[no_mangle]
unsafe extern fn compile(ctx: *mut Context, pflag: u8) -> u32 {
    let Context(bundle, target) = &mut *ctx;
    bundle.plan_and_compile(target);
    if pflag & 0x01 != 0 { polishing::remove_colocation_hint(target); }
    if pflag & 0x02 != 0 { polishing::remove_shape_hint(target); }
    if pflag & 0x04 != 0 { polishing::destructify_names(target); }
    if pflag & 0x08 != 0 { polishing::remove_dangling_nodes(&["GradientDescent"], target); }
    target.pb.compute_size()
}

#[no_mangle]
unsafe extern fn evaluate(ctx: *mut Context, profile_data: *const u8, len: u32) -> u64 {
    let profile_str = std::str::from_utf8(std::slice::from_raw_parts(profile_data, len as usize)).unwrap();
    let profile_dict: std::collections::BTreeMap<String, u64> = profile_str.split_ascii_whitespace().collect::<Vec<_>>()
        .chunks(2).map(|x| (x[0].to_string(), x[1].parse().unwrap())).collect();
    let Context(_bundle, target) = &mut *ctx;
    let mut scheduler = scheduler::TensorFlowLikeScheduler::new(target.devices.len(), profile_dict);
    scheduler::Scheduler::evaluate(&mut scheduler, target)
}

#[no_mangle]
unsafe extern fn read_and_destroy(ctx: *mut Context, dest: *mut u8) {
    let Context(_, target) = *reclaim(ctx);
    let mut ptr = std::slice::from_raw_parts_mut(dest, target.pb.get_cached_size() as usize);
    target.pb.write_to_writer(&mut ptr).unwrap();
}

// TODO: better error handling: return NULL or -1 rather than panicing
// TODO: properly exposing the polishing methods: maybe add a bits flag to compile and use keyword arguments on the python side
