import time

from homeassistant.core import callback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.trigger import Trigger, TriggerConfig

from .const import EVENT_DETECTION
from .zone_automation import CONF_DURATION, TRIGGER_SCHEMA, ZoneSelection
from .zones import ZONE_FRESHNESS_SECONDS


class ObjectEnteredTrigger(Trigger):
    def __init__(self, hass, config: TriggerConfig) -> None:
        super().__init__(hass, config)
        assert config.options is not None
        self._selection = ZoneSelection.from_options(config.options)
        self._duration = int(config.options[CONF_DURATION])

    @classmethod
    async def async_validate_config(cls, hass, config):
        return ZoneSelection.validate_config(hass, config, TRIGGER_SCHEMA)

    async def async_attach_runner(self, run_action, did_not_trigger=None):
        armed_at = time.time()
        pending = {}

        @callback
        def handle_entry(event):
            data = event.data
            if data.get("event") != "zone_entered":
                return

            timestamp = data.get("timestamp")
            if (type(timestamp) not in (int, float) or timestamp < armed_at
                    or not 0 <= time.time() - timestamp <= 2 * ZONE_FRESHNESS_SECONDS):
                return
            match = self._selection.match_event(self._hass, data)
            if match is None:
                return
            zone_id, category = match
            def fire():
                run_action(
                    {"event": event, "zone_id": zone_id, "category": category},
                    f"{category} entered {self._selection.entity_id}", event.context,
                )

            if not self._duration:
                fire()
                return
            presence_id = data.get("presence_id")
            if not isinstance(presence_id, str) or presence_id in pending:
                return

            @callback
            def check_presence(_now):
                pending.pop(presence_id, None)
                if self._selection.has_presence(self._hass, zone_id, presence_id):
                    fire()

            delay = max(0, timestamp + self._duration - time.time())
            pending[presence_id] = async_call_later(self._hass, delay, check_presence)

        unsubscribe = self._hass.bus.async_listen(EVENT_DETECTION, handle_entry)

        @callback
        def cancel():
            unsubscribe()
            for cancel_timer in pending.values():
                cancel_timer()
            pending.clear()

        return cancel


async def async_get_triggers(hass):
    return {"object_entered": ObjectEnteredTrigger}
