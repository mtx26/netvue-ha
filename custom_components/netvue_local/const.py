DOMAIN = "netvue_local"
CONF_HOST = "host"
CONF_STREAM_URL = "stream_url"
CONF_STREAM_NAME = "stream_name"
DEFAULT_NAME = "Netvue Local"
UPDATE_INTERVAL = 30

CONF_AI_ENABLED = "ai_enabled"
CONF_AI_CONFIDENCE = "ai_confidence"
DEFAULT_AI_CONFIDENCE = 0.38
CONF_AI_FPS = "ai_fps"
CONF_AI_CONFIRMATION_FRAMES = "ai_confirmation_frames"
CONF_AI_ZONES = "ai_zones"
CONF_AI_RULES = "ai_rules"
CONF_NOTIFY_TARGETS = "notification_targets"
CONF_NOTIFY_COOLDOWN = "notification_cooldown"

CONF_OVERLAY_LIVE = "overlay_live"
CONF_OVERLAY_REPLAY = "overlay_replay"
CONF_OVERLAY_EXPORT = "overlay_export"
OVERLAY_OPTIONS = (CONF_OVERLAY_LIVE, CONF_OVERLAY_REPLAY, CONF_OVERLAY_EXPORT)
OVERLAY_DEFAULTS = {
    CONF_OVERLAY_LIVE: True,
    CONF_OVERLAY_REPLAY: True,
    CONF_OVERLAY_EXPORT: False,
}
CONF_RETENTION_DAYS = "retention_days"
CONF_STORAGE_LIMIT_GB = "storage_limit_gb"
CONF_TURN_ENABLED = "turn_enabled"
CONF_TURN_KEY_ID = "turn_key_id"
CONF_TURN_API_TOKEN = "turn_api_token"
CONF_TURN_CLEAR_TOKEN = "turn_clear_token"
GLOBAL_SETTINGS_DEFAULTS = {
    CONF_RETENTION_DAYS: 7,
    CONF_STORAGE_LIMIT_GB: 0,
    CONF_TURN_ENABLED: False,
    CONF_TURN_KEY_ID: "",
    CONF_TURN_API_TOKEN: "",
}
SETTINGS_STORAGE_KEY = f"{DOMAIN}.settings"
