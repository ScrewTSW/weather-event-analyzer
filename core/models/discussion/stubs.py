"""Placeholder discussion models for sources without full API integration yet.

Each will get its own file with proper parsing once the API integration is built.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.models.discussion.base import Discussion


@dataclass
class JMADiscussion(Discussion):
    pass


@dataclass
class IMDDiscussion(Discussion):
    pass


@dataclass
class INMETDiscussion(Discussion):
    pass


@dataclass
class TMDDiscussion(Discussion):
    pass


@dataclass
class SAWSDiscussion(Discussion):
    pass


@dataclass
class WMODiscussion(Discussion):
    pass
