"""Compose editable templates with the exact materials sent to the model."""
import json
import math
import re

GAMEPLAY_POLICY = (
    '【玩法库使用规则：以本次明确勾选为准】玩法库不是自动背景资料或默认剧情。'
    '只有本次明确勾选并列入“本次明确勾选的玩法条目”的内容，才允许写入脚本。'
    '未勾选时，不把玩法库的流程、机制、参数写入口播全文、分镜台词、画面或游戏动作；'
    '画像开关、竞对原稿、旧稿和旧自动抽取结果都不能代替明确勾选。'
    '勾选后必须在口播正文中自然体现本次所列条目的核心内容，而不只写在分析或备注；'
    '只写本次条目，不追加其他玩法库内容。武器、强化、福利、关卡的独立已选事实仍按各自范围使用。'
)


def selected_gameplay_facts(req):
    """Legacy automatically sampled facts never imply user selection."""
    selection = getattr(req, 'selection', None)
    allowed = set(selection.gameplays if selection is not None
                  else getattr(req, 'selected_gameplay_ids', []))
    return [fact for fact in (req.gameplay_facts or []) if fact.get('id') in allowed]


def render(template, values):
    return re.sub(r"\{\{([a-z_0-9]+)\}\}", lambda match: str(values[match[1]]), template)

def build_prompts(req, load_cache, templates):
    w = req.weapon
    rivals = req.rivals
    shots = req.shots
    comps = req.components
    wf = req.welfare or {}
    g_facts = selected_gameplay_facts(req)

    primary_rival = rivals[0] if rivals else {}
    if req.mode == "多篇竞对-多向融合" and req.stitched_ref_script:
        ref_full_script = req.stitched_ref_script
    else:
        ref_full_script = primary_rival.get("raw_script") or primary_rival.get("highlight", "")
    if req.mode == "多篇竞对-多向融合" and req.stitched_ref_script:
        reference_section = ("【跨竞对时序分镜装配链 · 第一阶段仿写蓝本】：\n" + req.stitched_ref_script)
    elif req.mode != '本体事实-自主原创':
        reference_section = ("【参考竞对原稿全文（待洗稿仿写蓝本）】：\n\"\"\"\n" + ref_full_script + "\n\"\"\"\n"
                             + f"主竞对编号：{primary_rival.get('code')} | 风格：{primary_rival.get('style')} | 节奏结构：{primary_rival.get('rhythm')}")
    else:
        reference_section = '【自主原创模式：不引用任何竞对原稿】'

    skills_text = "\n".join([f"- 【{sk.get('name')}】: {sk.get('desc')}" for sk in w.get("skills", [])])
    if req.mode == "多篇竞对-多向融合":
        sec_rivals_text = "\n".join([f"- 融合来源【{r.get('code')}】：风格 {r.get('style')}，节奏 {r.get('rhythm')}；请结合其完整原稿提取可迁移的叙事动作或转折。" for r in rivals[1:]])
    else:
        sec_rivals_text = "\n".join([f"- 辅助表达参考【{r.get('code')}】：风格 {r.get('style')}，节奏 {r.get('rhythm')}。" for r in rivals[1:]])
    shots_text = "\n".join([f"- 参考原镜【{s.get('code')}】(记录ID: {s['id']}): 「{s.get('text')}」 (画面: {s.get('desc')})" for s in shots])
    comps_text = "\n".join([f"- 参考组件【{c.get('code')}】(记录ID: {c['id']}, {c.get('type')}): 「{c.get('text')}」" for c in comps])
    wf_text = f"【{wf.get('name')}】: {wf.get('content')}" if wf else "无"
    gameplay_text = "\n".join([f"- 【{gf.get('title')}】: {gf.get('summary')} ({gf.get('content')})" for gf in g_facts])

    mode_instructions = {'多篇竞对-多向融合': templates['mode_0'], '单篇竞对-定向仿写': templates['mode_1'], '竞对骨架-原创内容': templates['mode_2'], '本体事实-自主原创': templates['mode_3']}

    system_prompt = render(templates['system'], {'mode_instruction': mode_instructions.get(req.mode, mode_instructions['多篇竞对-多向融合'])})

    profile_instruction = render(templates['profile'], {'game_profile': templates["game_profile"]}) if req.enable_game_profile else ""

    user_prompt = render(templates['user'], {'mode': req.mode, 'gameplay': gameplay_text or '- 本次未勾选玩法库，不注入玩法条目，也不将玩法库内容写入脚本。', 'profile': profile_instruction, 'weapon_name': w.get('name'), 'weapon_position': w.get('position'), 'weapon_feature': w.get('feature'), 'skills': skills_text or '- 本次未提供专属强化效果，不从名字推断。', 'reference_section': reference_section, 'fusion_references': f"【辅助融合参考】：\n{sec_rivals_text or '无'}\n{shots_text}\n{comps_text}" if req.mode in ['多篇竞对-多向融合', '竞对骨架-原创内容'] else '', 'scene_references': f'【分镜与组件表达参考（不改变主竞对句序，仅参考画面和口语表达）】：\n{shots_text}\n{comps_text}' if req.mode == '单篇竞对-定向仿写' else '', 'welfare': wf_text, 'emotions': ', '.join(req.emotions), 'custom_instruction': f'- 补充微调要求: {req.custom_instruction}' if req.custom_instruction else ''})
    level = req.level or {}
    if level:
        user_prompt += (f"\n\n【本次已选关卡事实（允许用于洗稿，必须在口播正文中体现）】\n关卡号：{level.get('number')}；名称：{level.get('name')}；"
                        f"特性：{level.get('feature')}；主题与描述：{level.get('description')}。只采用这些资料，不扩写未提供的结果。")
    else:
        user_prompt += "\n\n【本次未勾选关卡库】不注入关卡信息；不得出现具体关卡名称、关卡特性、关卡专属敌人或关卡机制，不得从竞对文案或游戏画像推断。"

    if req.mode == "多篇竞对-多向融合":
        mode_rewrite_guide = (
            "- 严格逐句对仗仿写：分段 1:1 模仿每镜竞对原句的句式结构、语气、口头禅与生活化标点停顿，仅将原句里的外游机制替换为《家园防御战》本次武器/战场/关卡事实。得到每一镜的 stage1_text，并将所有分镜的 stage1_text 拼接成第一稿 stage1_stitched_script！\n"
            if req.rewrite_mode == "sentence_by_sentence"
            else "- 意译结构仿写：逐镜意译仿写，脱离原句字面具体句式，但分段模仿对应分镜的情绪、语境与说话习惯，转述为我方事实，得到每一镜的 stage1_text 并拼接成第一稿 stage1_stitched_script！\n"
        )
        rewrite_instruction = (
            "多来源跨分镜两阶段仿写：不设主稿，禁止沿第一篇或任一篇完整骨架写作。"
            "每篇来源必须向正文贡献至少一个彼此不同的完整分句或完整叙事动作，让每项都能在新稿正文中辨认；只借一个通用词、套话或尾句不能算融合。"
            "再按本次真实经历重新安排钩子、压力、选择、转折和结果。\n"
            "【阶段一：逐镜仿写并拼接第一稿】针对上方抽选的时序分镜链（开头钩子 → 痛点/冲突 → 过程/机制 → 爽点/高潮 → 行动号召）：\n"
            + mode_rewrite_guide
            + "【阶段二：AI 整体优化成稿（极高优先级：严禁对第一稿大刀阔斧修改！）】\n"
            "- 第一稿逐镜仿写已经非常对味，保留了原汁原味的口语、反差、吐槽与人设神采！第二稿严禁推翻重写！\n"
            "- 第二稿的核心职责仅为：【通顺语句、语境一致、逻辑一致】！\n"
            "- 文本保留度必须在 90% 以上：第二稿各镜 shots[index].text 必须忠实继承第一稿对应镜头的 stage1_shots[index].stage1_text，严禁擅自删掉第一稿精彩的开场反问、设问或口头禅；\n"
            "- 仅允许做必要的微调衔接：在镜与镜之间补上自然的承接词（如“不过/结果/这时候/要是/而且”），理顺上下文因果与人称时态，修剪因跨来源拼接导致的生硬语病；\n"
            "- 最终 full_script 就是第二稿各镜微调通顺后的台词按顺序组合而成的全文，以句号为主沉着松弛叙述，修饰掉“直接/瞬间/甚至/疯狂/拉满”等程度副词。\n"
            "冲突的游戏机制、角色和战果不得照搬；没有事实支撑时，迁移反问、递进、犹豫、反转或语气，不虚构事件。"
        )
    else:
        rewrite_instruction = templates["rewrite_sentence" if req.rewrite_mode == "sentence_by_sentence" else "rewrite_structure"]
    if req.mode != "本体事实-自主原创":
        user_prompt += "\n【本次仿写方式】" + rewrite_instruction
    if req.mode == "多篇竞对-多向融合":
        user_prompt += ("\n【多篇融合与两阶段生成规则】上方列出的跨竞对时序分镜链是第一阶段仿写蓝本。"
                        "先分别识别每篇分镜可迁移的语气、口吻、句式或意境，按所选仿写方式（对仗或意译）完成阶段一的逐镜仿写并拼接为第一稿；"
                        "【阶段二优化铁律】：严禁对第一稿进行大刀阔斧的修改或推倒重写！第一稿台词已经对味，第二稿仅对第一稿做微调打磨，核心是【通顺语句、语境一致、逻辑一致】。"
                        "第二稿每镜 text 必须忠实继承该镜 stage1_text（保留度 90% 以上），仅微调衔接词与语病，消除拼接生硬感，形成连贯成稿。"
                        "可把原稿的叙事功能改写到本次所选武器、玩法和关卡事实中，不照搬与本次游戏无关的人物、敌人、数值或结果。"
                        "逐镜ref_text改为可审查的来源贡献标注：写实际来源编号和该来源在本镜提供的叙事作用，可在一镜标多个来源；禁止填竞对原句，禁止原句与改写句一一对应。"
                        "在 stage1_shots 和最终 shots 中，ref_code 填写来源编号，stage1_text 填写第一阶段仿写句，text 填写最终优化台词。"
                        "每项标注必须写成‘来源编号：原稿独特具体动作/因果（转述）→本镜我方对应台词或动作’；只写风格、情绪、开场/结尾、夸赞或满屏印象不算贡献。"
                        "每个已选来源编号都必须至少在一镜的ref_text中出现，并在正文中贡献不同的内容或叙事作用；不得只在标题或摘要中点名。"
                        "来源贡献必须是可辨认的具体因果、选择或过程，不得只标注“帅气/有趣/高潮/满屏/收尾/爽感”等通用情绪、画面印象或口吻。"
                        "一篇来源至少贡献一个有独特原因和推进作用的叙事节拍，而非仅贡献一句开场、感叹词或结尾套话；各来源独特点按分镜链依次推进。"
                        "多篇模式禁止在full_script中原样复用任一来源连续5个汉字的原句片段（必要专名除外），也禁止把一篇来源的连续句序整体搬过来。"
                        "不得为体现来源贡献而移植其游戏数值、机制或结果；台词中的数字、倍数和战果只能来自本次我方素材，证据没有具体数值就删掉数字。"
                        "分镜参考只用于镜头表达和时间连续性：只沿同一来源视频内的连续镜头，不把不同视频的镜头假装成同一段实录。"
                        "分镜来源锚点不代表脚本内容主次；所有已选竞对原稿都必须参与内容融合。")
    char_budget = math.floor(req.max_duration * 5.6)
    duration_instruction = render(templates['duration'], {'max_duration': req.max_duration, 'char_budget': char_budget})
    user_prompt += duration_instruction
    candidates = req.weapon_candidates or [w]
    user_prompt += render(templates["weapons"], {"weapon_candidates": json.dumps(candidates, ensure_ascii=False)})
    rhythm_options = sorted({value for row in load_cache("main") for value in (row.get("二创节奏结构") or [])})
    if req.existing_script and req.mode == '本体事实-自主原创' and (shots_text or comps_text):
        user_prompt += '\n【本次替换的分镜与组件参考】\n' + shots_text + '\n' + comps_text
    user_prompt += render(templates["record"], {'rhythm_options': json.dumps(rhythm_options, ensure_ascii=False), 'shot_ids': json.dumps([s['id'] for s in shots], ensure_ascii=False), 'component_ids': json.dumps([c['id'] for c in comps], ensure_ascii=False)})
    if req.existing_script:
        revision = render(templates["revision"], {
            "operation": req.operation, "scene_policy": "保留输入分镜数量和顺序，并逐镜优化。" if req.scene_policy == "preserve" and req.existing_shots else "可按全文重新组织分镜。",
            "existing_script": req.existing_script, "existing_shots": json.dumps(req.existing_shots, ensure_ascii=False),
            "existing_reference": req.existing_reference, "revision_instruction": req.revision_instruction})
        system_prompt += "\n【当前任务：已有文案改写，以下任务要求优先于一般仿写方式】\n" + revision
        user_prompt += "\n" + revision
    if getattr(req, 'creative_brief', None):
        brief_policy = ('【当前任务：一源多创】按本篇独立创意任务组织口播，允许调整来源叙事顺序；'
                        '不执行单篇逐句对照，不把角度标签当作正文。必须体现本篇核心矛盾和决策理由，'
                        '与其他任务的区别来自叙事内容和推进，不只换词。原稿只提供叙事表达，'
                        '不得把来源机制、数值或结果移植成我方事实；我方事实仍以本次明确范围为准。'
                        'ref_text注明来源编号、借用节拍到本镜的转化，不虚构镜头实录。')
        system_prompt += '\n' + brief_policy
        user_prompt += '\n' + brief_policy + '\n本篇创意任务：\n' + json.dumps(req.creative_brief, ensure_ascii=False)
    # Append after editable templates and revision instructions so older local
    # overrides cannot quietly restore automatic gameplay exposition.
    system_prompt += '\n\n' + GAMEPLAY_POLICY
    user_prompt += '\n\n' + GAMEPLAY_POLICY
    if g_facts:
        user_prompt += '\n【本次明确勾选的玩法条目：必须在口播正文中体现】\n' + gameplay_text
    else:
        user_prompt += '\n【本次未勾选玩法库】没有任何获准写入的玩法库条目；旧稿、参考或画像中出现的玩法流程也不构成授权。'
    return dict(system=system_prompt, user=user_prompt, candidates=candidates, char_budget=char_budget, duration_instruction=duration_instruction)
