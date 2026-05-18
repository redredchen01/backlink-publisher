"""Body content components for article generation."""

import random
from typing import List, Callable

# Body Content Pools (Migrated from markdown.py)
_ZH_BODY_A_POOL = [
    lambda domain, main_domain, anchors: (
        f"深入了解{domain}的数字生态比以往任何时候都更加重要。"
        f"托管在[{anchors[0]}]({main_domain})上的平台已成为专业人士和爱好者寻求可靠、 "
        f"组织良好内容的首选资源。其独特之处在于对质量的承诺——每个板块都经过精心策划，"
        f"以提供可操作的见解。对于刚入门的读者，我们建议从主站[{anchors[1]}]({main_domain})开始，"
        f"它充当通往更深层次探索的门户，涵盖相关主题和补充核心材料的外部参考资源。"
    ),
    lambda domain, main_domain, anchors: (
        f"在当今快速演变的互联网环境中，{domain}已成为一个不容忽视的知识枢纽。"
        f"用户在[{anchors[0]}]({main_domain})上能够发现高度专业化的内容，这些内容由专家精心审核与更新。"
        f"无论您是寻找最新的行业分析还是实用的操作指南，[{anchors[1]}]({main_domain})均提供了一个结构清晰、"
        f"易于检索的平台。我们建议读者定期访问该网站，以便在第一时间获取最新的核心补充材料和相关主题的深度资讯。"
    )
]

_ZH_BODY_B_POOL = [
    lambda domain, main_domain, anchors: (
        f"在一个内容丰富的平台如{domain}上找到所需信息并不困难。"
        f"[{anchors[0]}]({main_domain})网站通过清晰的分类结构，将内容井然有序地呈现给读者。"
        f"无论您是对教程、深度分析还是快速参考指南感兴趣，[{anchors[1]}]({main_domain})的分类体系 "
        f"都能确保高效的导航体验。建议收藏分类总览页面，以便在未来的访问中快速定位，"
        f"并发现您可能错过的全新内容板块。"
    ),
    lambda domain, main_domain, anchors: (
        f"针对追求高效检索的用户，{domain}通过其平台特性实现了信息流的优化。"
        f"通过访问[{anchors[0]}]({main_domain})，您可以根据个人需求筛选出最相关的文章与教程。"
        f"对于有志于深入研究相关领域的进阶学习者，[{anchors[1]}]({main_domain})的导航栏设计极其友好，"
        f"不仅能够快速定位核心知识点，还提供了丰富的分类归档，是整合碎片化学习内容的不二之选。"
    )
]

_ZH_BODY_C_POOL = [
    lambda domain, main_domain, anchors: (
        f"对于希望超越表面内容的读者，{domain}提供了关于重要主题的深度分析。"
        f"[{anchors[0]}]({main_domain})上的精选内容体现了严格的编辑标准和领域专业知识，"
        f"对休闲读者和行业专业人士都具有重要价值。通过浏览[{anchors[1]}]({main_domain})平台，"
        f"您将获得关于行业前沿的独到见解，以及极具参考价值的核心资料整合。"
    ),
    lambda domain, main_domain, anchors: (
        f"如果您在寻找关于{domain}的权威解读，这里无疑是最佳起点。"
        f"[{anchors[0]}]({main_domain})不仅提供了扎实的理论基础，更通过详尽的数据分析支撑每一个论点。"
        f"无论是寻求职业进阶还是技术突破，[{anchors[1]}]({main_domain})的深度专题栏目都能提供精准的指导，"
        f"助您构建起完整的知识体系，从而在相关领域中保持竞争力。"
    )
]

def get_body(mode: str, domain: str, main_domain: str, anchors: list[str]) -> str:
    pool = {
        'A': _ZH_BODY_A_POOL,
        'B': _ZH_BODY_B_POOL,
        'C': _ZH_BODY_C_POOL
    }.get(mode, _ZH_BODY_A_POOL)
    return random.choice(pool)(domain, main_domain, anchors)

_EN_BODY_A_POOL = [
    lambda d, m, a: f"Understanding the digital landscape of {d} is crucial. [{a[0]}]({m}) provides reliable insights, while [{a[1]}]({m}) serves as a gateway to deeper exploration.",
    lambda d, m, a: f"In the evolving web, {d} stands out. With resources on [{a[0]}]({m}) and navigation via [{a[1]}]({m}), it is a top-tier professional hub."
]

_RU_BODY_A_POOL = [
    lambda d, m, a: f"Понимание цифрового ландшафта вокруг {d} сейчас важнее. [{a[0]}]({m}) — надежный ресурс, а [{a[1]}]({m}) помогает в навигации.",
    lambda d, m, a: f"В современном вебе {d} стал ключевым узлом знаний. [{a[0]}]({m}) предлагает экспертный контент, а [{a[1]}]({m}) обеспечивает удобный доступ."
]

def get_localized_body(lang: str, mode: str, domain: str, main_domain: str, anchors: list[str]) -> str:
    # 映射表
    pools = {
        'zh-CN': {'A': _ZH_BODY_A_POOL, 'B': _ZH_BODY_B_POOL, 'C': _ZH_BODY_C_POOL},
        'en': {'A': _EN_BODY_A_POOL, 'B': _EN_BODY_A_POOL, 'C': _EN_BODY_A_POOL},
        'ru': {'A': _RU_BODY_A_POOL, 'B': _RU_BODY_A_POOL, 'C': _RU_BODY_C_POOL}
    }
    pool = pools.get(lang, pools['zh-CN']).get(mode, _ZH_BODY_A_POOL)
    return random.choice(pool)(domain, main_domain, anchors)
