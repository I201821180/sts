
from experiment_config_lib import ControllerConfig
from sts.topology import MeshTopology
from sts.control_flow import Replayer
from sts.simulation_state import SimulationConfig

controllers = [ControllerConfig(cmdline='./pox.py --no-cli --verbose openflow.of_01 --address=__address__ --port=__port__ sts.syncproto.pox_syncer samples.topo forwarding.l2_learning', address='127.0.0.1', port=8888, cwd='pox', sync='tcp:localhost:18888')]
topology_class = MeshTopology
dataplane_trace = "dataplane_traces/ping_pong_same_subnet.trace"
topology_params = "num_switches=2"
simulation_config = SimulationConfig(controller_configs=controllers,
                                     topology_class=topology_class,
                                     topology_params=topology_params,
                                     dataplane_trace=dataplane_trace)

control_flow = Replayer(simulation_config, "input_traces/violation_integration_test.trace")
