#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""game-qa-kit：跨游戏可复用的 QA 工具核心（引擎无关核心 + 每游戏 profile）。

沉淀自真实汉化巡检的工作流：人快速手玩 + 盯屏自动留证 + 模型异步复核；
输入/改档为辅助且默认低优先（模型驱动比人手玩慢，且每引擎输入不同）。
"""
from __future__ import annotations

from .profile import GameProfile, load_profile, active_profile, list_profiles

__all__ = ["GameProfile", "load_profile", "active_profile", "list_profiles"]
__version__ = "0.1.0"
