"""Helpers for keeping competitor shot references inside one coherent source."""
import random
import re


def linked_rival_ids(shot):
    links = shot.get("关联竞对视频") or []
    return {item.get("id") if isinstance(item, dict) else item for item in links}


def _time_range(shot):
    values = re.findall(r"\d+(?:\.\d+)?", str(shot.get("时间区间") or ""))
    if len(values) >= 2:
        start, end = float(values[0]), float(values[1])
        if end >= start:
            return start, end
    return None


def _shot_sequence(shot):
    match = re.search(r"-S(\d+)$", str(shot.get("分镜编号") or ""), re.IGNORECASE)
    return int(match.group(1)) if match else None


def _runs(rows, ordering):
    if not rows:
        return []
    rows = sorted(rows, key=ordering)
    runs = [[rows[0]]]
    for previous, current in zip(rows, rows[1:]):
        prev_time, curr_time = _time_range(previous), _time_range(current)
        prev_seq, curr_seq = _shot_sequence(previous), _shot_sequence(current)
        if prev_time and curr_time:
            connected = curr_time[0] >= prev_time[0] and curr_time[0] <= prev_time[1] + 3
        elif prev_seq is not None and curr_seq is not None:
            connected = curr_seq == prev_seq + 1
        else:
            connected = False
        if connected:
            runs[-1].append(current)
        else:
            runs.append([current])
    return runs


def coherent_shot_chain(rival_id, shots, usage=None, limit=3, draw_counts=None):
    """Return up to ``limit`` chronological adjacent shots from one rival.

    If the source has no usable timing or sequence metadata, return one shot at
    most. This is safer than inventing continuity or filling from another video.
    """
    usage = usage or {}
    related = [shot for shot in shots if rival_id in linked_rival_ids(shot)]
    if not related or limit <= 0:
        return []

    if draw_counts is not None:
        # In uniform mode exhaust eligible source shots before starting a new
        # round, even if that leaves a shorter continuous chain this time.
        least = min(draw_counts.get(shot['_record_id'], 0) for shot in related)
        related = [shot for shot in related if draw_counts.get(shot['_record_id'], 0) == least]
        usage = {}

    has_all_times = all(_time_range(shot) for shot in related)
    has_all_sequences = all(_shot_sequence(shot) is not None for shot in related)
    if has_all_times:
        ordering = lambda shot: (_time_range(shot)[0], _time_range(shot)[1], shot.get("_record_id", ""))
    elif has_all_sequences:
        ordering = lambda shot: (_shot_sequence(shot), shot.get("_record_id", ""))
    else:
        ordering = lambda shot: (shot.get("_record_id", ""),)

    runs = _runs(related, ordering)
    windows = []
    for run in runs:
        width = min(limit, len(run))
        windows.extend(run[start:start + width] for start in range(len(run) - width + 1))
    if not windows:
        return []
    longest = max(map(len, windows))
    candidates = [window for window in windows if len(window) == longest]
    lowest_use = min(sum(usage.get(shot.get("_record_id"), 0) for shot in chain) for chain in candidates)
    candidates = [chain for chain in candidates
                 if sum(usage.get(shot.get("_record_id"), 0) for shot in chain) == lowest_use]
    return random.choice(candidates)
