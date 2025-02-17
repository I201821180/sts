"""
This module mocks out openflow switches, links, and hosts. These are all the
'entities' that exist within our simulated environment.
"""

from pox.openflow.software_switch import DpPacketOut, OFConnection
from pox.openflow.nx_software_switch import NXSoftwareSwitch
from pox.openflow.libopenflow_01 import *
from pox.lib.revent import EventMixin
from sts.util.procutils import popen_filtered, kill_procs
from sts.util.console import msg

import logging
import os
import re
import pickle

class DeferredOFConnection(OFConnection):
  def __init__(self, io_worker, dpid, god_scheduler):
    super(DeferredOFConnection, self).__init__(io_worker)
    self.dpid = dpid
    self.god_scheduler = god_scheduler
    # Don't feed messages to the switch directly
    self.on_message_received = self.insert_into_god_scheduler
    self.true_on_message_handler = None

  def insert_into_god_scheduler(self, _, ofp_msg):
    ''' Rather than pass directly on to the switch, feed into the god scheduler'''
    self.god_scheduler.insert_pending_message(self.dpid, self.get_controller_id(), ofp_msg, self)

  def set_message_handler(self, handler):
    ''' Take the switch's handler, and store it for later use '''
    self.true_on_message_handler = handler

  def allow_message_receipt(self, ofp_message):
    ''' Allow the message to actually go through to the switch '''
    self.true_on_message_handler(self, ofp_message)

class FuzzSoftwareSwitch (NXSoftwareSwitch):
  """
  A mock switch implementation for testing purposes. Can simulate dropping dead.
  """
  class ConnectionFSM(object):
    # TODO(cs): this FSM is specific to pox!
    START = 1
    OFP_HELLO = 2
    FEATURES_REQUEST = 3
    SET_CONFIG = 4
    BOOTED = 5

  _eventMixin_events = set([DpPacketOut])

  def __init__ (self, dpid, name=None, ports=4, miss_send_len=128,
                n_buffers=100, n_tables=1, capabilities=None,
                can_connect_to_endhosts=True):
    NXSoftwareSwitch.__init__(self, dpid, name, ports, miss_send_len,
                              n_buffers, n_tables, capabilities)

    # Whether this is a core or edge switch
    self.can_connect_to_endhosts = can_connect_to_endhosts

    self.failed = False
    self.log = logging.getLogger("FuzzSoftwareSwitch(%d)" % dpid)

    def error_handler(e):
      self.log.exception(e)
      raise e

    # controller (ip, port) -> connection
    self.uuid2connection = {}
    # We keep a finite state machine for each connection to track whether the
    # initialization handshake has completed
    self.connection2fsm = {}
    self.error_handler = error_handler
    self.controller_info = []

  @property
  def booted(self):
    return (self.connection2fsm != {} and
            set(self.connection2fsm.values()) == set([self.ConnectionFSM.BOOTED]))

  def add_controller_info(self, info):
    self.controller_info.append(info)

  def _handle_ConnectionUp(self, event):
    self._setConnection(event.connection, event.ofp)

  def connect(self, create_connection, down_controller_ids=None):
    ''' - create_connection is a factory method for creating Connection objects
          which are connected to controllers. Takes a ControllerConfig object
          and a reference to a switch (self) as a paramter
    '''
    # Keep around the connection factory for fail/recovery later
    if down_controller_ids is None:
      down_controller_ids = set()
    self.create_connection = create_connection
    connected_to_at_least_one = False
    for info in self.controller_info:
      # Don't connect to down controllers
      if info.uuid not in down_controller_ids:
        conn = create_connection(info, self)
        self.set_connection(conn)
        # cause errors to be raised
        conn.error_handler = self.error_handler
        # controller (ip, port) -> connection
        self.uuid2connection[conn.io_worker.socket.getpeername()] = conn
        connected_to_at_least_one = True

    return connected_to_at_least_one

  def get_connection(self, uuid):
    if uuid not in self.uuid2connection:
      raise ValueError("No such connection %s" % str(uuid))
    return self.uuid2connection[uuid]

  def fail(self):
    # TODO(cs): depending on the type of failure, a real switch failure
    # might not lead to an immediate disconnect
    if self.failed:
      self.log.warn("Switch already failed")
      return
    self.failed = True

    for connection in self.connections:
      connection.close()
    self.connections = []

  def recover(self, down_controller_ids=None):
    if not self.failed:
      self.log.warn("Switch already up")
      return
    connected_to_at_least_one = self.connect(self.create_connection,
                                             down_controller_ids=down_controller_ids)
    if connected_to_at_least_one:
      self.failed = False
    return connected_to_at_least_one

  def serialize(self):
    # Skip over non-serializable data, e.g. sockets
    # TODO(cs): is self.log going to be a problem?
    serializable = FuzzSoftwareSwitch(self.dpid, self.parent_controller_name)
    # Can't serialize files
    serializable.log = None
    # TODO(cs): need a cleaner way to add in the NOM port representation
    if self.software_switch:
      serializable.ofp_phy_ports = self.software_switch.ports.values()
    return pickle.dumps(serializable, protocol=0)

class Link (object):
  """
  A network link between two switches

  Temporary stand in for Murphy's graph-library for the NOM.

  Note: Directed!
  """
  def __init__(self, start_software_switch, start_port, end_software_switch, end_port):
    if type(start_port) == int:
      assert(start_port in start_software_switch.ports)
      start_port = start_software_switch.ports[start_port]
    if type(end_port) == int:
      assert(end_port in start_software_switch.ports)
      end_port = end_software_switch.ports[end_port]
    assert_type("start_port", start_port, ofp_phy_port, none_ok=False)
    assert_type("end_port", end_port, ofp_phy_port, none_ok=False)
    self.start_software_switch = start_software_switch
    self.start_port = start_port
    self.end_software_switch = end_software_switch
    self.end_port = end_port

  def __eq__(self, other):
    if not type(other) == Link:
      return False
    return (self.start_software_switch == other.start_software_switch and
            self.start_port == other.start_port and
            self.end_software_switch == other.end_software_switch and
            self.end_port == other.end_port)

  def __hash__(self):
    return (self.start_software_switch.__hash__() +  self.start_port.__hash__() +
           self.end_software_switch.__hash__() +  self.end_port.__hash__())

  def __repr__(self):
    return "(%d:%d) -> (%d:%d)" % (self.start_software_switch.dpid, self.start_port.port_no,
                                   self.end_software_switch.dpid, self.end_port.port_no)

  def reversed_link(self):
    '''Create a Link that is in the opposite direction of this Link.'''
    return Link(self.end_software_switch, self.end_port,
                self.start_software_switch, self.start_port)

class AccessLink (object):
  '''
  Represents a bidirectional edge: host <-> ingress switch
  '''
  def __init__(self, host, interface, switch, switch_port):
    assert_type("interface", interface, HostInterface, none_ok=False)
    assert_type("switch_port", switch_port, ofp_phy_port, none_ok=False)
    self.host = host
    self.interface = interface
    self.switch = switch
    self.switch_port = switch_port

class HostInterface (object):
  ''' Represents a host's interface (e.g. eth0) '''
  def __init__(self, hw_addr, ip_or_ips=[], name=""):
    self.hw_addr = hw_addr
    if type(ip_or_ips) != list:
      ip_or_ips = [ip_or_ips]
    self.ips = ip_or_ips
    self.name = name

  def __eq__(self, other):
    if type(other) != HostInterface:
      return False
    if self.hw_addr.toInt() != other.hw_addr.toInt():
      return False
    other_ip_ints = map(lambda ip: ip.toUnsignedN(), other.ips)
    for ip in self.ips:
      if ip.toUnsignedN() not in other_ip_ints:
        return False
    if len(other.ips) != len(self.ips):
      return False
    if self.name != other.name:
      return False
    return True

  def __hash__(self):
    hash_code = self.hw_addr.toInt().__hash__()
    for ip in self.ips:
      hash_code += ip.toUnsignedN().__hash__()
    hash_code += self.name.__hash__()
    return hash_code

  def __str__(self, *args, **kwargs):
    return "HostInterface:" + self.name + ":" + str(self.hw_addr) + ":" + str(self.ips)

  def __repr__(self, *args, **kwargs):
    return self.__str__()

#                Host
#          /      |       \
#  interface   interface  interface
#    |            |           |
# access_link acccess_link access_link
#    |            |           |
# switch_port  switch_port  switch_port

class Host (EventMixin):
  '''
  A very simple Host entity.

  For more sophisticated hosts, we should spawn a separate VM!

  If multiple host VMs are too heavy-weight for a single machine, run the
  hosts on their own machines!
  '''
  _eventMixin_events = set([DpPacketOut])

  def __init__(self, interfaces, name=""):
    '''
    - interfaces A list of HostInterfaces
    '''
    self.interfaces = interfaces
    self.log = logging.getLogger(name)
    self.name = name

  def send(self, interface, packet):
    ''' Send a packet out a given interface '''
    self.log.info("sending packet on interface %s: %s" % (interface.name, str(packet)))
    self.raiseEvent(DpPacketOut(self, packet, interface))

  def receive(self, interface, packet):
    '''
    Process an incoming packet from a switch

    Called by PatchPanel
    '''
    self.log.info("received packet on interface %s: %s" % (interface.name, str(packet)))

  def __str__(self):
    return self.name

class Controller(object):
  '''Encapsulates the state of a running controller.'''

  _active_processes = set() # set of processes that are currently running. These are all killed upon signal reception

  @staticmethod
  def kill_active_procs():
    '''Kill the active processes. Used by the simulator module to shut down the
    controllers because python can only have a single method to handle SIG* stuff.'''
    kill_procs(Controller._active_processes)

  def _register_proc(self, proc):
    '''Register a Popen instance that a controller is running in for the cleanup
    that happens when the simulator receives a signal. This method is idempotent.'''
    self._active_processes.add(proc)

  def _unregister_proc(self, proc):
    '''Remove a process from the set of this to be killed when a signal is
    received. This is for use when the Controller process is stopped. This
    method is idempotent.'''
    self._active_processes.discard(proc)

  def __del__(self):
    if hasattr(self, 'process') and self.process != None: # if it fails in __init__, process may not have been assigned
      if self.process.poll():
        self._unregister_proc(self.process) # don't let this happen for shutdown
      else:
        self.kill() # make sure it is killed if this was started errantly

  def __init__(self, controller_config, sync_connection_manager,
               snapshot_service):
    '''idx is the unique index for the controller used mostly for logging purposes.'''
    self.config = controller_config
    self.alive = False
    self.process = None
    self.sync_connection_manager = sync_connection_manager
    self.sync_connection = None
    self.snapshot_service = snapshot_service
    self.log = logging.getLogger("Controller")

  @property
  def pid(self):
    '''Return the PID of the Popen instance the controller was started with.'''
    return self.process.pid if self.process else None

  @property
  def uuid(self):
    '''Return the uuid of this controller. See ControllerConfig for more details.'''
    return self.config.uuid

  def kill(self):
    '''Kill the process the controller is running in.'''
    msg.event("Killing controller %s" % (str(self.uuid)))
    if self.sync_connection:
      self.sync_connection.close()

    kill_procs([self.process])
    self._unregister_proc(self.process)
    self.alive = False
    self.process = None

  def start(self):
    '''Start a new controller process based on the config's cmdline
    attribute. Registers the Popen member variable for deletion upon a SIG*
    received in the simulator process.'''
    msg.event("Starting controller %s" % (str(self.uuid)))
    env = None

    if self.config.sync:
      # if a sync connection has been configured in the controller conf
      # launch the controller with environment variable 'sts_sync' set
      # to the appropriate listening port. This is quite a hack.
      env = os.environ.copy()
      port_match = re.search(r':(\d+)$', self.config.sync)
      if port_match is None:
        raise ValueError("sync: cannot find port in %s" % self.config.sync)
      port = port_match.group(1)
      env['sts_sync'] = "ptcp:0.0.0.0:%d" % (int(port),)

      if self.config.name == "pox":
        src_dir = os.path.join(os.path.dirname(__file__), "..")
        pox_ext_dir = os.path.join(self.config.cwd, "ext")
        if os.path.exists(pox_ext_dir):
          for f in ("sts/util/io_master.py", "sts/syncproto/base.py",
                    "sts/syncproto/pox_syncer.py", "sts/__init__.py"):
            src_path = os.path.join(src_dir, f)
            if not os.path.exists(src_path):
              raise ValueError("Integrity violation: sts sync source path %s (abs: %s) does not exist" %
                  (src_path, os.path.abspath(src_path)))
            dst_path = os.path.join(pox_ext_dir, f)
            dst_dir = os.path.dirname(dst_path)
            init_py = os.path.join(dst_dir, "__init__.py")
            if not os.path.exists(dst_dir):
              os.makedirs(dst_dir)

            if not os.path.exists(init_py):
              open(init_py, "a").close()

            if os.path.islink(dst_path):
              # remove symlink and recreate
              os.remove(dst_path)

            if not os.path.exists(dst_path):
              rel_link = os.path.abspath(src_path)
              self.log.debug("creating symlink %s -> %s", rel_link, dst_path)
              os.symlink(rel_link, dst_path)
        else:
          self.log.warn("Could not find pox ext dir in %s. Cannot check/link in sync module" % pox_ext_dir)

    self.process = popen_filtered("c%s" % str(self.uuid), self.config.expanded_cmdline, self.config.cwd, env=env)
    self._register_proc(self.process)

    if self.config.sync:
      self.sync_connection = self.sync_connection_manager.connect(self, self.config.sync)

    self.alive = True

  def restart(self):
    self.kill()
    self.start()

  def check_process_status(self):
    if not self.alive:
      return (True, "OK")
    else:
      if not self.process:
        return (False, "Controller %s: Alive, but no controller process found" % self.config.name)
      rc = self.process.poll()
      if rc is not None:
        return (False, "Controller %s: Alive, but controller process terminated with return code %d" % ( self.config.name, rc))
      return (True, "OK")

  def send_policy_request(self, controller, api_call):
    pass

