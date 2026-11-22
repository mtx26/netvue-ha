from homeassistant.helpers.condition import Condition, ConditionConfig

from .zone_automation import CONDITION_SCHEMA, ZoneSelection


class ObjectPresentCondition(Condition):
    def __init__(self, hass, config: ConditionConfig) -> None:
        super().__init__(hass, config)
        assert config.options is not None
        self._selection = ZoneSelection.from_options(config.options)

    @classmethod
    async def async_validate_config(cls, hass, config):
        return ZoneSelection.validate_config(hass, config, CONDITION_SCHEMA)

    def _async_check(self, **kwargs):
        return self._selection.is_present(self._hass)



async def async_get_conditions(hass):
    return {"object_present": ObjectPresentCondition}
