"""Geometry-based cable reduction for classic HH and Exp2Syn."""
from __future__ import annotations

from collections import defaultdict, deque
from functools import lru_cache
import importlib.util
import math
from pathlib import Path

import numpy as np

try:
    from .point_hh_worker import _exp2syn_peak_factor, _hh_rates, _stimulus_current
except ImportError:
    # The external Windows runtime may exclude the script directory.
    _spec = importlib.util.spec_from_file_location(
        "digifly_hh_equations", Path(__file__).with_name("point_hh_worker.py"))
    _equations = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_equations)
    _exp2syn_peak_factor = _equations._exp2syn_peak_factor
    _hh_rates = _equations._hh_rates
    _stimulus_current = _equations._stimulus_current

MAX_GROUP_LENGTH_UM = 0.125


def _collapse(cells, contacts, worker):
    """Keep soma and contact junctions and conserve each group's membrane."""
    properties = []
    by_cell = {}
    neighbors = defaultdict(dict)

    def new_state(area=0.0, cm=0.0, gnabar=0.0, gkbar=0.0, gl=0.0,
                  ena=0.0, ek=0.0, el=0.0):
        index = len(properties)
        properties.append((area, cm, gnabar, gkbar, gl, ena, ek, el))
        return index

    for body, cell in cells.items():
        plan = cell["regional_plan"]
        sections = {s["node_id"]: s for s in plan["sections"]}
        rows = worker._swc_rows(Path(cell["swc_path"]))
        radii = {node: radius for node, _, _, _, _, radius, _ in rows}
        roots = [r[0] for r in rows if r[-1] == -1]
        if len(roots) != 1 or any(r <= 0 for r in radii.values()):
            raise ValueError(f"Reduced HH requires a single cable with positive radii for {body}.")
        children = defaultdict(list)
        for section in sections.values():
            if section["nseg"] != 1 or len(section["path"]) != 2 or section["uses_degenerate_stub"]:
                raise ValueError(f"Reduced HH requires one segment per nonzero SWC edge for {body}.")
            parent, child = section["path"]
            children[parent].append(child)

        soma, = (s["node_id"] for s in sections.values() if s["is_target_soma"])
        group_for_edge = {}
        length_by_group, area_by_group, profile_by_group = [], [], []
        profiles = {}
        queue = deque(roots)
        while queue:
            parent = queue.popleft()
            for child in sorted(children[parent]):
                queue.append(child)
                section = sections[child]
                hh = section["hh"]
                region_hh = worker._region_hh(hh, soma=section["soma"])
                profile = (
                    float(hh["cm_uF_cm2"]), region_hh["gnabar"], region_hh["gkbar"],
                    region_hh["gl"], float(hh["ena_mV"]), float(hh["ek_mV"]), region_hh["el"],
                    float(hh["ra_ohm_cm"]), bool(section["soma"]),
                )
                profiles[child] = profile
                group = group_for_edge.get(parent)
                length = section["length_um"]
                if (group is None or soma in (parent, child) or profile != profile_by_group[group]
                        or length_by_group[group] + length > MAX_GROUP_LENGTH_UM):
                    group = len(area_by_group)
                    length_by_group.append(0.0)
                    area_by_group.append(0.0)
                    profile_by_group.append(profile)
                group_for_edge[child] = group
                length_by_group[group] += length
                area_by_group[group] += section["surface_area_um2"]
        if group_for_edge.keys() != sections.keys():
            raise ValueError(f"Reduced HH cable is disconnected for {body}.")

        first_state = len(properties)
        for area, profile in zip(area_by_group, profile_by_group):
            cm, na, k, leak, ena, ek, el, _, _ = profile
            # µm² × µF/cm² to pF. µm² × S/cm² to nS.
            new_state(area, area * cm * 0.01, area * na * 10,
                      area * k * 10, area * leak * 10, ena, ek, el)
        edge_state = {edge: first_state + index for edge, index in group_for_edge.items()}
        at_junction = defaultdict(lambda: defaultdict(float))
        for section in sections.values():
            parent, child = section["path"]
            state = edge_state[child]
            radius_a, radius_b = radii[parent], radii[child]
            radius_mid = (radius_a + radius_b) / 2
            ra = profiles[child][-2]
            # Tapered half-edge resistance in MΩ, from the integral of 1/r².
            base = 0.01 * ra * (section["length_um"] / 2) / math.pi
            at_junction[parent][state] += 1000 * radius_a * radius_mid / base
            at_junction[child][state] += 1000 * radius_mid * radius_b / base

        destinations = {}
        required = defaultdict(list)
        for contact in contacts:
            if str(contact["post_id"]) != body:
                continue
            node = int(contact["post_run_node_id"])
            fraction = contact.get("post_source_edge_fraction")
            if fraction is None:
                site = plan["node_sites"][node]
                section_id, position = site["section_id"], site["section_pos"]
            else:
                section_id, position = worker._regional_contact_site(plan, node, float(fraction))
            spec = plan["sections"][section_id]
            identity = contact["contact_id"]
            if 0.0 < position < 1.0:
                # With one CV, NEURON maps an interior attachment to the CV center.
                destinations[identity] = edge_state[spec["node_id"]]
            else:
                required[spec["path"][int(position)]].append(identity)
        for junction, conductances in at_junction.items():
            if len(conductances) == 1 and junction not in required:
                continue  # A sealed cable end carries no current.
            state = new_state()
            for identity in required.get(junction, ()):
                destinations[identity] = state
            for center, conductance in conductances.items():
                neighbors[state][center] = conductance
                neighbors[center][state] = conductance
        by_cell[body] = {
            "soma": edge_state[soma], "edge_state": edge_state, "destinations": destinations,
            "groups": len(area_by_group), "native_edges": len(sections),
        }

    soma_states = [cell["soma"] for cell in by_cell.values()]
    order, queue = [], deque(soma_states)
    parent = np.full(len(properties), -2, dtype=np.int64)
    parent[soma_states] = -1
    conductance = np.zeros(len(properties))
    while queue:
        upstream = queue.popleft()
        order.append(upstream)
        for child, g in neighbors[upstream].items():
            if child == parent[upstream]:
                continue
            if parent[child] != -2:
                raise ValueError("Reduced HH axial graph is not a forest.")
            parent[child] = upstream
            conductance[child] = g
            queue.append(child)
    if len(order) != len(properties):
        raise ValueError("Reduced HH axial graph is disconnected.")
    diagonal = np.array([math.fsum(neighbors[i].values()) for i in range(len(properties))])
    return by_cell, (np.array(order), parent, conductance, diagonal), np.array(properties).T


def _solve_tree(order, parent, conductance, diagonal, rhs):
    """Implicit voltage solve by elimination from leaves to roots."""
    for child in order[::-1]:
        upstream = parent[child]
        if upstream != -1:
            factor = conductance[child] / diagonal[child]
            diagonal[upstream] -= factor * conductance[child]
            rhs[upstream] += factor * rhs[child]
    for child in order:
        upstream = parent[child]
        if upstream != -1:
            rhs[child] += conductance[child] * rhs[upstream]
        rhs[child] /= diagonal[child]
    return rhs


@lru_cache(maxsize=1)
def _optional_accelerator():
    # An existing Numba installation speeds the same solver. Environments
    # without it run the identical Python kernel and need only NumPy.
    try:
        from numba import njit
        from numba.types import float64, int64
        signature = float64[:](int64[::1], int64[::1], float64[::1], float64[::1], float64[::1])
        return njit(signature, cache=False)(_solve_tree), "Numba"
    except Exception:
        return _solve_tree, "Python"


def _select_solver(order, parent, axial_g):
    tree, version = _optional_accelerator()
    if version == "Numba":
        return lambda diagonal, rhs: tree(order, parent, axial_g, diagonal, rhs), version
    try:
        from scipy.sparse import coo_matrix
        from scipy.sparse.linalg import spsolve
    except ImportError:
        return lambda diagonal, rhs: tree(order, parent, axial_g, diagonal, rhs), version
    all_states = np.arange(len(parent))
    child = np.flatnonzero(parent >= 0)
    upstream = parent[child]
    matrix = coo_matrix(
        (np.concatenate((np.zeros(len(parent)), -axial_g[child], -axial_g[child])),
         (np.concatenate((all_states, child, upstream)), np.concatenate((all_states, upstream, child)))),
        shape=(len(parent), len(parent)),
    ).tocsc()
    offsets = np.array([
        start + np.searchsorted(matrix.indices[start:stop], col)
        for col, (start, stop) in enumerate(zip(matrix.indptr[:-1], matrix.indptr[1:]))
    ])

    def sparse_step(diagonal, rhs):
        matrix.data[offsets] = diagonal
        return spsolve(matrix, rhs)

    return sparse_step, "SciPy"


def run_condition(cells, experiment, stimulus, condition, edges, worker, check_cancel):
    dt = float(experiment["integration_dt_ms"])
    temperature = float(experiment["temperature_C"])
    interval = float(experiment["recording"]["sample_dt_ms"])
    stride = round(interval / dt)
    steps = round(float(experiment["duration_ms"]) / dt)
    contacts = edges["chemical_edges"] if condition.get("chemical_synapses_enabled", True) else []
    by_cell, (order, parent, axial_g, axial_diag), properties = _collapse(cells, contacts, worker)
    area, cap, gna, gk, gl, ena, ek, el = properties
    current_fraction = np.zeros(len(area))
    recording = experiment["recording"]
    recorded = {}
    stim_ids = set(stimulus.get("target_neuron_ids") or cells)
    record_ids = set(recording.get("target_neuron_ids") or cells)
    for body, model in by_cell.items():
        cell = cells[body]
        plan = cell["regional_plan"]
        states = model["edge_state"]
        if body in stim_ids:
            sites = worker._regional_sites(plan, stimulus["target_region"], cell)
            fractions = worker._area_weighted_fractions([s["surface_area_um2"] for s in sites])
            for site, fraction in zip(sites, fractions):
                current_fraction[states[site["node_id"]]] += fraction
        if body in record_ids:
            sites = worker._regional_sites(plan, recording["target_region"], cell)
            for site in sites:
                recorded[body, str(site["source_node_id"])] = states[site["node_id"]]
    states_to_record = np.array(list(dict.fromkeys(recorded.values())), dtype=np.int64)
    recording_index = {state: i for i, state in enumerate(states_to_record)}
    values = np.empty((len(states_to_record), steps // stride + 1))
    scale = float(condition.get("stimulus_scale", 1.0)) * 1000  # nA to pA

    # Equal-kinetics synapses at the same state sum linearly. Different
    # kinetics or reversals keep separate state, even at the same location.
    pools = {}
    delivery = defaultdict(lambda: defaultdict(list))
    for contact in contacts:
        body = str(contact["post_id"])
        destination = by_cell[body]["destinations"][contact["contact_id"]]
        tau1, tau2, reversal = (float(contact[k]) for k in ("tau1_ms", "tau2_ms", "reversal_mV"))
        pool = pools.setdefault((destination, tau1, tau2, reversal), len(pools))
        delay = round(float(contact["delay_ms"]) / dt)
        delivery[str(contact["pre_id"])][delay].append((pool, float(contact["weight_uS"]) * 1000))
    deliveries = {
        body: [(delay, np.array([p for p, w in input_rows], dtype=np.int64),
                np.array([w for p, w in input_rows]))
               for delay, input_rows in by_delay.items()]
        for body, by_delay in delivery.items()
    }
    post = np.array([p[0] for p in pools], dtype=np.int64)
    decay1 = np.array([math.exp(-dt / p[1]) for p in pools])
    decay2 = np.array([math.exp(-dt / p[2]) for p in pools])
    reversal = np.array([p[3] for p in pools])
    normalizer = np.array([_exp2syn_peak_factor(p[1], p[2]) for p in pools])
    x = np.zeros(len(pools))
    y = np.zeros(len(pools))
    event_queue = defaultdict(list)

    membrane = cap > 0
    voltage = np.full(len(area), float(experiment["initial_voltage_mV"]))
    rate = np.asarray(_hh_rates(voltage[membrane], temperature)).reshape(3, 2, -1)
    gates = rate[:, 0] / rate.sum(axis=1)
    steady = cap / dt
    leak_current = gl * el
    threshold = float(edges["chemical_synapse_policy"]["spike_threshold_mV"])
    solve, accelerator = _select_solver(order, parent, axial_g)
    for step in range(steps + 1):
        if step % 64 == 0:
            check_cancel()
        if step % stride == 0:
            values[:, step // stride] = voltage[states_to_record]
        if step == steps:
            break
        for indices, weights in event_queue.pop(step, ()):
            np.add.at(x, indices, weights)
            np.add.at(y, indices, weights)
        conductance = (y - x) * normalizer
        gsyn = np.bincount(post, weights=conductance, minlength=len(area))
        syn_current = np.bincount(post, weights=conductance * reversal, minlength=len(area))
        na, k = gna.copy(), gk.copy()
        na[membrane] *= gates[0]**3 * gates[1]
        k[membrane] *= gates[2]**4
        current = _stimulus_current((step + 0.5) * dt, stimulus)
        rhs = steady * voltage + na * ena + k * ek + leak_current + syn_current
        if current:
            rhs += scale * current * current_fraction
        new_voltage = solve(axial_diag + steady + na + k + gl + gsyn, rhs)
        if not np.isfinite(new_voltage).all():
            raise ValueError("Reduced HH voltage became non-finite. Try a smaller integration step.")
        rate = np.asarray(_hh_rates(new_voltage[membrane], temperature)).reshape(3, 2, -1)
        total = rate.sum(axis=1)
        infinity = rate[:, 0] / total
        gates = infinity + (gates - infinity) * np.exp(-dt * total)
        x *= decay1
        y *= decay2
        for source, targets in deliveries.items():
            soma = by_cell[source]["soma"]
            if voltage[soma] < threshold <= new_voltage[soma]:
                for delay, indices, weights in targets:
                    if step + 1 + delay < steps:
                        event_queue[step + 1 + delay].append((indices, weights))
        voltage = new_voltage
    check_cancel()
    times = np.arange(values.shape[1]) * interval
    traces = {key: (times, values[recording_index[state]]) for key, state in recorded.items()}
    details = {
        "group_length_cap_um": MAX_GROUP_LENGTH_UM,
        "accelerator": accelerator,
        "cells": {body: {
            "membrane_states": model["groups"],
            "source_edges": model["native_edges"],
            "source_node_to_membrane_state": {
                str(s["source_node_id"]): model["edge_state"][s["node_id"]]
                for s in cells[body]["regional_plan"]["sections"]
            },
            "chemical_contact_to_state": model["destinations"],
        } for body, model in by_cell.items()},
        "chemical_contacts": len(contacts),
        "axial_junction_states": len(area) - int(np.count_nonzero(membrane)),
    }
    return traces, f"NumPy {np.__version__} / {accelerator}", details
