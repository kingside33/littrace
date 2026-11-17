"""kbapp.core.evidence_rules：由职责边界组织的知识库能力。"""
from __future__ import annotations
import re

def fully_supported(review):
    if review.get('supported') is not True:
        return False
    if review.get('method_relation') is False or review.get('unsupported_details'):
        return False
    unresolved = '不能(?:据此|独立)?(?:核实|支持|确认)|未(?:在.{0,40})?(?:证据|原文).{0,24}(?:出现|支持|确认)|(?:缺少|缺乏|尚缺|不足以).{0,12}(?:证据|支持|确认|核实)|(?:部分|仅核心).{0,6}支持|未(?:出现|展示|证明).{0,45}(?:条件|参数|细节|方法层面)|仅(?:平台|动机|启发).{0,12}(?:关系|层面|成立)|(?:研究动机层面|平台来源/使用层面)'
    return not re.search(unresolved, review.get('reason') or '')
