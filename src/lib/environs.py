import os
import typing

import structlog
import environs


logger = structlog.getLogger(__name__)


class Env(environs.Env):
    @staticmethod
    def read_env(
        path: typing.Optional[str] = None,
        recurse: bool = True,
        verbose: bool = False,
        override: bool = False,
    ) -> None:
        env_files = ("settings.cfg", ".env")
        for env_file in env_files:
            if os.path.isfile(env_file):
                path = env_file
                logger.info(f"Loading settings file: {path}")
                return environs.Env.read_env(path, recurse, verbose, override)

        logger.warning("Settings file not found! Using the default values")
        return environs.Env.read_env(path, recurse, verbose, override)
