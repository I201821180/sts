'''
Classes for tracking replayed events.

The classes in this module get generated by 2 mechanisms:
* The fuzzer logs events that happens, and it makes one of these and
  conceptually adds it to a list of events to give a global order of events.
* The global ordered event list is parsed from an external input. This could be
  from a single log (for now), or from set of distributed logs (in the future, hopefully).

Author: sw
'''

from sts.entities import Link
from sts.god_scheduler import PendingReceive
from sts.input_traces.fingerprints import *
from invariant_checker import InvariantChecker
import itertools
import abc
import logging
import time
import json
import math
from collections import namedtuple
from sts.syncproto.base import SyncTime
log = logging.getLogger("events")


class EventDag(object):
  # We peek ahead this many seconds after the timestamp of the subseqeunt
  # event
  # TODO(cs): be smarter about this -- peek() too far, and peek()'ing not far
  # enough can both have negative consequences
  _peek_seconds = 10.0
  # If we prune a failure, make sure that the subsequent
  # recovery doesn't occur
  _failure_types = set([SwitchFailure, LinkFailure, ControllerFailure, ControlChannelBlock])
  # NOTE: we treat failure/recovery as an atomic pair, since it doesn't make
  # much sense to prune a recovery event
  _recovery_types = set([SwitchRecovery, LinkRecovery, ControllerRecovery, ControlChannelUnblock])


  '''A collection of Event objects. EventDags are primarily used to present a
  view of the underlying events with some subset of the input events pruned
  '''
  def __init__(self, events, is_view=False):
    '''events is a list of EventWatcher objects. Refer to log_parser.parse to
    see how this is assembled.'''
    # we need to the events to be ordered, so we keep a copy of the list
    self._events_list = events
    self._event_to_index = {
      e : i
      for i, e in enumerate(self._events_list)
    }
    self._label2event = {
      event.label : event
      for event in events
    }
    # Fill in domain knowledge about valid input
    # sequences (e.g. don't prune failure without pruning recovery.)
    # Only do so if this isn't a view of a previously computed DAG
    if not is_view:
      self._mark_invalid_input_sequences()

  @property
  def events(self):
    '''Return the events in the DAG'''
    return self._events_list

  @property
  def event_watchers(self):
    '''Return a generator of the EventWatchers in the DAG'''
    return map(EventWatcher, self._events_list)

  def _remove_event(self, event):
    ''' Recursively remove the event and its dependents '''
    if event.label in self._label2event[event.label]:
      del self._label2event[event.label]
    if event in self._event_to_index:
      list_idx = del self._event_to_index[event]
      self._event_list.pop(list_idx)

    # Note that dependent_labels only contains dependencies between input
    # events. We run peek() to infer dependencies with internal events
    for label in event.dependent_labels:
      if label in self._label2event:
        dependent_event = self._label2event[label]
        self._remove_event(dependent_event)

  def remove_events(self, ignored_portion):
    ''' Mutate the DAG: remove all input events in ignored_inputs,
    as well all of their dependent input events'''
    # Note that we treat failure/recovery as an atomic pair, so we don't prune
    # recovery events on their own
    for event in [ e for e in ignored_portion
                   if (isinstance(e, InputEvent) and
                       type(e) not in self._recovery_types) ]:
      self._remove_event(event)
    # Now run peek() to hide the internal events that will no longer occur
    # Note that causal dependencies change depending on what the prefix is!
    # So we have to run peek() once per prefix
    self.peek()

  def ignore_portion(self, ignored_portion):
    ''' Return a view of the dag with ignored_portion and its dependents
    removed'''
    dag = EventDag(list(self._events_list), is_view=True)
    # TODO(cs): potentially some redundant computation here
    dag.remove_events(ignored_portion)
    return dag

  def split_inputs(self, split_ways):
    ''' Split our events into split_ways separate lists '''
    events = self._events_list
    if len(events) == 0:
      return [[]]
    if split_ways == 1:
      return [events]
    if split_ways < 1 or split_ways > len(events):
      raise ValueError("Invalid split ways %d" % split_ways)

    splits = []
    split_interval = int(math.ceil(len(events) * 1.0 / split_ways))
    start_idx = 0
    split_idx = start_idx + split_interval
    while start_idx < len(events):
      splits.append(events[start_idx:split_idx])
      start_idx = split_idx
      # Account for odd numbered splits -- if we're about to eat up
      # all elements even though we will only have added split_ways-1
      # splits, back up the split interval by 1
      if (split_idx + split_interval >= len(events) and
          len(splits) == split_ways - 2):
        split_interval -= 1
      split_idx += split_interval
    return splits

  def peek(self):
    ''' Assign dependent labels for each internal event '''
    # TODO(cs): store prefix in a trie (class variable)
    # TODO(cs): optimization: write the prefix trie to a file, in case we want to run
    # FindMCS again?
    # TODO(cs): first step should be to look up longest matching prefix from
    # trie
    input_events = [ e for e in self._events_list if isinstance(e, InputEvent) ]
    if len(input_events) == 0:
      return

    # Note that we recompute wait times for every view, since the set of
    # internal events changes
    event2wait_time = {}
    for i in xrange(0, len(input_events)-1):
      current_event = input_events[i]
      next_event = input_events[i+1]
      wait_time = next_event.time.as_float() + self._peek_seconds
      event2wait_time[event] = wait_time
    # For the last event, we wait until the last internal event
    last_wait_time = self._events_list[-1].time.as_float() + self._peek_seconds
    event2wait_time[input_events[-1]] = last_wait_time

  def _mark_invalid_input_sequences(self):
    # Note: we treat each failure/recovery pair atomically, since it doesn't
    # make much sense to prune recovery events. Also note that that we will
    # never see two failures (for a particular node) in a row without an
    # interleaving recovery event
    fingerprint2previousfailure = {}

    # NOTE: mutates self._events
    for event in self._events_list:
      if type(event) in self._failure_types:
        # Insert it into the previous failure hash
        fingerprint2previousfailure[event.fingerprint] = event
      elif type(event) in self_recovery_types:
        # Check if there were any failure predecessors
        if event.fingerprint in fingerprint2previousfailure:
          failure = fingerprint2previousfailure[event.fingerprint]
          failure.dependent_labels.append(event.label)
      elif type(event) == Dataplae

class EventWatcher(object):
  '''EventWatchers watch events. This class can be used to wrap either
  InternalEvents or ExternalEvents to perform pre and post functionality.'''

  def __init__(self, event):
    self.event = event

  def run(self, simulation):
    self._pre()

    while not self.event.proceed(simulation):
      time.sleep(0.05)
      log.debug(".")

    self._post()

  def _pre(self):
    log.debug("Executing %s" % str(self.event))

  def _post(self):
    log.debug("Finished Executing %s" % str(self.event))

class Event(object):
  __metaclass__ = abc.ABCMeta

  # Create unique labels for events
  _label_gen = itertools.count(1)

  def __init__(self, label=None, time=None, dependent_labels=None):
    if label is None:
      label = 'e' + str(Event._label_gen.next())
    if time is None:
      # TODO(cs): compress time for interactive mode?
      time = SyncTime.now()
    self.label = label
    self.time = time
    # Add on dependent labels to appease log_processing.superlog_parser.
    # TODO(cs): Replayer shouldn't depend on superlog_parser
    self.dependent_labels = dependent_labels if dependent_labels else []

  @abc.abstractmethod
  def proceed(self, simulation):
    '''Executes a single `round'. Returns a boolean that is true if the
    Replayer may continue to the next Event, otherwise proceed() again
    later.'''
    pass

  def to_json(self):
    fields = dict(self.__dict__)
    fields['class'] = self.__class__.__name__
    return json.dumps(fields)

  def __str__(self):
    return self.__class__.__name__ + ":" + self.label

# -------------------------------------------------------- #
# Semi-abstract classes for internal and external events   #
# -------------------------------------------------------- #

class InternalEvent(Event):
  '''An InternalEvent is one that happens within the controller(s) under
  simulation. Derivatives of this class verify that the internal event has
  occured in its proceed method before it returns.'''
  def __init__(self, label=None, time=None):
    super(InternalEvent, self).__init__(label=label, time=time)

  def proceed(self, simulation):
     # There might be nothing happening for certain internal events, so default
     # to just doing nothing for proceed (i.e. proceeding automatically).
    pass

class InputEvent(Event):
  '''An event that the simulator injects into the simulation. These events are
  assumed to be causally independent.

  Each InputEvent has a list of dependent InternalEvents that it takes in its
  constructor. This enables the pruning of events.

  This class also conceptually models (because it is equivalent to) 'external
  events', which is a term that may be used elsewhere in documentation or
  code.'''
  def __init__(self, label=None, time=None, dependent_labels=None):
    super(InputEvent, self).__init__(label=label, time=time,
                                     dependent_labels=dependent_labels)

# --------------------------------- #
#  Concrete classes of InputEvents  #
# --------------------------------- #

def assert_fields_exist(json_hash, *args):
  ''' assert that the fields exist in json_hash '''
  fields = args
  for field in fields:
    if field not in json_hash:
      raise ValueError("Field %s not in json_hash %s" % (field, str(json_hash)))

def extract_label_time(json_hash):
  assert_fields_exist(json_hash, 'label', 'time')
  label = json_hash['label']
  time = SyncTime(json_hash['time'][0], json_hash['time'][1])
  return (label, time)

class SwitchFailure(InputEvent):
  def __init__(self, dpid, label=None, time=None):
    super(SwitchFailure, self).__init__(label=label, time=time)
    self.dpid = dpid

  def proceed(self, simulation):
    software_switch = simulation.topology.get_switch(self.dpid)
    simulation.topology.crash_switch(software_switch)
    return True

  @staticmethod
  def from_json(json_hash):
    (label, time) = extract_label_time(json_hash)
    assert_fields_exist(json_hash, 'dpid')
    dpid = int(json_hash['dpid'])
    return SwitchFailure(dpid, label=label, time=time)

  @property
  def fingerprint(self):
    return (self.dpid,)

class SwitchRecovery(InputEvent):
  def __init__(self, dpid, label=None, time=None):
    super(SwitchRecovery, self).__init__(label=label, time=time)
    self.dpid = dpid

  def proceed(self, simulation):
    software_switch = simulation.topology.get_switch(self.dpid)
    simulation.topology.recover_switch(software_switch)
    return True

  @staticmethod
  def from_json(json_hash):
    (label, time) = extract_label_time(json_hash)
    assert_fields_exist(json_hash, 'dpid')
    dpid = int(json_hash['dpid'])
    return SwitchRecovery(dpid, label=label, time=time)

  @property
  def fingerprint(self):
    return (self.dpid,)

def get_link(link_event, simulation):
  start_software_switch = simulation.topology.get_switch(link_event.start_dpid)
  end_software_switch = simulation.topology.get_switch(link_event.end_dpid)
  link = Link(start_software_switch, link_event.start_port_no,
              end_software_switch, link_event.end_port_no)
  return link

class LinkFailure(InputEvent):
  def __init__(self, start_dpid, start_port_no, end_dpid, end_port_no,
               label=None, time=None):
    super(LinkFailure, self).__init__(label=label, time=time)
    self.start_dpid = start_dpid
    self.start_port_no = start_port_no
    self.end_dpid = end_dpid
    self.end_port_no = end_port_no

  def proceed(self, simulation):
    link = get_link(self, simulation)
    simulation.topology.sever_link(link)
    return True

  @staticmethod
  def from_json(json_hash):
    (label, time) = extract_label_time(json_hash)
    assert_fields_exist(json_hash, 'start_dpid', 'start_port_no', 'end_dpid',
                        'end_port_no')
    start_dpid = int(json_hash['start_dpid'])
    start_port_no = int(json_hash['start_port_no'])
    end_dpid = int(json_hash['end_dpid'])
    end_port_no = int(json_hash['end_port_no'])
    return LinkFailure(start_dpid, start_port_no, end_dpid, end_port_no,
                       label=label, time=time)

  @property
  def fingerprint(self):
    return (self.start_dpid, self.start_port_no,
            self.end_dpid, self.end_port_no)

class LinkRecovery(InputEvent):
  def __init__(self, start_dpid, start_port_no, end_dpid, end_port_no,
               label=None, time=None):
    super(LinkRecovery, self).__init__(label=label, time=time)
    self.start_dpid = start_dpid
    self.start_port_no = start_port_no
    self.end_dpid = end_dpid
    self.end_port_no = end_port_no

  def proceed(self, simulation):
    link = get_link(self, simulation)
    simulation.topology.repair_link(link)
    return True

  @staticmethod
  def from_json(json_hash):
    (label, time) = extract_label_time(json_hash)
    assert_fields_exist(json_hash, 'start_dpid', 'start_port_no', 'end_dpid',
                        'end_port_no')
    start_dpid = int(json_hash['start_dpid'])
    start_port_no = int(json_hash['start_port_no'])
    end_dpid = int(json_hash['end_dpid'])
    end_port_no = int(json_hash['end_port_no'])
    return LinkRecovery(start_dpid, start_port_no, end_dpid, end_port_no,
                        label=label, time=time)

  @property
  def fingerprint(self):
    return (self.start_dpid, self.start_port_no,
            self.end_dpid, self.end_port_no)

class ControllerFailure(InputEvent):
  def __init__(self, controller_id, label=None, time=None):
    super(ControllerFailure, self).__init__(label=label, time=time)
    self.controller_id = controller_id

  def proceed(self, simulation):
    controller = simulation.controller_manager.get_controller(self.controller_id)
    simulation.controller_manager.kill_controller(controller)
    return True

  @staticmethod
  def from_json(json_hash):
    (label, time) = extract_label_time(json_hash)
    assert_fields_exist('controller_id')
    controller_id = json_hash['controller_id']
    controller_id = (controller_id[0], int(controller_id[1]))
    return ControllerFailure(controller_id, label=label, time=time)

  @property
  def fingerprint(self):
    return self.controller_id

class ControllerRecovery(InputEvent):
  def __init__(self, controller_id, label=None, time=None):
    super(ControllerRecovery, self).__init__(label=label, time=time)
    self.controller_id = controller_id

  def proceed(self, simulation):
    controller = simulation.controller_manager.get_controller(self.controller_id)
    simulation.controller_manager.reboot_controller(controller)
    return True

  @staticmethod
  def from_json(json_hash):
    (label, time) = extract_label_time(json_hash)
    assert_fields_exist('controller_id')
    controller_id = json_hash['controller_id']
    controller_id = (controller_id[0], int(controller_id[1]))
    return ControllerFailure(controller_id, label=label, time=time)

  @property
  def fingerprint(self):
    return self.controller_id

class HostMigration(InputEvent):
  def __init__(self, old_ingress_dpid, old_ingress_port_no,
               new_ingress_dpid, new_ingress_port_no, label=None, time=None):
    super(HostMigration, self).__init__(label=label, time=time)
    self.old_ingress_dpid = old_ingress_dpid
    self.old_ingress_port_no = old_ingress_port_no
    self.new_ingress_dpid = new_ingress_dpid
    self.new_ingress_port_no =  new_ingress_port_no

  def proceed(self, simulation):
    simulation.topology.migrate_host(self.old_ingress_dpid,
                                     self.old_ingress_port_no,
                                     self.new_ingress_dpid,
                                     self.new_ingress_port_no)
    return True

  @staticmethod
  def from_json(json_hash):
    (label, time) = extract_label_time(json_hash)
    assert_fields_exist(json_hash, 'old_ingress_dpid', 'old_ingress_port_no',
                        'new_ingress_dpid', 'new_ingress_port_no')
    old_ingress_dpid = int(json_hash['old_ingress_dpid'])
    old_ingress_port_no = int(json_hash['old_ingress_port_no'])
    new_ingress_dpid = int(json_hash['new_ingress_dpid'])
    new_ingress_port_no = int(json_hash['new_ingress_port_no'])
    return HostMigration(old_ingress_dpid, old_ingress_port_no,
                         new_ingress_dpid, new_ingress_port_no,
                         label=label, time=time)

class PolicyChange(InputEvent):
  def __init__(self, request_type, label=None, time=None):
    super(PolicyChange, self).__init__(label=label, time=time)
    self.request_type = request_type

  def proceed(self, simulation):
    # TODO(cs): implement me, and add PolicyChanges to Fuzzer
    pass

  @staticmethod
  def from_json(json_hash):
    (label, time) = extract_label_time(json_hash)
    assert_fields_exist(json_hash, 'request_type')
    request_type = json_hash['request_type']
    return PolicyChange(request_type, label=label, time=time)

class TrafficInjection(InputEvent):
  def __init__(self, label=None, time=None):
    super(TrafficInjection, self).__init__(label=label, time=time)

  def proceed(self, simulation):
    if simulation.dataplane_trace is None:
      raise RuntimeError("No dataplane trace specified!")
    simulation.dataplane_trace.inject_trace_event()
    return True

  @staticmethod
  def from_json(json_hash):
    (label, time) = extract_label_time(json_hash)
    return TrafficInjection(label, time)

class WaitTime(InputEvent):
  def __init__(self, wait_time, label=None, time=None):
    super(WaitTime, self).__init__(label=label, time=time)
    self.wait_time = wait_time

  def proceed(self, simulation):
    log.info("WaitTime: pausing simulation for %f seconds" % (self.wait_time))
    time.sleep(self.wait_time)
    return True

  @staticmethod
  def from_json(json_hash):
    (label, time) = extract_label_time(json_hash)
    assert_fields_exist(json_hash, 'wait_time')
    wait_time = json_hash['wait_time']
    return WaitTime(wait_time, label=label, time=time)

class CheckInvariants(InputEvent):
  def __init__(self, fail_on_error=False, label=None, time=None,
               invariant_check=InvariantChecker.check_correspondence):
    super(CheckInvariants, self).__init__(label=label, time=time)
    self.fail_on_error = fail_on_error
    self.invariant_check = invariant_check

  def proceed(self, simulation):
    log.info("CheckInvariants: checking correspondence")
    violations = self.invariant_check(simulation)

    if violations != []:
      log.warning("Correctness violations!: %s" % str(violations))
      if self.fail_on_error:
        exit(5)
    else:
      log.info("No correctness violations!")
    return True

  @staticmethod
  def from_json(json_hash):
    (label, time) = extract_label_time(json_hash)
    fail_on_error = False
    if 'fail_on_error' in json_hash:
      fail_on_error = json_hash['fail_on_error']
    invariant_check = InvariantChecker.check_correspondence
    if 'invariant_check' in json_hash:
      method_name = json_hash['invariant_check']
      if method_name not in InvariantChecker.__dict__:
        raise ValueError("No such method %s in InvariantChecker" %
                         (method_name,))
      invariant_check = InvariantChecker.__dict__[method_name]
    return CheckInvariants(label=label, time=time,
                           fail_on_error=fail_on_error,
                           invariant_check=invariant_check)

class ControlChannelBlock(InternalEvent):
  def __init__(self, dpid, controller_id, label=None, time=None):
    super(ControlChannelBlock, self).__init__(label=label, time=time)
    self.dpid = dpid
    self.controller_id = controller_id

  def proceed(self, simulation):
    switch = simulation.topology.get_switch(self.dpid)
    connection = switch.get_connection(self.controller_id)
    if connection.currently_blocked:
      raise RuntimeError("Expected channel %s to not be blocked" % str(connection))
    connection.block()
    return True

  @property
  def fingerprint(self):
    return (self.dpid, self.controller_id)

  @staticmethod
  def from_json(json_hash):
    (label, time) = extract_label_time(json_hash)
    assert_fields_exist(json_hash, 'dpid', 'controller_id')
    dpid = json_hash['dpid']
    controller_id = tuple(json_hash['controller_id'])
    return ControlChannelBlock(dpid, controller_id, label=label, time=time)

class ControlChannelUnblock(InternalEvent):
  def __init__(self, dpid, controller_id, label=None, time=None):
    super(ControlChannelUnblock, self).__init__(label=label, time=time)
    self.dpid = dpid
    self.controller_id = controller_id

  def proceed(self, simulation):
    switch = simulation.topology.get_switch(self.dpid)
    connection = switch.get_connection(self.controller_id)
    if not connection.currently_blocked:
      raise RuntimeError("Expected channel %s to be blocked" % str(connection))
    connection.unblock()
    return True

  @property
  def fingerprint(self):
    return (self.dpid, self.controller_id)

  @staticmethod
  def from_json(json_hash):
    (label, time) = extract_label_time(json_hash)
    assert_fields_exist(json_hash, 'dpid', 'controller_id')
    dpid = json_hash['dpid']
    controller_id = tuple(json_hash['controller_id'])
    return ControlChannelUnblock(dpid, controller_id, label=label, time=time)

all_input_events = [SwitchFailure, SwitchRecovery, LinkFailure, LinkRecovery,
                    ControllerFailure, ControllerRecovery, HostMigration,
                    PolicyChange, TrafficInjection, WaitTime, CheckInvariants,
                    ControlChannelBlock, ControlChannelUnblock]

# ----------------------------------- #
#  Concrete classes of InternalEvents #
# ----------------------------------- #

# Simulator's internal events:

# TODO(cs): Technically these aren't internal events! They're input events!
# And they have really complicated dependencies with other input events!
# For now, turn them off completely.
class DataplaneDrop(InternalEvent):
  def __init__(self, fingerprint, label=None, time=None):
    super(DataplaneDrop, self).__init__(label=label, time=time)
    self.fingerprint = fingerprint

  def proceed(self, simulation):
    dp_event = simulation.patch_panel.get_buffered_dp_event(self.fingerprint)
    if dp_event is not None:
      simulation.patch_panel.drop_dp_event(dp_event)
      return True
    return False

  @staticmethod
  def from_json(json_hash):
    (label, time) = extract_label_time(json_hash)
    assert_fields_exist(json_hash, 'fingerprint')
    fingerprint = DPFingerprint(json_hash['fingerprint'])
    return DataplaneDrop(fingerprint, label=label, time=time)

class DataplanePermit(InternalEvent):
  def __init__(self, fingerprint, label=None, time=None):
    super(DataplanePermit, self).__init__(label=label, time=time)
    self.fingerprint = fingerprint

  def proceed(self, simulation):
    dp_event = simulation.patch_panel.get_buffered_dp_event(self.fingerprint)
    if dp_event is not None:
      simulation.patch_panel.permit_dp_event(dp_event)
      return True
    return False

  @staticmethod
  def from_json(json_hash):
    (label, time) = extract_label_time(json_hash)
    assert_fields_exist(json_hash, 'fingerprint')
    fingerprint = DPFingerprint(json_hash['fingerprint'])
    return DataplanePermit(fingerprint, label=label, time=time)

class ControlMessageReceive(InternalEvent):
  '''
  Logged whenever the GodScheduler decides to allow a switch to see an
  openflow packet.
  '''
  def __init__(self, dpid, controller_id, fingerprint, label=None, time=None):
    super(ControlMessageReceive, self).__init__(label=label, time=time)
    self.dpid = dpid
    self.controller_id = controller_id
    self.fingerprint = fingerprint

  def proceed(self, simulation):
   pending_receive = PendingReceive(self.dpid, self.controller_id,
                                    self.fingerprint)
   message_waiting = simulation.god_scheduler.message_waiting(pending_receive)
   if message_waiting:
     simulation.god_scheduler.schedule(pending_receive)
     return True
   return False

  @staticmethod
  def from_json(json_hash):
    (label, time) = extract_label_time(json_hash)
    assert_fields_exist(json_hash, 'dpid', 'controller_id', 'fingerprint')
    dpid = json_hash['dpid']
    controller_id = tuple(json_hash['controller_id'])
    fingerprint = OFFingerprint(json_hash['fingerprint'])
    return ControlMessageReceive(dpid, controller_id, fingerprint, label=label, time=time)

# Controllers' internal events:

# TODO(cs): move me?
PendingStateChange = namedtuple('PendingStateChange',
                                ['controller_id', 'time', 'fingerprint',
                                 'name', 'value'])

class ControllerStateChange(InternalEvent):
  '''
  Logged for any relevent kind of state change in the controller (e.g.
  mastership change)
  '''
  def __init__(self, controller_id, fingerprint, name, value, label=None, time=None):
    super(ControllerStateChange, self).__init__(label=label, time=time)
    self.controller_id = controller_id
    self.fingerprint = fingerprint
    self.name = name
    self.value = value

  def proceed(self, simulation):
    pending_state_change = PendingStateChange(self.controller_id, self.time,
                                              self.fingerprint, self.name, self.value)
    observed_yet = simulation.controller_sync_callback\
                             .state_change_pending(pending_state_change)
    if observed_yet:
      simulation.controller_sync_callback\
                .gc_pending_state_change(pending_state_change)
      return True
    return False

  @staticmethod
  def from_json(json_hash):
    (label, time) = extract_label_time(json_hash)
    assert_fields_exist(json_hash, 'dpid', 'controller_id', 'fingerprint',
                        'name', 'value')
    controller_id = tuple(json_hash['controller_id'])
    fingerprint = json_hash['fingerprint']
    name = json_hash['name']
    value = json_hash['value']
    return ControllerStateChange(controller_id, fingerprint, name, value, label=label, time=time)

class DeterministicValue(InternalEvent):
  '''
  Logged whenever the controller asks for a deterministic value (e.g.
  gettimeofday()
  '''
  pass

all_internal_events = [DataplaneDrop, DataplanePermit,
                       ControlMessageReceive,
                       ControllerStateChange, DeterministicValue]
