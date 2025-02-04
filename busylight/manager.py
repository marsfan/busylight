""" a Manager for working with multiple USBLights
"""

import asyncio

from typing import Dict, List, Optional, Union, Tuple
from loguru import logger

from .color import ColorTuple
from .effects import Effects
from .lights import LightUnavailable, NoLightsFound, USBLight, Speed


class LightManager:
    def __init__(self, greedy: bool = True, lightclass: type = None):
        """
        :greedy: bool
        :lightclass: USBLight or subclass

        If `greedy` is True, the default, then calls to the update
        method will look for lights that have been plugged in since
        the last update.

        If the caller supplies a `lightclass`, which is expected to
        be USBLight or a subclass, the light manager will only
        manage lights returned by `lightclass.all_lights()`. If the
        user does not supply a class, the default is `USBLight`.
        """

        self.greedy = greedy

        if lightclass is None:
            self._lightclass = USBLight
        else:
            if not issubclass(lightclass, USBLight):
                raise TypeError("Not a USBLight subclass")
            self._lightclass = lightclass

    def __repr__(self) -> str:
        return "".join(
            [
                f"{self.__class__.__name__}(",
                f"greedy={self.greedy}, ",
                f"lightclass={self.lightclass!r})",
            ]
        )

    def __str__(self) -> str:
        return "\n".join(
            [f"{n:3d} {light.name}" for n, light in enumerate(self.lights)]
        )

    def __len__(self) -> int:
        return len(self.lights)

    def __del__(self) -> None:
        logger.debug(f"releasing resources for LightManager {id(self)}")
        self.release()

    @property
    def lightclass(self) -> USBLight:
        """USBLight subclass used to locate lights, read-only."""
        return getattr(self, "_lightclass", USBLight)

    @property
    def lights(self) -> List[USBLight]:
        """List of managed lights."""
        try:
            return self._lights
        except AttributeError:
            pass
        self._lights = list(self.lightclass.all_lights(reset=False))
        return self._lights

    def selected_lights(self, indices: List[int] = None) -> List[USBLight]:
        """Return a list of USBLights matching the list of `indices`.

        If `indices` is empty, all managed lights are returned.

        If there are no lights with matching indices, an empty list is returned.

        :indices: List[int]
        :return: List[USBLight]

        Raises:
        - NoLightsFound
        """
        if not indices:
            indices = range(0, len(self.lights))

        selected_lights = []
        for index in indices:
            try:
                selected_lights.append(self.lights[index])
            except IndexError as error:
                logger.debug(f"index:{index} {error}")

        if selected_lights:
            return selected_lights

        raise NoLightsFound(indices)

    def update(self) -> Tuple[int, int, int]:
        """Updates managed lights list.

        This method looks for newly plugged in lights if the greedy
        property is True. It then surveys known lights, building a
        count of plugged in lights and unplugged lights. New lights
        are appended to the end of the `lights` property in order to
        keep the light index order stable over the lifetime of the
        manager. The return value is an integer three-tuple which
        records the number of new lights found, the number of
        previously known lights that are still active and the number
        of previously known lights that are now inactive.

        :return: Tuple[# new lights, # active lights, # inactive lights]
        """
        if self.greedy:
            new_lights = self.lightclass.all_lights()
            logger.debug(f"{len(new_lights)} new {new_lights}")

        active_lights = [light for light in self.lights if light.is_pluggedin]

        inactive_lights = [light for light in self.lights if light.is_unplugged]

        self._lights += new_lights

        return len(new_lights), len(active_lights), len(inactive_lights)

    def release(self) -> None:
        """Release managed lights."""

        # EJO Is this better than calling self._lights.clear()?
        #     This implementation forces the finalization of each
        #     light and adds some logging. I'm not sure it's
        #     worth the added complexity.
        try:
            while self._lights:
                light = self._lights.pop()
                del light
        except (IndexError, AttributeError) as error:
            logger.debug(f"during release {error}")

        try:
            del self._lights
        except AttributeError as error:
            logger.debug(f"during release, failed to del _lights property {error}")

    def on(
        self,
        color: ColorTuple,
        light_ids: List[int] = None,
        timeout: float = None,
    ) -> None:
        """Turn on all the lights whose indices are in the `lights` list.

        :color: ColorTuple
        :lights: List[int]
        :timeout: float seconds

        Raises:
        - NoLightsFound
        """

        asyncio.run(self.on_supervisor(color, self.selected_lights(light_ids), timeout))

    async def on_supervisor(
        self,
        color: ColorTuple,
        lights: List[USBLight],
        timeout: float = None,
        wait: bool = True,
    ) -> None:
        """"""
        awaitables = []
        for light in lights:
            light.on(color)
            awaitables.extend(light.tasks.values())

        if awaitables and wait:
            await asyncio.wait(awaitables, timeout=timeout)

    def apply_effect(
        self,
        effect: Effects,
        light_ids: List[int] = None,
        timeout: float = None,
    ) -> None:
        """Applies the given `effect` to all of the lights whose indices are
        in the `lights` list.

        :effect: FrameGenerator
        :lights: List[int]
        :timeout: float seconds

        Raises:
        - NoLightsFound
        """
        asyncio.run(
            self.effect_supervisor(effect, self.selected_lights(light_ids), timeout)
        )

    async def effect_supervisor(
        self,
        effect: Effects,
        lights: List[USBLight],
        timeout: float = None,
        wait: bool = True,
    ) -> None:
        """Builds a list of awaitable coroutines to perform the given `effect`
        on each of the `lights` and awaits the exit of the coroutines (which
        typically do not exit). If a timeout in seconds is specified, the
        effect will stop at the end of the period.

        :effect:
        :lights: List[USBLight]
        :timeout: float seconds
        """

        awaitables = []
        for light in lights:
            light.cancel_tasks()
            light.add_task(effect.name, effect)
            awaitables.extend(light.tasks.values())

        if awaitables and wait:
            await asyncio.wait(awaitables, timeout=timeout)

    def off(self, lights: List[int] = None) -> None:
        """Turn off all the lights whose indices are in the `lights` list.

        :lights: List[int]

        raises:
        - NoLightsFound
        """

        for light in self.selected_lights(lights):
            light.off()
