"""Persist bounded native lifecycle history without changing IPC decisions."""
from __future__ import annotations
from decision_observability import decision_context, emit_telemetry, pick

EVENT_KEYS = ('sequence', 'observedAtTick', 'reason', 'screen', 'takeoverState',
              'battleGeneration', 'battleVerified', 'battleFinished', 'finishedReadStatus', 'dialogOpen', 'openedAtTick',
              'uiEpoch', 'resultGenerationRetired')


class ObservedIpc:
    def __init__(self, ipc, emit=emit_telemetry):
        self.ipc = ipc
        self.emit = emit
        self.cursor = 0
        self.instance = None
        self.terminal_key = None
        self.last_sequence = None
        self.missing_terminal_key = None

    def __getattr__(self, name):
        return getattr(self.ipc, name)

    def lifecycle(self):
        lifecycle = self.ipc.lifecycle()
        if not isinstance(lifecycle, dict):
            return lifecycle
        instance = lifecycle.get('agentInstanceId')
        if instance != self.instance:
            self.instance = instance
            self.cursor = 0
            self.last_sequence = None
            self.terminal_key = None
            self.missing_terminal_key = None
        sequence = lifecycle.get('sequence')
        if sequence == self.last_sequence:
            return lifecycle
        self.last_sequence = sequence
        events = [row for row in (lifecycle.get('events') or [])
                  if isinstance(row, dict) and type(row.get('sequence')) is int and row['sequence'] > self.cursor]
        events.sort(key=lambda row: row['sequence'])
        for event in events:
            if event['sequence'] > self.cursor + 1:
                self.emit(lifecycle={'event': 'native_history_gap', 'agentInstanceId': instance,
                    'fromSequence': self.cursor + 1, 'toSequence': event['sequence'] - 1,
                    'beforeFirstObservation': self.cursor == 0})
            self.emit(lifecycle={'event': 'native_transition', 'agentInstanceId': instance,
                                 'observation': pick(event, EVENT_KEYS)})
            self.cursor = event['sequence']
        self.emit(lifecycle={'event': 'lifecycle_observed', 'agentInstanceId': instance,
            'observation': pick(lifecycle, ('sequence', 'observedAtTick', 'reason', 'screen',
                'takeoverState', 'battleGeneration', 'resultConfirmationAvailable')),
            'result': pick(lifecycle.get('result'), ('confirmed', 'battleFinished', 'battleGeneration',
                'openedAtTick', 'source', 'bossMode', 'finishedReadStatus')),
            'selection': pick(lifecycle.get('selection'), ('valid', 'filled', 'bossMode', 'areaTypeId',
                'stageId', 'heroTypeIds', 'autoBattle', 'quickBattle'))})
        # The ledger retains a terminal snapshot even after live decision data
        # is cleared by OnDisabled. This read never invokes a game action.
        try:
            ledger = self.ipc.battle_ledger() or {}
            terminal = ledger.get('terminalState')
            if isinstance(terminal, dict) and terminal.get('battleGeneration') is not None:
                key = (terminal.get('battleGeneration'), terminal.get('observedAtTick'))
                if key != self.terminal_key:
                    self.terminal_key = key
                    self.emit(lifecycle={'event': 'terminal_battle_observed', 'agentInstanceId': instance,
                        'availability': pick(terminal, ('source', 'actorsAvailable', 'bossesAvailable', 'entitiesTruncated')),
                        'context': decision_context(terminal)})
            elif lifecycle.get('screen') == 'result' and self.missing_terminal_key != lifecycle.get('battleGeneration'):
                self.missing_terminal_key = lifecycle.get('battleGeneration')
                self.emit(lifecycle={'event': 'terminal_observation_unavailable', 'reason': 'not_published',
                    'battleGeneration': lifecycle.get('battleGeneration')})
        except (ValueError, OSError) as error:
            self.emit(lifecycle={'event': 'terminal_observation_unavailable', 'errorType': type(error).__name__})
        return lifecycle
