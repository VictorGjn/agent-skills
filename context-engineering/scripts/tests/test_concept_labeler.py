"""Tests for concept_labeler — LLM-driven cluster naming."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_build_prompt_shape():
    """Prompt must include cluster label, top symbols, first sentences."""
    from concept_labeler import build_prompt

    cluster = {'nodes': ['src/nav/side.ts', 'src/nav/top.ts']}
    file_data = {
        'src/nav/side.ts': {'symbols': ['SideNavbar', 'renderNav'],
                             'first_sentence': 'Side navigation drawer for vessel list.'},
        'src/nav/top.ts': {'symbols': ['TopNavbar', 'renderMenu'],
                            'first_sentence': 'Top menu bar with profile dropdown.'},
    }
    prompt = build_prompt(cluster, file_data, current_label='SideNavbar, TopNavbar')

    assert 'SideNavbar' in prompt
    assert 'vessel list' in prompt.lower()
    assert 'concept' in prompt.lower()
    assert 'sub-features' in prompt.lower() or 'sub_features' in prompt.lower()


def test_cache_hit_skips_llm(tmp_path):
    """A second call with identical inputs must not hit the LLM."""
    from concept_labeler import label_cluster

    calls = {'n': 0}
    def fake_llm(prompt: str) -> str:
        calls['n'] += 1
        return ('{"concept": "Navigation", "description": "Top and side menus",'
                ' "sub_features": ["Vessel List", "Profile"]}')

    cluster = {'nodes': ['src/nav/side.ts']}
    file_data = {'src/nav/side.ts': {'symbols': ['SideNavbar'],
                                      'first_sentence': 'Side nav.'}}

    r1 = label_cluster(cluster, file_data, llm=fake_llm, cache_dir=tmp_path)
    r2 = label_cluster(cluster, file_data, llm=fake_llm, cache_dir=tmp_path)

    assert r1 == r2
    assert r1['concept'] == 'Navigation'
    assert calls['n'] == 1  # second call served from cache


def test_malformed_json_falls_back():
    """If LLM returns unparseable JSON, return a safe fallback label."""
    from concept_labeler import label_cluster

    cluster = {'nodes': ['x.ts']}
    file_data = {'x.ts': {'symbols': ['X'], 'first_sentence': ''}}

    def bad_llm(prompt: str) -> str:
        return "this is not json"

    result = label_cluster(cluster, file_data, llm=bad_llm,
                            cache_dir=None, current_label='X')
    assert result['concept'] == 'X'  # falls back to current_label
    assert result['sub_features'] == []


def test_llm_exception_falls_back():
    """If the LLM callable raises, return the fallback label."""
    from concept_labeler import label_cluster

    cluster = {'nodes': ['x.ts']}
    file_data = {'x.ts': {'symbols': ['X'], 'first_sentence': ''}}

    def bad_llm(prompt: str) -> str:
        raise RuntimeError('rate limited')

    result = label_cluster(cluster, file_data, llm=bad_llm,
                            cache_dir=None, current_label='Foo Bar')
    assert result['concept'] == 'Foo Bar'
    assert result['description'] == ''
    assert result['sub_features'] == []


def test_empty_cluster_skips_llm():
    """An empty cluster must not call the LLM."""
    from concept_labeler import label_cluster

    calls = {'n': 0}
    def fake_llm(prompt: str) -> str:
        calls['n'] += 1
        return '{}'

    result = label_cluster({'nodes': []}, {}, llm=fake_llm,
                            cache_dir=None, current_label='Empty')
    assert result['concept'] == 'Empty'
    assert calls['n'] == 0


def test_label_all_clusters_concurrent(tmp_path):
    """label_all_clusters fans out and merges results keyed by cluster id."""
    from concept_labeler import label_all_clusters

    clusters = {
        0: {'nodes': ['a.ts']},
        1: {'nodes': ['b.ts']},
    }
    file_data = {
        'a.ts': {'symbols': ['A'], 'first_sentence': ''},
        'b.ts': {'symbols': ['B'], 'first_sentence': ''},
    }
    cluster_labels = {0: 'Alpha', 1: 'Beta'}

    def fake_llm(prompt: str) -> str:
        # Echo the current_label back as concept so we can verify routing.
        if 'Alpha' in prompt:
            return '{"concept": "AlphaConcept", "description": "", "sub_features": []}'
        return '{"concept": "BetaConcept", "description": "", "sub_features": []}'

    out = label_all_clusters(clusters, file_data, cluster_labels,
                              llm=fake_llm, cache_dir=tmp_path, max_workers=2)

    assert out[0]['concept'] == 'AlphaConcept'
    assert out[1]['concept'] == 'BetaConcept'
