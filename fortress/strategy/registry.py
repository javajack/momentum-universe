"""
Strategy registry for FORTRESS MOMENTUM.

Central registry for all available strategies. Strategies register themselves
and can be retrieved by name for use in backtest/CLI.
"""

from typing import TYPE_CHECKING, Dict, List, Tuple, Type

from .base import BaseStrategy

if TYPE_CHECKING:
    from ..config import Config


class StrategyRegistry:
    """
    Central registry for all available strategies.

    Strategies have a canonical `name` plus optional `aliases` (older names
    or descriptive synonyms). Both forms resolve to the same class — config
    files using either keep working.

    Usage:
        # Get strategy by canonical name OR alias
        strategy = StrategyRegistry.get("vanguard", config)
        strategy = StrategyRegistry.get("emerging_momentum", config)  # same thing

        # List available strategies (canonical names only)
        for name, desc in StrategyRegistry.list_strategies():
            print(f"{name}: {desc}")
    """

    _strategies: Dict[str, Type[BaseStrategy]] = {}
    _aliases: Dict[str, str] = {}  # alias → canonical name

    @classmethod
    def register(cls, strategy_class: Type[BaseStrategy]) -> None:
        """Register a strategy class. Picks up `aliases` attr if present."""
        instance = strategy_class()
        canonical = instance.name
        cls._strategies[canonical] = strategy_class
        aliases = getattr(strategy_class, "aliases", ()) or ()
        for alias in aliases:
            if alias == canonical:
                continue
            cls._aliases[alias] = canonical

    @classmethod
    def _resolve(cls, name: str) -> str:
        """Map an alias to its canonical name (no-op if already canonical)."""
        if name in cls._strategies:
            return name
        if name in cls._aliases:
            return cls._aliases[name]
        return name  # leave unresolved; get/is_registered handle the miss

    @classmethod
    def get(cls, name: str, config: "Config" = None) -> BaseStrategy:
        """Get strategy instance by canonical name OR alias."""
        canonical = cls._resolve(name)
        if canonical not in cls._strategies:
            available = ", ".join(sorted(cls._strategies.keys()))
            if cls._aliases:
                available += "  (aliases: " + ", ".join(
                    f"{a}→{c}" for a, c in sorted(cls._aliases.items())
                ) + ")"
            raise ValueError(f"Unknown strategy: {name!r}. Available: {available}")
        return cls._strategies[canonical](config)

    @classmethod
    def list_strategies(cls) -> List[Tuple[str, str]]:
        """List all registered canonical strategies (aliases not shown)."""
        result = []
        for name in sorted(cls._strategies.keys()):
            instance = cls._strategies[name]()
            result.append((name, instance.description))
        return result

    @classmethod
    def is_registered(cls, name: str) -> bool:
        """True if `name` matches a canonical name OR a registered alias."""
        return cls._resolve(name) in cls._strategies

    @classmethod
    def get_names(cls) -> List[str]:
        """Canonical registered names only (no aliases)."""
        return list(cls._strategies.keys())
