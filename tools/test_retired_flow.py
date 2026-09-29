"""Retired flowchart strategies (no live IPC).

Flow execution was removed in 1.0.6. Strategies saved with a flow keep their
data on export, are refused on import and are never run, not even through the
list rules they carry.
"""
import copy
import json
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

import chimera_controller as c
from test_trial_skill_reservation import fixture

# A flow as older versions saved it; only its presence matters now.
SAVED_FLOW = {"nodes": [{"id": "start", "type": "rule", "rule": 0}], "edges": [], "revision": "saved"}


def config_for(rules):
    return {"rules": rules, "executionMode": "flow", "strategyFlow": copy.deepcopy(SAVED_FLOW)}


def test_retired_flow_export_preserves_data_but_import_rejects_execution():
    import chimera_web as web
    from strategy_storage import write_strategy_store
    from boss_modes import default_store, update_mode_strategy
    _, _, generic, fallback = fixture()
    config = {**web.strategy_template('chimera'), **config_for([generic, fallback])}
    with tempfile.TemporaryDirectory() as directory:
        store_path = Path(directory) / 'strategies.json'
        store = update_mode_strategy(default_store(), 'chimera', config)
        write_strategy_store(store_path, store)
        service = object.__new__(web.ChimeraService)
        service.lock = threading.RLock()
        with patch.object(service, 'store_path', return_value=store_path), patch.object(service, 'strategy_store', side_effect=lambda: json.loads(store_path.read_text(encoding='utf-8'))):
            document = service.export_strategy_profile('default', 'chimera')
            assert document['version'] == 2  # Stable 1.0.5 rejects rather than silently running the list backup.
            try:
                service.import_strategy_profile(document, 'chimera')
            except ValueError as error:
                assert '1.0.6' in str(error)
            else:
                raise AssertionError('Retired flow must not be imported as executable')
            assert service.strategy('chimera')['strategyFlow'] == config['strategyFlow']


def test_live_evaluator_rejects_retired_flow_without_falling_back_to_list():
    state, owner, generic, fallback = fixture()
    before = copy.deepcopy(state)
    try:
        c.evaluate(config_for([generic, fallback]), state)
    except ValueError as error:
        assert '1.0.6' in str(error)
    else:
        raise AssertionError('Hidden flow execution or silent list fallback')
    assert state == before
    archived = {'rules': [generic, fallback], 'executionMode': 'list', 'strategyFlow': {'archived': True}}
    assert c.evaluate(archived, state) is not None
