import copy
import threading
from unittest.mock import patch
from catalog_stream import merge_skill_catalog
import chimera_web as web


def skill(type_id, slot, form=0, name='same'):
    return {'typeId': type_id, 'slot': slot, 'formIndex': form, 'name': name, 'activeSkill': True}


def test_ascension_variant_is_one_position_and_old_cache_is_cleaned():
    rows = [skill(48801, 1), skill(48802, 2), skill(48804, 3), skill(48803, 3)]
    normalized = web.canonicalize_hero_catalog({4880: {'avatar': 'HeroAvatars/4880', 'skills': rows}})
    assert [s['typeId'] for s in normalized[4880]['skills']] == [48801, 48802, 48804]
    assert len(rows) == 4
    assert web.canonicalize_hero_catalog(normalized) == normalized


def test_real_fourth_button_and_other_form_survive_same_names():
    rows = [skill(10+i, i) for i in range(1, 5)] + [skill(20+i, i, 1) for i in range(1, 5)]
    assert merge_skill_catalog(rows) == rows


def test_partial_live_form_replaces_variant_without_removing_other_form():
    previous = [skill(11, 1), skill(13, 3), skill(23, 3, 1)]
    assert [s['typeId'] for s in merge_skill_catalog([skill(14, 3)], previous)] == [14, 11, 23]
    hidden = skill(13, 3); hidden['hiddenOnHud'] = True
    assert 13 not in [s['typeId'] for s in merge_skill_catalog([hidden], previous)]


def test_live_update_neither_resurrects_old_variant_nor_copies_its_name():
    service = object.__new__(web.ChimeraService)
    service.lock = threading.RLock()
    service.catalog_epoch = 0
    service.catalog_session = 'test'
    service.hero_catalog = {4880: {'typeId': 4880, 'avatar': 'HeroAvatars/4880', 'skills': [skill(48803, 3, name='old name')]}}
    fresh = skill(48804, 3, name='Skill 48804 name')
    state = {'heroes': [{'typeId': 4886, 'avatar': 'HeroAvatars/4880', 'skills': [fresh]}]}
    with patch.object(web, 'save_hero_catalog'):
        service.update_hero_catalog(state)
    rows = service.hero_catalog[4880]['skills']
    assert len(rows) == 1 and rows[0]['typeId'] == 48804
    assert rows[0]['name'] != 'old name'


def test_live_passive_and_hidden_entries_are_removed_even_if_cached_active():
    service = object.__new__(web.ChimeraService)
    service.lock = threading.RLock(); service.catalog_epoch = 0; service.catalog_session = 'test'
    service.hero_catalog = {99: {'typeId': 99, 'skills': [skill(991, 1), skill(992, 2)]}}
    state = {'activeHeroTypeId': 99, 'heroes': [{'typeId': 99, 'skills': [skill(991, 1), skill(992, 2)]}],
             'skills': [{'typeId': 992, 'slot': 2, 'passive': True}]}
    with patch.object(web, 'save_hero_catalog'):
        service.update_hero_catalog(state)
    assert [s['typeId'] for s in service.hero_catalog[99]['skills']] == [991]
