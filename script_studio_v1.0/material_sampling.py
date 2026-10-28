"""Uniform draws without replacement until the current eligible pool is used up."""
import random


LABELS = {'weapons': '武器', 'rivals': '竞对', 'shots': '分镜', 'comps': '组件',
          'welfares': '福利', 'gameplays': '玩法', 'levels': '关卡', 'skills': '强化'}


class RandomCycleSampler:
    def __init__(self, counts=None):
        # Counts belong to this browser/batch, never to historical library usage.
        self.counts = counts if counts is not None else {}
        self.reused = set()

    def record(self, rows, key):
        counts = self.counts.setdefault(key, {})
        for row in rows:
            identity = row['_record_id']
            if counts.get(identity, 0) > 0:
                self.reused.add(key)
            counts[identity] = counts.get(identity, 0) + 1

    def take(self, rows, size, key):
        # Deduplicate cache IDs as well as removing each pick from this draw.
        remaining = list({row['_record_id']: row for row in rows}.values())
        chosen = []
        counts = self.counts.setdefault(key, {})
        for _ in range(min(size, len(remaining))):
            least = min(counts.get(row['_record_id'], 0) for row in remaining)
            eligible = [row for row in remaining if counts.get(row['_record_id'], 0) == least]
            row = random.choice(eligible)
            self.record([row], key)
            chosen.append(row)
            remaining.remove(row)
        return chosen

    def metadata(self):
        labels = [LABELS[key] for key in LABELS if key in self.reused]
        return {'strategy': 'uniform', 'counts': self.counts,
                'reused': labels,
                'note': ('已进入下一轮随机复用：' + '、'.join(labels)) if labels
                        else '纯随机：本轮优先抽未抽过的素材，不按历史使用次数加权。'}
