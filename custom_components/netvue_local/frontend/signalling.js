// Home Assistant WebRTC signalling (camera/webrtc/*), the same commands as HA's native player.

// WebSocket errors that a new attempt cannot fix.
const FATAL_CODES = new Set([
  "not_found",
  "unknown_command",
  "unauthorized",
  "webrtc_offer_failed",
  "webrtc_get_client_config_failed",
  "netvue_webrtc_busy",
]);

export function describeError(err) {
  const code = err && typeof err.code === "string" ? err.code : "signalling";
  const message = err && typeof err.message === "string" ? err.message : "";
  return { code, message, recoverable: !FATAL_CODES.has(code) };
}

export function createSignalling(getHass) {
  return {
    getClientConfig: (entityId) =>
      getHass().callWS({ type: "camera/webrtc/get_client_config", entity_id: entityId }),
    // Resolves with the unsubscribe function once HA accepted the subscription.
    offer: (entityId, offer, callback) =>
      getHass().connection.subscribeMessage(callback, {
        type: "camera/webrtc/offer",
        entity_id: entityId,
        offer,
      }),
    addCandidate: (entityId, sessionId, candidate) =>
      getHass().callWS({
        type: "camera/webrtc/candidate",
        entity_id: entityId,
        session_id: sessionId,
        candidate,
      }),
  };
}
