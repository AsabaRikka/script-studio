"""Map generated scripts to the existing Base parent/shot record contract."""
import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
import time
from datetime import datetime, timezone, timedelta
from manual_edit import validate_manual
from script_sources import used_weapons, normalize_source_ids
from script_labels import normalize_labels


class WritebackError(ValueError):
    def __init__(self, message, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain


def compact(text):
    return re.sub(r"\s", "", str(text or ""))


def links(ids):
    return [{"id": value} for value in dict.fromkeys(ids) if value]


def link_ids(values):
    return [v.get("id") if isinstance(v, dict) else v for v in (values or [])]


def build_plan(request, load_cache):
    d = request["data"]
    code = str(d.get("material_code") or "").strip()
    if not code or len(code) > 150 or re.search(r'[\\/:*?"<>|\x00-\x1f]', code) or code.endswith((".", " ")):
        raise WritebackError("素材编号为空或含不安全字符，请重新生成或修正编号")
    shots = d.get("shots")
    if not isinstance(shots, list) or not 1 <= len(shots) <= 199:
        raise WritebackError("缺少完整分镜，不能只写主记录；请恢复原批次或重新生成")
    full = d.get("full_script", "")
    if not isinstance(full, str) or not full.strip():
        raise WritebackError("缺少整篇文案")
    if any(not isinstance(s, dict) or not isinstance(s.get("text"), str) or not s["text"].strip() for s in shots):
        raise WritebackError("分镜台词为空或格式错误")
    if compact(full) != compact("".join(s["text"] for s in shots)):
        raise WritebackError("分镜台词拼接与整篇文案不一致，请修正后写入，避免主记录和子记录错位")
    manual = d.get("manual_script", d.get("人工修改稿", ""))
    manual_shots = d.get("manual_shots")
    try:
        manual, manual_shots = validate_manual(d, manual, manual_shots)
    except ValueError as error:
        raise WritebackError(str(error))

    slot = d.get("source_slot") or {}
    # Persisted sources are authoritative, even if the preview has since changed.
    weapon_ids = [slot["weapon"]["id"]] if slot.get("weapon") else [request.get("weapon_id")]
    rival_ids = [r["id"] for r in slot["rivals"]] if "rivals" in slot else request.get("rival_ids", [])
    shot_ids = [s["id"] for s in slot["shots"]] if "shots" in slot else request.get("shot_ids", [])
    comp_ids = [c["id"] for c in slot["components"]] if "components" in slot else request.get("comp_ids", [])
    source_shots = slot["shots"] if "shots" in slot else load_cache("rival_shots")
    source_comps = slot["components"] if "components" in slot else load_cache("rival_comp")
    welfare_id = (slot.get("welfare") or {}).get("id") if "welfare" in slot else request.get("welfare_id")
    try:
        emotions = normalize_labels(slot.get("emotions", request.get("emotions", [])), '主记录情绪标签')
    except ValueError as error:
        raise WritebackError(str(error))
    mode = slot.get("mode", request.get("mode", "多篇竞对-多向融合"))
    if "used_weapon_ids" in d or "manual_script" in d or "人工修改稿" in d:
        candidates = slot.get("weapon_candidates") or ([slot["weapon"]] if slot.get("weapon") else [])
        # Resolve full names against local facts; never trust model-provided link IDs.
        candidates = [*candidates, *load_cache("weapons")]
        effective_script = manual if manual.strip() else full
        weapon_ids = [row["id"] for row in used_weapons(effective_script, candidates)]
    if mode == "本体事实-自主原创" and not d.get('revision_source'):
        rival_ids, shot_ids, comp_ids = [], [], []
    rivals_by_id = {r["_record_id"]: r for r in load_cache("rival_struct")}
    source_rivals = {r["id"]: r for r in slot.get("rivals", [])}
    references = []
    for index, rid in enumerate(dict.fromkeys(rival_ids), 1):
        r = source_rivals.get(rid, {})
        cached = rivals_by_id.get(rid, {})
        raw = r.get("raw_script") or cached.get("口播文案")
        if not raw:
            raise WritebackError("参考竞对缺少原稿全文，请刷新素材或恢复来源后写入")
        source_code = r.get("code") or cached.get("素材编号") or rid
        references.append(f"【参考来源{index} {source_code}】：\n{raw}")
    reference = "\n\n".join(references) if references else "本体玩法库自主原创"
    if d.get('revision_source'):
        revision = d['revision_source']
        parts = ['【改写前文案】\n' + revision.get('script','')]
        if revision.get('reference'):
            parts.append('【补充参考】\n' + revision['reference'])
        if references:
            parts.append(reference)
        reference = '\n\n'.join(parts)

    structure = d.get("rhythm_structure") or ""
    if isinstance(structure, list):
        structure = structure[0] if structure else ""
    # Existing drafts often encode the same rhythm tag as the first summary segment.
    if not structure:
        candidate = re.split(r"[|｜]", d.get("dup_summary", ""))[0].strip()
        known = {v for row in load_cache("main") for v in (row.get("二创节奏结构") or [])}
        if candidate in known:
            structure = candidate
    parent = {
        "素材编号": code, "核心卖点": d.get("title", ""), "参考文案": reference,
        "二创文案": full, "钩子爆点分析": d.get("hook_analysis", ""),
        "正文爆点分析": d.get("body_analysis", ""), "节奏结构说明": d.get("rhythm_desc", ""),
        "查重摘要": d.get("dup_summary", ""), "创作模式": [mode],
        "情绪触发": list(dict.fromkeys(emotions)), "状态": ["待制作"],
        "关联武器": links(weapon_ids), "参考竞对": links(rival_ids),
        "参考竞对分镜": links(shot_ids), "参考组件": links(comp_ids),
        "关联福利点": links([welfare_id]),
        "日期": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d 00:00"),
    }
    if structure:
        parent["二创节奏结构"] = [structure]
    if "manual_script" in d or "人工修改稿" in d:
        parent["人工修改稿"] = manual or None
    children = []
    total_duration = 0
    synced_manual = manual_shots is not None and compact(manual) == compact(''.join(s['text'] for s in manual_shots))
    for index, s in enumerate(shots, 1):
        revision = manual_shots[index-1] if manual_shots is not None else None
        effective = revision or s
        human_revision = "manual_script" in d or "人工修改稿" in d
        try:
            duration = float(effective.get("duration") or 0)
        except (TypeError, ValueError):
            raise WritebackError(f"第{index}镜时长无效")
        # The existing Base speed formula still reads the preserved AI scene text.
        required = max(len(compact(effective["text"])), len(compact(s["text"]))) / 5.6
        if not math.isfinite(duration) or duration < 0 or (not human_revision and (duration <= 0 or duration < required - 1e-9)):
            raise WritebackError(f"第{index}镜时长无效或语速超过5.6字/秒，请修正后写入")
        total_duration += duration
        child = {
            "素材编号": f"{code}-S{index:02d}", "分镜序号": index,
            "参考原文": s.get("ref_text", ""), "分镜文案": s["text"],
            "分镜时长(秒)": duration, "分镜画面描述": effective.get("visual", ""),
            "对应游戏画面": effective.get("game_action", ""),
        }
        if revision is not None and synced_manual:
            child["人工修改稿"] = revision["text"]
        elif "manual_script" in d or "人工修改稿" in d:
            child["人工修改稿"] = None
        # Link only explicit per-shot attribution. Never infer it from array position.
        for key, field, allowed, sources in (("source_shot_ids", "参考竞对分镜", shot_ids, source_shots),
                                             ("source_comp_ids", "参考组件", comp_ids, source_comps)):
            try:
                ids = normalize_source_ids(s.get(key), allowed, sources, f"第{index}镜{field}")
            except ValueError as error:
                raise WritebackError(str(error))
            if ids:
                child[field] = links(ids)
        if s.get("bgm"):
            child["BGM"] = s["bgm"]
        try:
            labels = normalize_labels(s.get('emotions'), f'第{index}镜情绪标签')
        except ValueError as error:
            raise WritebackError(str(error))
        if labels:
            child['情绪触发'] = labels
        children.append(child)
    if not human_revision and d.get("max_duration") and total_duration > float(d["max_duration"]) + 1e-9:
        raise WritebackError("分镜总时长超过该草稿的时长上限，请修正后写入")
    return {"parent": parent, "children": children, "shot_count": len(children),
            "total_duration": round(total_duration, 3), "mode": mode,
            "manual_edit": "manual_script" in d or "人工修改稿" in d}


class LarkClient:
    def __init__(self, command, target, cwd):
        self.command, self.target, self.cwd = command, target, cwd

    def run(self, action, payload=None, table=None, extra=()):
        argv = [self.command, "base", action, "--base-token", self.target["base_token"],
                "--table-id", table or self.target["table_ids"]["main"], "--as", "user", "--format", "json"]
        path = None
        try:
            if payload is not None:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.cwd,
                                                  prefix=".studio-wb-", suffix=".json", delete=False) as f:
                    json.dump(payload, f, ensure_ascii=False)
                    path = f.name
                flag = "--filter-json" if action == "+record-list" else "--json"
                argv += [flag, "@" + os.path.relpath(path, self.cwd)]
            argv += list(extra)
            result = subprocess.run(argv, cwd=self.cwd, capture_output=True, timeout=45)
            raw = (result.stdout if result.returncode == 0 else result.stderr or result.stdout).decode("utf-8", errors="replace")
            try:
                envelope = json.loads(raw)
            except (ValueError, TypeError):
                raise WritebackError("飞书 CLI 返回了无法确认的响应；请先核对远端记录再重试", uncertain=True)
            if not isinstance(envelope, dict):
                raise WritebackError("飞书 CLI 响应结构无法确认，请核对远端记录后重试", uncertain=True)
            if result.returncode or envelope.get("ok") is not True:
                error = envelope.get("error") or {}
                if error.get("type") in ("authentication", "authorization"):
                    raise WritebackError("飞书用户授权失效或权限不足，请重新授权后重试")
                message = str(error.get("message") or "飞书返回错误")
                raise WritebackError(message.replace(self.target["base_token"], "[Base]"))
            if envelope.get("identity") != "user":
                raise WritebackError("飞书操作者不是当前用户，已停止写入")
            data = envelope.get("data") or {}
            if not isinstance(data, dict):
                raise WritebackError("飞书 CLI 数据结构无法确认，请核对远端记录后重试", uncertain=True)
            ignored = data.get("ignored_fields") or (data.get("record") or {}).get("ignored_fields")
            if ignored:
                raise WritebackError("飞书忽略了部分字段，不能视为完整写入，请检查字段结构", uncertain=True)
            return data
        except subprocess.TimeoutExpired:
            raise WritebackError("飞书请求超时，结果尚不确定；请稍后核对后重试", uncertain=True)
        finally:
            if path and os.path.exists(path):
                os.remove(path)


def rows_from_response(data):
    if data.get("has_more"):
        raise WritebackError("远端查询结果未完整返回，已停止写入，请先核对重复编号或分镜数量")
    if isinstance(data.get("records"), list):
        if any(not isinstance(r, dict) or not isinstance(r.get("fields"), dict)
               or not isinstance(r.get("record_id"), str) for r in data["records"]):
            raise WritebackError("飞书记录读取结构不完整，已停止写入")
        return [{**r["fields"], "_record_id": r["record_id"]} for r in data["records"]]
    fields, values, ids = data.get("fields"), data.get("data"), data.get("record_id_list")
    if not isinstance(fields, list) or not isinstance(values, list) or not isinstance(ids, list) or len(ids) != len(values):
        raise WritebackError("飞书记录读取结构不完整，已停止写入")
    names = [f.get("name") if isinstance(f, dict) else f for f in fields]
    if any(not isinstance(n, str) for n in names) or len(set(names)) != len(names) or any(not isinstance(rid, str) for rid in ids):
        raise WritebackError("飞书字段或记录 ID 不完整，已停止写入")
    if any(not isinstance(row, list) or len(row) != len(names) for row in values):
        raise WritebackError("飞书字段与记录无法对应，已停止写入")
    return [{**dict(zip(names, row)), "_record_id": rid} for row, rid in zip(values, ids)]


def created_ids(data, count):
    ids = data.get("record_id_list") or (data.get("record") or {}).get("record_id_list")
    if not isinstance(ids, list) or len(ids) != count or len(set(ids)) != count or any(not isinstance(i, str) or not i.startswith("rec") for i in ids):
        raise WritebackError("飞书返回的新增记录 ID 不完整，请核对远端记录后重试", uncertain=True)
    return ids


def validate_schema(plan, data, target):
    fields = data.get("fields")
    if not isinstance(fields, list):
        raise WritebackError("飞书字段结构无法读取，已停止写入")
    if any(not isinstance(f, dict) or not isinstance(f.get("name"), str) for f in fields):
        raise WritebackError("飞书字段定义不完整，已停止写入")
    schema = {f["name"]: f for f in fields}
    records = [plan["parent"], *plan["children"], {"父记录": [{"id": "rec-parent"}]}]
    table_keys = {"关联武器":"weapons", "参考竞对":"rival_struct", "参考竞对分镜":"rival_shots",
                  "参考组件":"rival_comp", "关联福利点":"welfare", "父记录":"main"}
    for record in records:
        for name, value in record.items():
            field = schema.get(name)
            if not field:
                raise WritebackError(f"飞书表缺少字段：{name}")
            kind = field.get("type")
            if kind in ("formula", "lookup", "created_at", "updated_at", "created_by", "updated_by", "auto_number"):
                raise WritebackError(f"{name}是只读字段，不能写入")
            expected = "link" if name in table_keys else ("select" if isinstance(value, list) else
                       "number" if isinstance(value, (float, int)) else "datetime" if name == "日期" else "text")
            if kind != expected:
                raise WritebackError(f"飞书字段类型不符：{name}应为{expected}，当前为{kind}")
            if kind == "link":
                link_table = field.get("link_table")
                if isinstance(link_table, dict):
                    link_table = link_table.get("id") or link_table.get("table_id")
                if link_table != target["table_ids"].get(table_keys[name]):
                    raise WritebackError(f"{name}的关联目标表不符，请核对配置")
            if kind == "select":
                options = {option["name"] if isinstance(option, dict) else option for option in field.get("options", [])}
                unknown = [option for option in value if option not in options]
                if unknown:
                    raise WritebackError(f"{name}包含表中不存在的选项：{'、'.join(unknown)}；可用标签：{'、'.join(sorted(options))}")
                if not field.get("multiple", False) and len(value) > 1:
                    raise WritebackError(f"{name}只允许单选")
    return schema


def field_matches(name, actual, expected):
    if expected is None:
        return actual in (None, "", [])
    if isinstance(expected, list):
        if name in ('关联武器', '参考竞对', '参考竞对分镜', '参考组件', '关联福利点', '父记录'):
            return set(link_ids(actual)) == set(link_ids(expected))
        return set(actual or []) == set(expected)
    if name == "日期":
        return str(actual or "")[:10] == expected[:10]
    if isinstance(expected, (int, float)):
        try:
            return abs(float(actual) - expected) < 1e-6
        except (ValueError, TypeError):
            return False
    return actual == expected or (expected == "" and actual is None)


def save_receipt(path, receipt):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as f:
        json.dump(receipt, f, ensure_ascii=False, indent=2)
    os.replace(temporary, path)


def execute_plan(plan, client, receipt_dir):
    target = client.target
    target_key = target["base_token"] + ":" + target["table_ids"]["main"]
    receipt_key = hashlib.sha256((target_key + ":" + plan["parent"]["素材编号"]).encode()).hexdigest()
    path = os.path.join(receipt_dir, receipt_key + ".json")
    receipt = {}
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            receipt = json.load(f)
    snapshot = json.loads(json.dumps(plan, ensure_ascii=False))
    snapshot["parent"].pop("日期", None)
    snapshot.pop("manual_edit", None)
    fingerprint = hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    if (receipt.get("record_id") or receipt.get("uncertain_codes")) and receipt.get("fingerprint") and receipt["fingerprint"] != fingerprint and (receipt.get("uncertain_codes") or not plan.get("manual_edit")):
        raise WritebackError("同一素材编号的内容已改变，请使用新编号，避免覆盖已写分镜")
    receipt.update({"fingerprint":fingerprint, "material_code":plan["parent"]["素材编号"]})
    save_receipt(path, receipt)
    schema = validate_schema(plan, client.run("+field-list"), target)
    # Validate every actual link ID against its configured destination before writing.
    for field, key in (("关联武器","weapons"), ("参考竞对","rival_struct"), ("参考竞对分镜","rival_shots"),
                       ("参考组件","rival_comp"), ("关联福利点","welfare")):
        ids = link_ids(plan["parent"][field])
        if ids:
            data = client.run("+record-get", {"record_id_list":ids}, table=target["table_ids"][key])
            found = {row["_record_id"] for row in rows_from_response(data)}
            if found != set(ids):
                raise WritebackError(f"{field}中部分来源已不存在，请刷新后重新选择")
    code = plan["parent"]["素材编号"]
    codes = [code, *(s["素材编号"] for s in plan["children"])]
    found_rows = rows_from_response(client.run("+record-list", {"logic":"or", "conditions":[["素材编号","==",c] for c in codes]}, extra=("--limit","200")))
    by_code = {}
    for row in found_rows:
        row_code = row.get("素材编号")
        if row_code in by_code:
            raise WritebackError(f"远端有重复编号 {row_code}，请先核对，已停止写入")
        by_code[row_code] = row
    parent_row = by_code.get(code)
    if parent_row and (parent_row.get("父记录") or compact(parent_row.get("二创文案")) != compact(plan["parent"]["二创文案"])):
        raise WritebackError("远端同编号记录属于不同文案或子记录，已停止覆盖")
    uncertain_codes = receipt.get("uncertain_codes", [])
    if any(c not in by_code for c in uncertain_codes):
        raise WritebackError("上次写入结果尚不确定，远端未查到全部记录；请稍后核对，暂不重复创建", uncertain=True)
    receipt.pop("uncertain_codes", None)
    if parent_row:
        parent_id = parent_row["_record_id"]
        remote_children = rows_from_response(client.run("+record-list", {"logic":"and", "conditions":[["父记录","intersects",[{"id":parent_id}]]]}, extra=("--limit","200")))
        if any(row.get("素材编号") not in codes[1:] for row in remote_children):
            raise WritebackError("主记录已有额外分镜，需先核对数量；已停止写入，保留原记录")
    for child_code in codes[1:]:
        existing = by_code.get(child_code)
        if existing and (not parent_row or link_ids(existing.get("父记录")) != [parent_row["_record_id"]]):
            raise WritebackError("同编号分镜属于其他主记录，已停止覆盖")
        if existing and plan.get("manual_edit"):
            original = next(child for child in plan["children"] if child["素材编号"] == child_code)
            if compact(existing.get("分镜文案")) != compact(original["分镜文案"]):
                raise WritebackError("远端分镜原稿已变更，请核对后使用新编号")
    expected = {}
    try:
        if parent_row:
            parent_id = parent_row["_record_id"]
            parent = dict(plan["parent"])
            # Preserve the original creation date and human-edited fields.
            if parent_row.get("日期"):
                parent.pop("日期", None)
            if parent_row.get("状态"):
                parent["状态"] = parent_row["状态"]
            receipt["record_id"] = parent_id
            save_receipt(path, receipt)
            for field in ("父记录", "参考原文", "分镜序号", "分镜文案", "分镜时长(秒)", "分镜画面描述", "对应游戏画面", "BGM"):
                if field in schema:
                    parent[field] = None
            updates = {name:value for name,value in parent.items() if not field_matches(name, parent_row.get(name), value)}
            if updates:
                client.run("+record-batch-update", {"update_records":{parent_id:updates}})
        else:
            parent = dict(plan["parent"])
            receipt["uncertain_codes"] = [code]
            save_receipt(path, receipt)
            parent_id = created_ids(client.run("+record-batch-create", {"create_records":[parent]}), 1)[0]
            receipt.pop("uncertain_codes", None)
        receipt["record_id"] = parent_id
        save_receipt(path, receipt)
        expected[parent_id] = parent
        missing, updates, child_ids = [], {}, {}
        for child in plan["children"]:
            fields = {**child, "父记录":[{"id":parent_id}]}
            existing = by_code.get(child["素材编号"])
            if existing:
                if link_ids(existing.get("父记录")) != [parent_id]:
                    raise WritebackError("同编号分镜属于其他主记录，已停止覆盖")
                rid = existing["_record_id"]
                delta = {k:v for k,v in fields.items() if not field_matches(k,existing.get(k),v)}
                if delta:
                    updates[rid] = delta
                child_ids[child["素材编号"]] = rid
                expected[rid] = fields
            else:
                missing.append(fields)
        if updates:
            client.run("+record-batch-update", {"update_records":updates})
        if missing:
            receipt["uncertain_codes"] = [s["素材编号"] for s in missing]
            save_receipt(path, receipt)
            ids = created_ids(client.run("+record-batch-create", {"create_records":missing}), len(missing))
            for rid, child in zip(ids, missing):
                expected[rid] = child
                child_ids[child["素材编号"]] = rid
            receipt.pop("uncertain_codes", None)
        receipt["child_record_ids"] = [child_ids[c] for c in codes[1:]]
        receipt["status"] = "written"
        save_receipt(path, receipt)
        mismatches = []
        for attempt in range(3):
            rows = rows_from_response(client.run("+record-get", {"record_id_list":list(expected)}))
            actual = {row["_record_id"]:row for row in rows}
            mismatches = [f"{fields['素材编号']}：{name}" for rid,fields in expected.items() for name,value in fields.items()
                          if rid not in actual or not field_matches(name,actual[rid].get(name),value)]
            if not mismatches:
                break
            if attempt < 2:
                time.sleep(.5)
        if mismatches:
            raise WritebackError("已写入但读回未一致：" + "、".join(mismatches[:5]) + "；可重试核验，无需重复新增")
        receipt["status"] = "verified"
        save_receipt(path, receipt)
        return {"ok":True, "record_id":parent_id, "child_record_ids":receipt["child_record_ids"],
                "shot_count":len(plan["children"]), "verified":True}
    except Exception as error:
        if not getattr(error, "uncertain", False):
            receipt.pop("uncertain_codes", None)
        receipt["status"] = "partial" if receipt.get("record_id") else "failed"
        save_receipt(path, receipt)
        raise
