"""Channel recipe registry + single-authority CHANNELS frozenset.

CHANNELS is the *only* place new browser-binding channels are added.
Every entry point (CLI argparse, webui routes, AuthExpiredError ctor,
mark_bound / mark_expired) imports from here and validates membership
before constructing paths or argv — defense in depth against
``channel=../traversal`` injection.

Unit 2 will add ``EVENTS`` (frozenset of RECON event names shared by
CLI driver + webui reader) and ``RECIPES`` (dict[name -> ChannelRecipe])
into this module. Unit 1 only ships CHANNELS.
"""

from __future__ import annotations


CHANNELS: frozenset[str] = frozenset({"velog", "medium", "blogger"})
"""The closed set of supported binding channels. Adding a new channel
requires updating this frozenset plus shipping its recipe in
``channels/<name>.py`` (Unit 2)."""


__all__ = ["CHANNELS"]
