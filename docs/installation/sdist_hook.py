"""Keep repository ignore lists out of source distributions."""

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class SourceDistributionHook(BuildHookInterface):
    def initialize(self, version, build_data):
        # Hatch adds these files independently of the configured runtime allowlist.
        included = build_data["force_include"]
        for source, destination in tuple(included.items()):
            if destination in {".gitignore", ".hgignore"}:
                del included[source]
